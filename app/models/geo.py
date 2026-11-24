"""
Model: utilidades geográficas

Conceito importante: coordenadas lat/lon (graus) são ótimas para localizar,
mas péssimas para medir ÁREA. Para medir em metros, projetamos tudo para
UTM (Universal Transversa de Mercator), onde 1 unidade = 1 metro.
"""
from pyproj import Transformer
from rasterio.features import geometry_mask
from shapely.geometry import Point
from shapely.ops import transform


def epsg_utm(lon: float, lat: float) -> int:
    """Descobre o código EPSG da zona UTM que contém o ponto.

    O mundo é dividido em 60 fatias de 6° de longitude. No hemisfério sul o
    código é 327xx; no norte, 326xx. Ex.: Belo Horizonte -> zona 23S -> 32723.
    """
    zona = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zona


def _transformador(origem: int, destino: int):
    # always_xy=True garante a ordem (x=lon, y=lat), evitando confusões.
    return Transformer.from_crs(origem, destino, always_xy=True).transform


def para_utm(geom_ll, epsg: int):
    return transform(_transformador(4326, epsg), geom_ll)


def para_latlon(geom_utm, epsg: int):
    return transform(_transformador(epsg, 4326), geom_utm)


def circulo_em_volta(lon: float, lat: float, raio_m: float, epsg: int):
    """Polígono provisório (círculo) para barragens sem polígono cadastrado."""
    centro_utm = para_utm(Point(lon, lat), epsg)
    return para_latlon(centro_utm.buffer(raio_m), epsg)


def bbox_latlon(geom_ll, epsg: int, buffer_m: float) -> list[float]:
    """Retângulo [oeste, sul, leste, norte] do polígono + margem em metros."""
    ampliado = para_utm(geom_ll, epsg).buffer(buffer_m)
    return list(para_latlon(ampliado, epsg).bounds)


def rasterizar(geom_utm, formato: tuple, affine):
    """Converte o polígono numa matriz True/False do mesmo tamanho da imagem.

    True = pixel dentro do polígono. 'affine' é a transformação que liga
    (linha, coluna) do pixel a (x, y) em metros.
    """
    return geometry_mask([geom_utm], out_shape=formato, transform=affine, invert=True)


def limites_latlon(affine, formato: tuple, epsg: int) -> list[list[float]]:
    """Cantos da imagem em lat/lon no formato que o Leaflet espera:
    [[sul, oeste], [norte, leste]]."""
    altura, largura = formato
    cantos = [affine * (0, 0), affine * (largura, 0),
              affine * (0, altura), affine * (largura, altura)]
    t = _transformador(epsg, 4326)
    lons, lats = zip(*(t(x, y) for x, y in cantos))
    return [[min(lats), min(lons)], [max(lats), max(lons)]]
