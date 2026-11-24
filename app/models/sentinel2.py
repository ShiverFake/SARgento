"""
Model: validação com Sentinel-2 (óptico)

O Sentinel-2 enxerga como uma câmera (luz do sol), então NUVEM atrapalha.
Por isso só usamos cenas com < 10% de nuvem, e ainda mascaramos os pixels
de nuvem/sombra pela banda SCL (classificação da cena).

Índice de água MNDWI = (Verde - SWIR) / (Verde + SWIR)
  - água absorve muito o SWIR (infravermelho de onda curta) -> MNDWI > 0
  - solo/vegetação refletem SWIR                            -> MNDWI < 0
Bandas: B03 = verde (10 m), B11 = SWIR1 (20 m, reamostrada para 10 m).
"""
import logging

import numpy as np
import pandas as pd
from odc.stac import load

import config
from app.models import processamento
from app.models.sentinel1 import abrir_catalogo

log = logging.getLogger(__name__)

# Classes da banda SCL (Scene Classification Layer):
#   0 sem dado, 1 saturado, 3 sombra de nuvem, 8 nuvem média, 9 nuvem alta, 10 cirrus
SCL_NUVEM_SOMBRA = [3, 8, 9, 10]          # usadas no % de nuvem do polígono
SCL_INVALIDAS = [0, 1] + SCL_NUVEM_SOMBRA  # pixels que não entram no MNDWI


def buscar_cenas(bbox, inicio: str, fim: str):
    busca = abrir_catalogo().search(
        collections=[config.COLECAO_S2],
        bbox=bbox,
        datetime=f"{inicio}/{fim}",
        query={"eo:cloud_cover": {"lt": config.NUVEM_MAX_S2}},
    )
    itens = sorted(busca.items(), key=lambda i: i.datetime)
    log.info("Sentinel-2: %d cenas com nuvem < %d%%", len(itens), config.NUVEM_MAX_S2)
    return itens


def carregar_cubo(itens, bbox, epsg: int):
    return load(itens, bands=["B03", "B11", "SCL"], bbox=bbox,
                crs=f"EPSG:{epsg}", resolution=config.RESOLUCAO_M,
                groupby="solar_day", chunks={}, fail_on_error=False)


def offsets_por_data(itens) -> dict:
    """A partir de 25/01/2022 (baseline de processamento 04.00) a ESA passou
    a somar 1000 aos números digitais do L2A. Para calcular um índice
    normalizado corretamente precisamos subtrair esse deslocamento."""
    offsets = {}
    for item in itens:
        baseline = item.properties.get("s2:processing_baseline", "0")
        try:
            offsets[item.datetime.strftime("%Y-%m-%d")] = 1000 if float(baseline) >= 4.0 else 0
        except ValueError:
            offsets[item.datetime.strftime("%Y-%m-%d")] = 0
    return offsets


def limiar_mndwi(mndwi, valido, dentro, faixa_otsu=None, limiar_fixo=None):
    """Limiar de água no MNDWI. Retorna (limiar_usado, limiar_otsu_bruto ou None).

    Ordem de prioridade:
    1. limiar_fixo da barragem (coluna mndwi_limiar, calibrado em imagens RGB);
    2. Otsu dentro do polígono, preso na faixa (colunas mndwi_otsu_min/max);
    3. limiar global (config.LIMIAR_MNDWI = 0), bom para água limpa.
    Em barragens de rejeito o limiar 0 não serve: a praia úmida e clara tem
    MNDWI positivo (~+0,15) e seria contada como lago.
    """
    if limiar_fixo is not None:
        return limiar_fixo, None
    if faixa_otsu is None:
        return config.LIMIAR_MNDWI, None
    usado, bruto = processamento.limiar_otsu(mndwi[valido & dentro], *faixa_otsu)
    return usado, (None if np.isnan(bruto) else bruto)


def processar_cena_s2(b03, b11, scl, offset, dentro, area_pixel_m2, faixa_otsu=None, limiar_fixo=None):
    """Retorna (resultado, mascara_agua, fracao_do_poligono_com_dado, (mndwi, valido)).

    O MNDWI e a máscara de pixels válidos são devolvidos para serem salvos:
    com eles dá para recalcular a área com outros limiares sem baixar de novo.
    """
    verde = b03.astype("float32") - offset
    swir = b11.astype("float32") - offset

    # "Com dado" = o satélite imageou o pixel (pode ser nuvem, mas existe).
    com_dado = (scl != 0) & (b03 > 0) & (b11 > 0)
    n_dado_poligono = int((com_dado & dentro).sum())
    cobertura_dados = n_dado_poligono / max(int(dentro.sum()), 1)

    # % de nuvem/sombra DENTRO do polígono. A nuvem da cena inteira (eo:cloud_cover)
    # pode ser 1% e, por azar, esse 1% estar bem em cima do reservatório.
    nuvem = np.isin(scl, SCL_NUVEM_SOMBRA) & com_dado & dentro
    nuvem_pct = 100.0 * nuvem.sum() / n_dado_poligono if n_dado_poligono else 100.0

    valido = com_dado & ~np.isin(scl, SCL_INVALIDAS)
    with np.errstate(divide="ignore", invalid="ignore"):
        mndwi = (verde - swir) / (verde + swir)
    valido &= np.isfinite(mndwi)

    limiar, bruto = limiar_mndwi(mndwi, valido, dentro, faixa_otsu, limiar_fixo)
    agua = processamento.limpar_mascara((mndwi > limiar) & valido)
    resultado = processamento.calcular_areas(agua, dentro, valido, area_pixel_m2)
    resultado["limiar"] = round(float(limiar), 3)
    resultado["limiar_otsu_bruto"] = None if bruto is None else round(float(bruto), 3)
    resultado["nuvem_poligono_pct"] = round(float(nuvem_pct), 2)
    return resultado, agua, cobertura_dados, (mndwi, valido)


