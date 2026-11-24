"""
Model: métricas de proximidade entre a água e a crista

OBSERVAÇÃO, NÃO É AVALIAÇÃO DE SEGURANÇA. Os números descrevem onde o radar
viu água em relação a uma linha desenhada sobre uma imagem; não dizem nada
sobre borda livre, nível d'água, percolação ou estabilidade.

Para cada passagem do Sentinel-1 e cada trecho de crista:
  - dist_min_m  : a menor distância entre a água e a linha (vazio se não há água)
  - ext_30m_m   : quantos metros da crista têm água a menos de 30 m
  - ext_30m_pct : o mesmo, em % do comprimento do trecho

Como é calculado:
  1. "Mapa de distâncias": para CADA pixel da imagem, a distância até o pixel
     de água mais próximo (scipy.ndimage.distance_transform_edt).
  2. A linha da crista é amostrada a cada 5 m; em cada ponto lemos o mapa.
  3. Mínimo dos pontos = dist_min; pontos com distância < 30 m = extensão.

Precisão aproximada: ±20 m (pixel de 10 m e as distorções geométricas do
radar no talude inclinado da barragem).
"""
import logging

import numpy as np
from affine import Affine
from scipy import ndimage

import config
from app.models import database, geo, qualidade
from app.models.barragem import Barragem

log = logging.getLogger(__name__)


def _pontos_da_linha(linha_utm, passo_m: float) -> np.ndarray:
    """Pontos igualmente espaçados ao longo da linha, em coordenadas UTM."""
    n = max(int(linha_utm.length // passo_m) + 1, 2)
    return np.array([linha_utm.interpolate(d).coords[0]
                     for d in np.linspace(0, linha_utm.length, n)])


def medir(agua: np.ndarray, affine: Affine, linha_utm,
          limite_m: float = config.CRISTA_DISTANCIA_M,
          passo_m: float = config.CRISTA_PASSO_M) -> dict:
    """Métricas de UM trecho de crista numa data."""
    comprimento = float(linha_utm.length)
    if not agua.any():
        return {"comprimento_m": round(comprimento, 1), "dist_min_m": None,
                "ext_30m_m": 0.0, "ext_30m_pct": 0.0}

    tamanho_pixel = abs(affine.a)
    # distance_transform_edt mede a distância até o pixel "False" mais próximo;
    # por isso passamos ~agua (água = False = "alvo").
    distancia = ndimage.distance_transform_edt(~agua) * tamanho_pixel

    pontos = _pontos_da_linha(linha_utm, passo_m)
    colunas, linhas = (~affine) * (pontos[:, 0], pontos[:, 1])
    linhas = np.clip(np.floor(linhas).astype(int), 0, agua.shape[0] - 1)
    colunas = np.clip(np.floor(colunas).astype(int), 0, agua.shape[1] - 1)
    d = distancia[linhas, colunas]

    perto = d < limite_m
    # Cada ponto "representa" um pedaço de linha de comprimento/(n-1).
    extensao = float(perto.mean() * comprimento)
    return {"comprimento_m": round(comprimento, 1),
            "dist_min_m": round(float(d.min()), 1),
            "ext_30m_m": round(extensao, 1),
            "ext_30m_pct": round(100 * extensao / comprimento, 1)}


def atualizar_metricas(barragem: Barragem) -> None:
    """Recalcula as métricas de crista de todas as datas S1 da barragem."""
    if not barragem.cristas:
        return
    epsg = geo.epsg_utm(barragem.lon, barragem.lat)
    linhas_utm = {tr: geo.para_utm(l, epsg) for tr, l in barragem.cristas.items()}
    comprimento_total = sum(l.length for l in linhas_utm.values())

    resultados = []
    serie = database.obter_serie(barragem.slug, "S1")
    for data in (d.strftime("%Y-%m-%d") for d in serie["data"]):
        caminho = qualidade.caminho_raster(barragem.slug, data)
        if not caminho.exists():
            continue
        with np.load(caminho) as arq:
            if "affine" not in arq:
                continue  # raster antigo, sem a posição da grade
            affine = Affine(*arq["affine"])
            # Só a água DENTRO do polígono: poças a jusante não contam.
            agua = arq["agua"] & arq["dentro"]

        total_ext, dists = 0.0, []
        for trecho, linha in linhas_utm.items():
            m = medir(agua, affine, linha)
            resultados.append({"data": data, "trecho": trecho, **m})
            total_ext += m["ext_30m_m"]
            if m["dist_min_m"] is not None:
                dists.append(m["dist_min_m"])
        resultados.append({"data": data, "trecho": "total",
                           "comprimento_m": round(comprimento_total, 1),
                           "dist_min_m": min(dists) if dists else None,
                           "ext_30m_m": round(total_ext, 1),
                           "ext_30m_pct": round(100 * total_ext / comprimento_total, 1)})

    database.salvar_metricas_crista(barragem.slug, resultados)
    log.info("Crista: métricas de %d datas x %d trechos salvas",
             len({r["data"] for r in resultados}), len(linhas_utm))
