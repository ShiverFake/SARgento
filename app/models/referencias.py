"""
Model: datas RGB de referência (conferência visual independente)

Por que isso existe: rejeito encharcado é escuro no radar (parece água para o
S1) e tem MNDWI positivo (parece água para o S2). Os dois sensores podem errar
JUNTOS, e aí a validação S1 x S2 concorda num valor errado. A foto colorida
(RGB) é uma terceira opinião: o olho humano distingue lago de praia molhada.

Para cada barragem escolhemos 4 cenas Sentinel-2:
  - 2 na seca (jun–set) e 2 na chuva (dez–mar), a mais limpa de cada período;
  - "mais limpa" = menor % de nuvem/sombra DENTRO do polígono (banda SCL).
Se o período monitorado só tem UMA estação chuvosa (ou seca), ela é dividida
em duas metades, para ainda termos 2 cenas daquele tipo.

Cada painel mostra, lado a lado:
  RGB com o polígono | máscara S2 (MNDWI) | máscara S1 da data mais próxima
"""
import json
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from affine import Affine
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

import config
from app.models import database, geo, qualidade, sentinel2
from app.models.barragem import Barragem
from app.models.sentinel1 import abrir_catalogo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Períodos
# ---------------------------------------------------------------------------
def periodos(inicio: date, fim: date) -> list[dict]:
    """Lista os 4 períodos (2 de seca, 2 de chuva) dentro de [inicio, fim]."""
    s0, s1 = config.MESES_SECA
    c0, c1 = config.MESES_CHUVA
    candidatos = []
    for ano in range(inicio.year - 1, fim.year + 1):
        candidatos.append(("seca", f"Seca {ano}", date(ano, s0, 1), date(ano, s1, 30)))
        candidatos.append(("chuva", f"Chuva {ano}/{str(ano + 1)[-2:]}",
                           date(ano, c0, 1), date(ano + 1, c1, 31)))
    saida = []
    for tipo in ("seca", "chuva"):
        dentro = []
        for t, rot, a, b in candidatos:
            a, b = max(a, inicio), min(b, fim)
            if t == tipo and (b - a).days >= 30:   # ao menos 1 mês de estação
                dentro.append({"tipo": t, "rotulo": rot, "inicio": a, "fim": b})
        dentro = dentro[-2:]   # as duas mais recentes
        if len(dentro) == 1:   # só uma estação: divide em duas metades
            p = dentro[0]
            meio = p["inicio"] + (p["fim"] - p["inicio"]) / 2
            dentro = [{**p, "rotulo": p["rotulo"] + " (1ª metade)", "fim": meio},
                      {**p, "rotulo": p["rotulo"] + " (2ª metade)", "inicio": meio + timedelta(days=1)}]
        saida += dentro
    return saida


# ---------------------------------------------------------------------------
# 2. Escolha da cena mais limpa de um período
# ---------------------------------------------------------------------------
def _melhor_cena(barragem, bbox, epsg, dentro_poligono_ll, p):
    itens = list(abrir_catalogo().search(
        collections=[config.COLECAO_S2], bbox=bbox,
        datetime=f"{p['inicio']:%Y-%m-%d}/{p['fim']:%Y-%m-%d}",
        query={"eo:cloud_cover": {"lt": config.REFERENCIA_NUVEM_CENA_MAX}}).items())
    if not itens:
        return None
    # Agrupa por data e ordena pela nuvem da cena (a mais limpa primeiro)
    por_data = {}
    for i in itens:
        por_data.setdefault(i.datetime.strftime("%Y-%m-%d"), []).append(i)
    ordem = sorted(por_data, key=lambda d: min(i.properties["eo:cloud_cover"] for i in por_data[d]))
    melhor = None
    for data in ordem[:config.REFERENCIA_CANDIDATAS]:
        grupo = por_data[data]
        from odc.stac import load
        ds = load(grupo, bands=["B02", "B03", "B04", "B11", "SCL"], bbox=bbox, crs=f"EPSG:{epsg}",
                  resolution=config.RESOLUCAO_M, groupby="solar_day", chunks={},
                  fail_on_error=False).isel(time=0).compute()
        affine = ds.odc.geobox.affine
        dentro = geo.rasterizar(geo.para_utm(dentro_poligono_ll, epsg), tuple(ds.odc.geobox.shape), affine)
        scl = ds["SCL"].values
        com_dado = (scl != 0) & dentro
        if com_dado.sum() < 0.9 * dentro.sum():
            continue  # cena não cobre o polígono
        nuvem = 100 * (np.isin(scl, sentinel2.SCL_NUVEM_SOMBRA) & dentro).sum() / com_dado.sum()
        if melhor is None or nuvem < melhor["nuvem_pct"]:
            off = sentinel2.offsets_por_data(grupo).get(data, 0)
            melhor = {"data": data, "nuvem_pct": float(nuvem), "ds": ds, "affine": affine,
                      "dentro": dentro, "offset": off}
        if nuvem < 0.1:
            break  # não dá para ficar mais limpo
    return melhor


