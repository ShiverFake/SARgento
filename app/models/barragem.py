"""
Model: Barragem

Lê a lista de barragens (data/sigbm.csv) e associa cada uma ao polígono do
seu reservatório (data/poligonos.geojson). A ligação é feita pelo campo
"nome", que precisa ser IGUAL nos dois arquivos.
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

import config


def slugify(texto: str) -> str:
    """Transforma 'Barragem São João I' em 'barragem-sao-joao-i'.

    O "slug" é um identificador sem acentos nem espaços, seguro para usar
    em URLs e nomes de arquivo.
    """
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


# @dataclass gera automaticamente o __init__ a partir dos campos abaixo.
@dataclass
class Barragem:
    nome: str
    lat: float
    lon: float
    categoria_risco: str
    poligono: Optional[BaseGeometry] = None  # geometria em lat/lon (EPSG:4326)
    # ---- colunas OPCIONAIS do CSV (podem ficar vazias) ----
    dano_potencial: Optional[str] = None     # DPA do SIGBM
    situacao: Optional[str] = None           # texto livre (operação, método etc.)
    orbita: Optional[str] = None             # força a órbita S1, ex.: "descending/126"
    mndwi_otsu_min: Optional[float] = None   # se preenchidos, o S2 usa Otsu no MNDWI
    mndwi_otsu_max: Optional[float] = None   #   limitado a [min, max] (em vez do limiar fixo)
    serie_principal: str = "area"            # "area" ou "crista": qual gráfico vem primeiro
    observacao: Optional[str] = None         # aparece no relatório
    tipo: str = "lago"                       # "rejeito" ou "lago" (muda rótulos e análises)
    mndwi_limiar: Optional[float] = None     # limiar MNDWI FIXO desta barragem (calibrado)
    mndwi_incerteza: list = field(default_factory=list)  # limiares para a faixa de incerteza
    cristas: dict = field(default_factory=dict)  # {trecho: LineString lat/lon}
    fonte_alertas: str = "S1"                # série que gera os alertas: "S1" ou "S2"
    ruido_ha: Optional[float] = None         # ruído da medição: nenhum alerta abaixo dele
    papel: str = "monitorada"                # "monitorada" ou "controle" (calibração do método)
    crista_interpretavel: bool = True        # False = métrica calculada mas não interpretável
    crista_nota: Optional[str] = None        # motivo, quando não interpretável

    @property
    def slug(self) -> str:
        return slugify(self.nome)

    @property
    def rejeito(self) -> bool:
        return self.tipo == "rejeito"

    @property
    def faixa_otsu_mndwi(self):
        """(min, max) se o S2 deve usar Otsu no MNDWI; senão None."""
        if self.mndwi_otsu_min is not None and self.mndwi_otsu_max is not None:
            return (self.mndwi_otsu_min, self.mndwi_otsu_max)
        return None


def _carregar_poligonos(caminho) -> dict:
    """Devolve um dicionário {nome: geometria shapely}."""
    if not caminho.exists():
        return {}
    with open(caminho, encoding="utf-8") as f:
        geojson = json.load(f)
    poligonos = {}
    for feature in geojson.get("features", []):
        nome = feature.get("properties", {}).get("nome")
        if nome:
            # shape() converte o dicionário GeoJSON em objeto shapely
            poligonos[nome.strip()] = shape(feature["geometry"])
    return poligonos


def _carregar_cristas(caminho) -> dict:
    """Devolve {nome_da_barragem: {trecho: LineString}} a partir do GeoJSON.

    Cada linha é a BORDA DE MONTANTE da crista (o lado do reservatório).
    Uma barragem pode ter vários trechos (ex.: "reto norte" e "dique noroeste").
    """
    if not caminho.exists():
        return {}
    with open(caminho, encoding="utf-8") as f:
        geojson = json.load(f)
    cristas = {}
    for feature in geojson.get("features", []):
        p = feature.get("properties", {})
        if p.get("nome"):
            c = cristas.setdefault(p["nome"].strip(), {"linhas": {}, "interpretavel": True, "nota": None})
            c["linhas"][p.get("trecho", "crista")] = shape(feature["geometry"])
            # Basta um trecho marcado como não interpretável para valer para a barragem
            if p.get("interpretavel") is False:
                c["interpretavel"], c["nota"] = False, p.get("nota")
    return cristas


def _texto(valor):
    """Célula vazia do CSV (NaN) vira None; texto é aparado."""
    return None if pd.isna(valor) or str(valor).strip() == "" else str(valor).strip()


def _numero(valor):
    return None if _texto(valor) is None else float(valor)


def carregar_barragens(csv=config.CSV_BARRAGENS,
                       geojson=config.GEOJSON_POLIGONOS,
                       cristas_geojson=config.GEOJSON_CRISTAS) -> list[Barragem]:
    """Lê o CSV e devolve uma lista de objetos Barragem."""
    # keep_default_na=False: textos como "N/A" ficam como texto (o pandas os
    # trataria como "sem dado"); só células realmente vazias viram vazio.
    df = pd.read_csv(csv, encoding="utf-8", dtype=str, keep_default_na=False, na_values=[""])
    df.columns = [c.strip().lower() for c in df.columns]

    obrigatorias = {"nome", "lat", "lon", "categoria_risco"}
    faltando = obrigatorias - set(df.columns)
    if faltando:
        raise ValueError(f"Colunas faltando em {csv.name}: {faltando}")

    poligonos = _carregar_poligonos(geojson)
    cristas = _carregar_cristas(cristas_geojson)
    barragens = []
    for _, linha in df.iterrows():
        nome = str(linha["nome"]).strip()
        opcional = lambda col: linha[col] if col in df.columns else None
        barragens.append(Barragem(
            nome=nome,
            lat=float(linha["lat"]),
            lon=float(linha["lon"]),
            categoria_risco=str(linha["categoria_risco"]).strip(),
            poligono=poligonos.get(nome),
            dano_potencial=_texto(opcional("dano_potencial")),
            situacao=_texto(opcional("situacao")),
            orbita=_texto(opcional("orbita")),
            mndwi_otsu_min=_numero(opcional("mndwi_otsu_min")),
            mndwi_otsu_max=_numero(opcional("mndwi_otsu_max")),
            serie_principal=_texto(opcional("serie_principal")) or "area",
            observacao=_texto(opcional("observacao")),
            tipo=_texto(opcional("tipo")) or "lago",
            fonte_alertas=(_texto(opcional("fonte_alertas")) or "S1").upper(),
            ruido_ha=_numero(opcional("ruido_ha")),
            papel=_texto(opcional("papel")) or "monitorada",
            mndwi_limiar=_numero(opcional("mndwi_limiar")),
            mndwi_incerteza=[float(v) for v in (_texto(opcional("mndwi_incerteza")) or "").split(";") if v.strip()],
            cristas=cristas.get(nome, {}).get("linhas", {}),
            crista_interpretavel=cristas.get(nome, {}).get("interpretavel", True),
            crista_nota=cristas.get(nome, {}).get("nota"),
        ))
    return barragens


def buscar_por_slug(slug: str) -> Optional[Barragem]:
    for b in carregar_barragens():
        if b.slug == slug:
            return b
    return None
