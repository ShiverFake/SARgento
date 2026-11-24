"""
Controller: pipeline de processamento

Para cada barragem:
  buscar cenas -> carregar cubo -> (para cada data) processar -> salvar no SQLite
"""
import logging

import numpy as np
import pandas as pd

import config
from app.models import (crista, database, geo, processamento, qualidade, sentinel1,
                        sentinel2, superficie)
from app.models.barragem import Barragem

log = logging.getLogger(__name__)


def _preparar_geometria(barragem: Barragem):
    """Devolve (epsg, poligono_latlon, bbox) da barragem."""
    epsg = geo.epsg_utm(barragem.lon, barragem.lat)
    poligono = barragem.poligono
    if poligono is None:
        log.warning("'%s' não tem polígono em poligonos.geojson; usando círculo "
                    "de 500 m em volta do ponto (resultado só indicativo).", barragem.nome)
        poligono = geo.circulo_em_volta(barragem.lon, barragem.lat, 500, epsg)
    bbox = geo.bbox_latlon(poligono, epsg, config.BUFFER_M)
    return epsg, poligono, bbox


def _grade(cubo, poligono, epsg):
    """Informações da grade de pixels do cubo carregado."""
    gbox = cubo.odc.geobox              # "caixa geográfica" da grade
    formato = tuple(gbox.shape)         # (linhas, colunas)
    affine = gbox.affine
    dentro = geo.rasterizar(geo.para_utm(poligono, epsg), formato, affine)
    area_pixel_m2 = abs(affine.a * affine.e)  # largura x altura do pixel
    limites = geo.limites_latlon(affine, formato, epsg)
    return dentro, area_pixel_m2, limites, affine


def _datas(cubo):
    return [pd.Timestamp(t).strftime("%Y-%m-%d") for t in cubo.time.values]


def processar_s1(barragem: Barragem, inicio: str, fim: str, reprocessar=False) -> int:
    """Processa todas as passagens Sentinel-1 no período. Retorna quantas salvou."""
    epsg, poligono, bbox = _preparar_geometria(barragem)

    # Mesma órbita sempre. Ordem de prioridade:
    #   1. coluna "orbita" do sigbm.csv (ex.: "descending/126")
    #   2. ORBITA_RELATIVA / DIRECAO_ORBITA do config.py
    #   3. a órbita já registrada no banco para esta barragem
    # Sem isso, ao estender a série por poucos meses o programa poderia
    # escolher outra órbita e misturar geometrias de visada diferentes.
    direcao, numero = config.DIRECAO_ORBITA, config.ORBITA_RELATIVA
    if barragem.orbita:
        direcao, numero = barragem.orbita.split("/")
        numero = int(numero)
        log.info("Usando a órbita definida no sigbm.csv: %s", barragem.orbita)
    elif direcao is None and numero is None:
        salva = database.orbita_predominante(barragem.slug)
        if salva:
            direcao, numero = salva.split("/")
            numero = int(numero)
            log.info("Usando a órbita já registrada para esta barragem: %s", salva)

    itens, orbita = sentinel1.buscar_cenas(bbox, inicio, fim, orbita_relativa=numero, direcao=direcao)
    if not itens:
        log.warning("Nenhuma cena Sentinel-1 para '%s' em %s a %s", barragem.nome, inicio, fim)
        return 0

    cubo = sentinel1.carregar_cubo(itens, bbox, epsg)
    dentro, area_px, limites, affine = _grade(cubo, poligono, epsg)
    salvas = 0

    for i, data in enumerate(_datas(cubo)):
        if not reprocessar and database.existe(barragem.slug, data, "S1"):
            log.info("  %s já processada — pulando", data)
            continue

        # .compute() é o momento em que os dados são realmente baixados
        fatia = cubo.isel(time=i).compute()
        vv = fatia["vv"].values.astype("float32")
        vh = fatia["vh"].values.astype("float32")

        resultado, agua, vv_db = processamento.processar_cena_s1(vv, vh, dentro, area_px)
        if resultado["cobertura_valida"] < config.COBERTURA_MINIMA:
            log.info("  %s descartada: só %.0f%% do polígono com dados",
                     data, 100 * resultado["cobertura_valida"])
            continue

        nome_png = f"{barragem.slug}_{data}_S1.png"
        processamento.salvar_png_mascara(agua, config.PASTA_MASCARAS / nome_png, dentro)
        qualidade.salvar_raster(barragem.slug, data, vv_db, agua, dentro, affine, epsg)

        database.salvar_observacao({
            "barragem": barragem.slug, "data": data, "sensor": "S1",
            "orbita": orbita, "mascara_png": nome_png, "limites": limites,
            **resultado,
        })
        salvas += 1
        log.info("  %s  lago=%.2f ha  praia=%.2f ha  limiar=%.1f dB",
                 data, resultado["area_lago_ha"], resultado["area_praia_ha"], resultado["limiar"])
    return salvas


