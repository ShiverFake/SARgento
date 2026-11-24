"""
Model: qualidade da cena (núcleo de água permanente)

O problema: vento forte ou chuva no momento da passagem encrespam a
superfície do lago. A água deixa de ser um "espelho" e fica CLARA no radar,
e o programa passa a vê-la como terra. Resultado: uma queda falsa da área.

A solução: olhar um pedaço do lago que SEMPRE é água (o "núcleo").
  1. Empilhamos as máscaras de todas as datas e contamos, para cada pixel,
     em quantas datas ele foi água (frequência).
  2. Núcleo = pixels com frequência >= 90%, dentro do polígono.
  3. Erosão de 2 pixels: tiramos a borda do núcleo, longe das margens,
     onde a água pode variar de verdade.
  4. Para cada data, calculamos a mediana do VV (dB) dentro do núcleo.
     Água calma fica em torno de -20 dB. Se nesse dia o núcleo ficou 3 dB
     (ou mais) acima da mediana da série, a culpa é do tempo, não do
     reservatório: marcamos "suspeita de vento/chuva".
"""
import logging
from collections import Counter

import numpy as np
from scipy import ndimage

import config
from app.models import database

log = logging.getLogger(__name__)


def caminho_raster(slug: str, data: str, sensor: str = "S1"):
    return config.PASTA_RASTERS / f"{slug}_{data}_{sensor}.npz"


def salvar_raster(slug: str, data: str, vv_db, agua, dentro, affine=None, epsg=None) -> None:
    """Guarda o VV filtrado (dB), a máscara de água e o polígono rasterizado.

    float16 ocupa metade do espaço de float32 e a precisão (~0,01 dB) sobra.
    savez_compressed compacta o arquivo (máscaras compactam muito bem).
    affine/epsg guardam ONDE a grade está no mundo (necessário para cruzar
    a máscara com as linhas de crista e com as imagens de referência).
    """
    config.PASTA_RASTERS.mkdir(parents=True, exist_ok=True)
    extras = {}
    if affine is not None:
        extras = {"affine": np.array(tuple(affine)[:6], dtype="float64"), "epsg": np.array(epsg)}
    np.savez_compressed(caminho_raster(slug, data), vv_db=vv_db.astype("float16"),
                        agua=agua.astype(bool), dentro=dentro.astype(bool), **extras)


def calcular_nucleo(mascaras: np.ndarray, dentro: np.ndarray,
                    freq_min: float = config.NUCLEO_FREQ_MIN,
                    erosao: int = config.NUCLEO_EROSAO_PX) -> np.ndarray:
    """mascaras: pilha (datas, linhas, colunas) de True/False."""
    frequencia = mascaras.mean(axis=0)           # média de True/False = fração de datas com água
    nucleo = (frequencia >= freq_min) & dentro
    if erosao > 0:
        nucleo = ndimage.binary_erosion(nucleo, iterations=erosao)
    return nucleo


def atualizar_qualidade(slug: str) -> None:
    """Recalcula o núcleo com TODAS as datas e atualiza o banco.

    É recalculado a cada execução porque o núcleo melhora conforme a série
    cresce (mais datas = estimativa de "água permanente" mais confiável).
    """
    serie = database.obter_serie(slug, "S1")
    datas = [d.strftime("%Y-%m-%d") for d in serie["data"]]
    rasters = {}
    for data in datas:
        caminho = caminho_raster(slug, data)
        if caminho.exists():
            with np.load(caminho) as arq:
                rasters[data] = {k: arq[k] for k in ("vv_db", "agua", "dentro")}

    # Todas as datas precisam ter a mesma grade; se alguma diferir (ex.: o
    # polígono foi alterado), usamos o formato mais comum.
    if rasters:
        formato = Counter(r["agua"].shape for r in rasters.values()).most_common(1)[0][0]
        rasters = {d: r for d, r in rasters.items() if r["agua"].shape == formato}

    if len(rasters) < config.NUCLEO_MIN_DATAS:
        log.warning("Qualidade: só %d datas com raster salvo (mínimo %d). "
                    "Rode o processamento com --reprocessar para gerá-los.",
                    len(rasters), config.NUCLEO_MIN_DATAS)
        return

    ordem = sorted(rasters)
    pilha = np.stack([rasters[d]["agua"] for d in ordem])
    dentro = rasters[ordem[-1]]["dentro"]
    nucleo = calcular_nucleo(pilha, dentro)
    if nucleo.sum() < 10:
        log.warning("Qualidade: núcleo de água permanente muito pequeno (%d px).", nucleo.sum())
        return
    log.info("Qualidade: núcleo com %d pixels (%.1f ha), calculado com %d datas",
             nucleo.sum(), nucleo.sum() * config.RESOLUCAO_M ** 2 / 1e4, len(ordem))

    medianas = {}
    for d in ordem:
        valores = rasters[d]["vv_db"][nucleo].astype("float32")
        valores = valores[np.isfinite(valores)]
        medianas[d] = float(np.median(valores)) if valores.size else np.nan

    referencia = np.nanmedian(list(medianas.values()))  # "VV típico" do núcleo
    for d, vv in medianas.items():
        delta = vv - referencia
        suspeita = bool(np.isfinite(delta) and delta >= config.LIMIAR_SUSPEITA_DB)
        database.atualizar_campos(slug, d, "S1", {
            "vv_nucleo_db": None if np.isnan(vv) else round(vv, 2),
            "vv_nucleo_delta_db": None if np.isnan(delta) else round(delta, 2),
            "suspeita_meteo": int(suspeita),
        })
        if suspeita:
            log.info("  %s: VV do núcleo %.1f dB (%+.1f dB) -> suspeita de vento/chuva", d, vv, delta)