def area_com_limiar(mndwi, valido, dentro, limiar, area_pixel_m2) -> float:
    """Área de água (ha) dentro do polígono para um limiar MNDWI qualquer."""
    agua = processamento.limpar_mascara((mndwi > limiar) & valido)
    return round(float((agua & dentro).sum() * area_pixel_m2 / 1e4), 2)


def s2_confiavel(df: pd.DataFrame) -> pd.Series:
    """True nas datas S2 confiáveis: pouca nuvem no polígono E aprovada no
    controle de qualidade da SCL. Usada na validação, nas classes e nos alertas."""
    if df.empty:
        return pd.Series(dtype=bool)
    nuvem_ok = df["nuvem_poligono_pct"].astype(float) <= config.NUVEM_MAX_POLIGONO_PCT
    scl_ok = df.get("nao_confiavel_scl", pd.Series(0, index=df.index)).fillna(0).astype(int) != 1
    return nuvem_ok & scl_ok


def motivo_nao_confiavel(linha) -> str:
    """Texto curto do motivo ('' se a data é confiável)."""
    if linha.get("nuvem_poligono_pct") is None or not (linha["nuvem_poligono_pct"] <= config.NUVEM_MAX_POLIGONO_PCT):
        return "nuvem"
    if linha.get("nao_confiavel_scl") == 1:
        return "SCL"
    return ""


def comparar_s1_s2(df_s1: pd.DataFrame, df_s2: pd.DataFrame,
                   tolerancia_dias: int = config.TOLERANCIA_DIAS_VALIDACAO) -> pd.DataFrame:
    """Para cada data S2, acha a data S1 mais próxima (até N dias) e compara.

    Datas S2 com mais de NUVEM_MAX_POLIGONO_PCT % de nuvem/sombra no polígono
    recebem confiavel=False: sob a nuvem não sabemos se há água, então a área
    S2 tende a sair subestimada e não serve de referência.
    """
    colunas = ["data_s2", "data_s1", "dias", "lago_s1_ha", "lago_s2_ha",
               "diferenca_ha", "diferenca_pct", "nuvem_poligono_pct", "confiavel", "motivo_nao_confiavel"]
    if df_s1.empty or df_s2.empty:
        return pd.DataFrame(columns=colunas)

    a = df_s2[["data", "area_lago_ha", "nuvem_poligono_pct"]].rename(
        columns={"area_lago_ha": "lago_s2_ha"})
    a["confiavel"] = s2_confiavel(df_s2).values
    a["motivo_nao_confiavel"] = [motivo_nao_confiavel(l) for _, l in df_s2.iterrows()]
    b = df_s1[["data", "area_lago_ha"]].rename(columns={"area_lago_ha": "lago_s1_ha"})
    a["data_s2"], b["data_s1"] = a["data"], b["data"]

    # merge_asof = "junção pela data mais próxima"
    m = pd.merge_asof(a.sort_values("data"), b.sort_values("data"), on="data",
                      direction="nearest", tolerance=pd.Timedelta(days=tolerancia_dias))
    m = m.dropna(subset=["lago_s1_ha"])
    m["dias"] = (m["data_s1"] - m["data_s2"]).dt.days
    m["diferenca_ha"] = (m["lago_s1_ha"] - m["lago_s2_ha"]).round(2)
    # % só quando a área S2 passa de 5 ha: em áreas pequenas a % não tem sentido
    # (0,8 ha x 20 ha = +2.400%). Abaixo disso vale a diferença em ha.
    grande = m["lago_s2_ha"] > config.AREA_MIN_PCT_HA
    m["diferenca_pct"] = (100 * m["diferenca_ha"] / m["lago_s2_ha"]).where(grande).round(1)
    return m[colunas].reset_index(drop=True)


def estatisticas_validacao(validacao: pd.DataFrame) -> dict:
    """Estatísticas de erro, só das datas confiáveis.

    Ex.: diferenças (S1 - S2) de +4% e -6%:
      - erro médio ABSOLUTO = (4 + 6) / 2 = 5%   -> "quanto erra", sem sinal
      - viés médio          = (4 - 6) / 2 = -1%  -> "para que lado erra":
        negativo = S1 tende a dar MENOS área que o S2; positivo = MAIS.
    """
    vazio = {"erro_medio_abs_ha": None, "vies_medio_ha": None, "n_validas": 0,
             "erro_medio_abs_pct": None, "vies_medio_pct": None, "n_validas_pct": 0}
    if validacao.empty:
        return vazio
    validas = validacao[validacao["confiavel"].astype(bool)].dropna(subset=["diferenca_ha"])
    if validas.empty:
        return vazio
    com_pct = validas.dropna(subset=["diferenca_pct"])   # só datas com S2 > 5 ha
    return {
        # Em hectares: vale para todas as estruturas
        "erro_medio_abs_ha": round(float(validas["diferenca_ha"].abs().mean()), 2),
        "vies_medio_ha": round(float(validas["diferenca_ha"].mean()), 2),
        "n_validas": int(len(validas)),
        # Em %: só nas datas com área S2 > 5 ha
        "erro_medio_abs_pct": round(float(com_pct["diferenca_pct"].abs().mean()), 1) if len(com_pct) else None,
        "vies_medio_pct": round(float(com_pct["diferenca_pct"].mean()), 1) if len(com_pct) else None,
        "n_validas_pct": int(len(com_pct)),
    }