def processar_s2(barragem: Barragem, inicio: str, fim: str, reprocessar=False) -> int:
    """Validação opcional com Sentinel-2 (MNDWI)."""
    epsg, poligono, bbox = _preparar_geometria(barragem)
    itens = sentinel2.buscar_cenas(bbox, inicio, fim)
    if not itens:
        return 0

    offsets = sentinel2.offsets_por_data(itens)
    cubo = sentinel2.carregar_cubo(itens, bbox, epsg)
    dentro, area_px, limites, affine = _grade(cubo, poligono, epsg)
    faixa = barragem.faixa_otsu_mndwi
    if barragem.mndwi_limiar is not None:
        log.info("S2: limiar MNDWI fixo desta barragem = %.2f", barragem.mndwi_limiar)
    elif faixa:
        log.info("S2: Otsu no MNDWI limitado a [%.2f, %.2f]", *faixa)
    salvas = 0

    for i, data in enumerate(_datas(cubo)):
        # Pula só se a linha existe E o raster S2 está salvo (dados antigos
        # sem raster são reprocessados automaticamente).
        if (not reprocessar and qualidade.caminho_raster(barragem.slug, data, "S2").exists()
                and database.existe(barragem.slug, data, "S2", exigir_coluna="nuvem_poligono_pct")):
            continue
        fatia = cubo.isel(time=i).compute()
        resultado, agua, cobertura_dados, (mndwi, valido) = sentinel2.processar_cena_s2(
            fatia["B03"].values, fatia["B11"].values, fatia["SCL"].values,
            offsets.get(data, 0), dentro, area_px, faixa_otsu=faixa,
            limiar_fixo=barragem.mndwi_limiar)

        # Só descarta se a CENA não cobre o polígono (borda da passagem).
        # Datas com nuvem são guardadas e marcadas como "não confiáveis"
        # na validação, para você ver que existiram.
        if cobertura_dados < config.COBERTURA_MINIMA:
            log.info("  S2 %s descartada: polígono fora da cena", data)
            continue

        nome_png = f"{barragem.slug}_{data}_S2.png"
        processamento.salvar_png_mascara(agua, config.PASTA_MASCARAS / nome_png, dentro)
        superficie.salvar_raster_s2(barragem.slug, data, mndwi, valido, dentro, affine,
                                    agua_scl=fatia["SCL"].values == 6)
        database.salvar_observacao({
            "barragem": barragem.slug, "data": data, "sensor": "S2",
            "mascara_png": nome_png, "limites": limites, **resultado,
        })
        salvas += 1
        log.info("  S2 %s  lago=%.2f ha  nuvem no polígono=%.1f%%  limiar MNDWI=%.2f",
                 data, resultado["area_lago_ha"], resultado["nuvem_poligono_pct"], resultado["limiar"])
    return salvas


def executar(barragens: list[Barragem], inicio: str, fim: str,
             validar_s2: bool = False, reprocessar: bool = False, apenas_s2: bool = False) -> None:
    database.criar_tabelas()
    for b in barragens:
        log.info("=== %s (risco: %s) ===", b.nome, b.categoria_risco)
        try:
            if not apenas_s2:
                n = processar_s1(b, inicio, fim, reprocessar)
                log.info("Sentinel-1: %d novas datas salvas", n)
                qualidade.atualizar_qualidade(b.slug)
                crista.atualizar_metricas(b)
            if validar_s2 or apenas_s2:
                n2 = processar_s2(b, inicio, fim, reprocessar)
                log.info("Sentinel-2: %d novas datas salvas", n2)
                superficie.atualizar_qualidade_s2(b)     # antes das classes e dos alertas
                superficie.atualizar_sensibilidade(b)
                superficie.atualizar_classes(b)
        except Exception:
            # Um erro numa barragem não deve parar as outras.
            log.exception("Falha ao processar '%s'", b.nome)