# ---------------------------------------------------------------------------
# 3. Desenho do painel
# ---------------------------------------------------------------------------
def _fonte(tamanho):
    for caminho in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"):
        try:
            return ImageFont.truetype(caminho, tamanho)
        except OSError:
            continue
    return ImageFont.load_default()


def _esticar(img):
    """Realce de contraste simples (2% a 98%) para a imagem não ficar escura."""
    v = img[np.isfinite(img)]
    lo, hi = np.percentile(v, [2, 98]) if v.size else (0, 1)
    return np.nan_to_num(np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1), nan=0.0)


def _contorno(mascara):
    return mascara & ~ndimage.binary_erosion(mascara)


def _painel(titulo, fundo_rgb, agua, dentro, linhas_px, recorte, escala):
    r0, r1, c0, c1 = recorte
    img = (fundo_rgb[r0:r1, c0:c1] * 255).astype(np.uint8).copy()
    if agua is not None:
        a = agua[r0:r1, c0:c1]
        img[a] = (0.45 * img[a] + 0.55 * np.array([30, 144, 255])).astype(np.uint8)
    img[_contorno(dentro)[r0:r1, c0:c1]] = (255, 210, 0)
    im = Image.fromarray(img).resize(((c1 - c0) * escala, (r1 - r0) * escala), Image.NEAREST)
    d = ImageDraw.Draw(im, "RGBA")
    for pts in linhas_px:
        d.line([((x - c0) * escala, (y - r0) * escala) for x, y in pts], fill=(255, 0, 255, 255), width=3)
    d.rectangle([0, 0, im.width, 26], fill=(0, 0, 0, 180))
    d.text((6, 4), titulo, fill=(255, 255, 255), font=_fonte(15))
    return im


