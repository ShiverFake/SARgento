"""
Model: busca e carregamento de cenas Sentinel-1 RTC

Fluxo:
1. pystac-client pergunta ao catálogo STAC: "quais cenas cobrem esta área
   neste período?"
2. planetary_computer "assina" os links (adiciona um token de acesso
   temporário às URLs).
3. odc-stac lê só o pedaço necessário de cada imagem e empilha tudo num
   cubo xarray com dimensões (tempo, y, x).
"""
import logging
from collections import Counter

import planetary_computer
import pystac_client
import odc.stac  # noqa: F401  (registra o acessório ".odc" no xarray)
from odc.stac import load

import config

log = logging.getLogger(__name__)


def abrir_catalogo() -> pystac_client.Client:
    # sign_inplace assina automaticamente cada item retornado
    return pystac_client.Client.open(config.STAC_URL,
                                     modifier=planetary_computer.sign_inplace)


def _chave_orbita(item) -> tuple:
    p = item.properties
    return (p.get("sat:orbit_state"), p.get("sat:relative_orbit"))


def buscar_cenas(bbox, inicio: str, fim: str,
                 orbita_relativa=config.ORBITA_RELATIVA,
                 direcao=config.DIRECAO_ORBITA):
    """Busca cenas S1 RTC e mantém só UMA órbita.

    Retorna (lista_de_itens, "direcao/numero_da_orbita").
    """
    busca = abrir_catalogo().search(
        collections=[config.COLECAO_S1],
        bbox=bbox,
        datetime=f"{inicio}/{fim}",
    )
    itens = list(busca.items())
    log.info("Sentinel-1: %d cenas encontradas antes do filtro de órbita", len(itens))
    if not itens:
        return [], None

    # Filtros opcionais definidos em config.py
    if direcao:
        itens = [i for i in itens if i.properties.get("sat:orbit_state") == direcao]
    if orbita_relativa is not None:
        itens = [i for i in itens if i.properties.get("sat:relative_orbit") == orbita_relativa]
    if not itens:
        return [], None

    # Se ainda sobrou mais de uma órbita, fica com a mais frequente
    # (= maior número de passagens = série temporal mais densa).
    contagem = Counter(_chave_orbita(i) for i in itens)
    escolhida, n = contagem.most_common(1)[0]
    log.info("Órbitas disponíveis: %s -> usando %s (%d cenas)", dict(contagem), escolhida, n)
    itens = [i for i in itens if _chave_orbita(i) == escolhida]
    itens.sort(key=lambda i: i.datetime)
    return itens, f"{escolhida[0]}/{escolhida[1]}"


def carregar_cubo(itens, bbox, epsg: int):
    """Monta o cubo (tempo, y, x) com as bandas VV e VH em escala LINEAR.

    - crs/resolution: reprojeta para UTM com pixels de 10 m.
    - groupby="solar_day": junta cenas vizinhas do mesmo dia num só mosaico.
    - chunks={}: carregamento "preguiçoso" (dask) — nada é baixado até
      pedirmos .compute(). Assim processamos uma data por vez sem estourar a RAM.
    """
    return load(
        itens,
        bands=["vv", "vh"],
        bbox=bbox,
        crs=f"EPSG:{epsg}",
        resolution=config.RESOLUCAO_M,
        groupby="solar_day",
        chunks={},
        fail_on_error=False,
    )