def gerar(barragem: Barragem) -> list[dict]:
    """Escolhe as 4 cenas, desenha os painéis e devolve a lista (também salva em JSON)."""
    serie_s1 = database.obter_serie(barragem.slug, "S1")
    if serie_s1.empty:
        log.warning("Referências: '%s' ainda não tem série S1.", barragem.nome)
        return []
    epsg = geo.epsg_utm(barragem.lon, barragem.lat)
    poligono = barragem.poligono or geo.circulo_em_volta(barragem.lon, barragem.lat, 500, epsg)
    bbox = geo.bbox_latlon(poligono, epsg, config.BUFFER_M)
    inicio, fim = serie_s1["data"].min().date(), serie_s1["data"].max().date()
    datas_s1 = [d.strftime("%Y-%m-%d") for d in serie_s1["data"]]

    config.PASTA_REFERENCIAS.mkdir(parents=True, exist_ok=True)
    saida = []
    for k, p in enumerate(periodos(inicio, fim), start=1):
        cena = _melhor_cena(barragem, bbox, epsg, poligono, p)
        if cena is None:
            log.warning("Referências: nenhuma cena S2 utilizável em %s", p["rotulo"])
            saida.append({"periodo": p["rotulo"], "tipo": p["tipo"], "arquivo": None})
            continue
        nome = f"{barragem.slug}_ref{k}_{cena['data']}.png"
        r = _desenhar(barragem, cena, epsg, serie_s1, nome)
        saida.append({"periodo": p["rotulo"], "tipo": p["tipo"], **r})
        log.info("Referência %s: S2 %s (nuvem %.1f%%) x S1 %s (%+d d)",
                 p["rotulo"], cena["data"], cena["nuvem_pct"], r["data_s1"], r["dias"])

    with open(config.PASTA_REFERENCIAS / f"{barragem.slug}.json", "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)
    return saida


def gerar_data(barragem: Barragem, data: str) -> dict:
    """Painel avulso de UMA data S2 (para conferência pontual; não entra no relatório)."""
    serie_s1 = database.obter_serie(barragem.slug, "S1")
    epsg = geo.epsg_utm(barragem.lon, barragem.lat)
    poligono = barragem.poligono or geo.circulo_em_volta(barragem.lon, barragem.lat, 500, epsg)
    bbox = geo.bbox_latlon(poligono, epsg, config.BUFFER_M)
    dia = pd.Timestamp(data).date()
    cena = _melhor_cena(barragem, bbox, epsg, poligono, {"inicio": dia, "fim": dia, "rotulo": data})
    if cena is None:
        raise ValueError(f"Nenhuma cena S2 cobrindo o polígono em {data}")
    config.PASTA_REFERENCIAS.mkdir(parents=True, exist_ok=True)
    r = _desenhar(barragem, cena, epsg, serie_s1, f"{barragem.slug}_avulso_{cena['data']}.png")
    # Composição da banda SCL dentro do polígono (para diagnosticar nuvem/sombra)
    scl = cena["ds"]["SCL"].values[cena["dentro"]]
    nomes = {0: "sem dado", 1: "saturado", 2: "sombra escura/topografia", 3: "sombra de nuvem",
             4: "vegetação", 5: "solo exposto", 6: "água", 7: "não classificado",
             8: "nuvem média", 9: "nuvem alta", 10: "cirrus", 11: "neve"}
    valores, contagem = np.unique(scl, return_counts=True)
    r["scl_pct"] = {nomes.get(int(v), str(v)): round(100 * c / scl.size, 2) for v, c in zip(valores, contagem)}
    return r


def _desenhar(barragem, cena, epsg, serie_s1, nome) -> dict:
    """Desenha o painel RGB | S2 | S1 de uma cena e devolve os números dela."""
    datas_s1 = [d.strftime("%Y-%m-%d") for d in serie_s1["data"]]
    ds, affine, dentro = cena["ds"], cena["affine"], cena["dentro"]

    # Máscara S2 com a MESMA regra do pipeline (limiar fixo ou Otsu da barragem)
    res, agua_s2, _, _ = sentinel2.processar_cena_s2(
        ds["B03"].values, ds["B11"].values, ds["SCL"].values, cena["offset"],
        dentro, config.RESOLUCAO_M ** 2, faixa_otsu=barragem.faixa_otsu_mndwi,
        limiar_fixo=barragem.mndwi_limiar)

    # S1 da data mais próxima (lida dos rasters salvos)
    alvo = pd.Timestamp(cena["data"])
    data_s1 = min(datas_s1, key=lambda d: abs((pd.Timestamp(d) - alvo).days))
    dias = (pd.Timestamp(data_s1) - alvo).days
    agua_s1 = vv = None
    caminho = qualidade.caminho_raster(barragem.slug, data_s1)
    if caminho.exists():
        with np.load(caminho) as arq:
            if arq["agua"].shape == dentro.shape:   # mesma grade (mesmo bbox/CRS/resolução)
                agua_s1, vv = arq["agua"], arq["vv_db"].astype("float32")

    rgb = np.dstack([_esticar(ds[b].values.astype("float32")) for b in ("B04", "B03", "B02")]) ** 0.8
    cinza = np.repeat(rgb.mean(axis=2, keepdims=True), 3, axis=2) * 0.8
    fundo_s1 = np.repeat(_esticar(vv)[..., None], 3, axis=2) if vv is not None else cinza * 0

    # Recorte: polígono + margem de 25 pixels
    linhas, colunas = np.nonzero(dentro)
    recorte = (max(linhas.min() - 25, 0), min(linhas.max() + 25, dentro.shape[0]),
               max(colunas.min() - 25, 0), min(colunas.max() + 25, dentro.shape[1]))
    escala = max(1, int(380 / (recorte[3] - recorte[2])))
    linhas_px = [[(~affine) * xy for xy in geo.para_utm(l, epsg).coords]
                 for l in barragem.cristas.values()]

    br = lambda v: f"{v:.1f}".replace(".", ",")
    paineis = [
        _painel(f"RGB {pd.Timestamp(cena['data']):%d/%m/%Y} (nuvem {br(cena['nuvem_pct'])}%)",
                rgb, None, dentro, linhas_px, recorte, escala),
        _painel(f"S2 MNDWI > {res['limiar']:.2f}".replace(".", ",") + f": {br(res['area_lago_ha'])} ha",
                cinza, agua_s2, dentro, linhas_px, recorte, escala),
        _painel(f"S1 {pd.Timestamp(data_s1):%d/%m/%Y} ({dias:+d} d)" if agua_s1 is not None
                else "S1: sem raster nesta grade", fundo_s1, agua_s1, dentro, linhas_px, recorte, escala),
    ]
    largura = sum(p_.width for p_ in paineis) + 8 * (len(paineis) - 1)
    final = Image.new("RGB", (largura, paineis[0].height), (255, 255, 255))
    x = 0
    for p_ in paineis:
        final.paste(p_, (x, 0)); x += p_.width + 8
    final.save(config.PASTA_REFERENCIAS / nome)

    lago_s1 = serie_s1.loc[serie_s1["data"] == pd.Timestamp(data_s1), "area_lago_ha"]
    return {"data_s2": cena["data"], "nuvem_poligono_pct": round(cena["nuvem_pct"], 2),
            "data_s1": data_s1, "dias": dias,
            "lago_s2_ha": res["area_lago_ha"], "limiar_mndwi": res["limiar"],
            "lago_s1_ha": float(lago_s1.iloc[0]) if len(lago_s1) else None, "arquivo": nome}


def carregar(slug: str) -> list[dict]:
    caminho = config.PASTA_REFERENCIAS / f"{slug}.json"
    if not caminho.exists():
        return []
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)
