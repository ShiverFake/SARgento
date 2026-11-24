"""
Model: banco de dados SQLite

O SQLite é um banco de dados que vive num único arquivo (data/monitor.db).
Não precisa instalar servidor nenhum — o Python já vem com o módulo sqlite3.

Cada linha da tabela "observacoes" é UMA data de UMA barragem por UM sensor.
"""
import json
import sqlite3
from datetime import datetime

import pandas as pd

import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS observacoes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    barragem         TEXT NOT NULL,   -- slug da barragem
    data             TEXT NOT NULL,   -- AAAA-MM-DD
    sensor           TEXT NOT NULL,   -- 'S1' ou 'S2'
    orbita           TEXT,            -- ex.: 'descending/53' (só S1)
    limiar           REAL,            -- limiar usado (dB no S1, MNDWI no S2)
    limiar_otsu_bruto REAL,           -- limiar de Otsu antes de ser limitado
    area_poligono_ha REAL,
    area_lago_ha     REAL,
    area_praia_ha    REAL,
    cobertura_valida REAL,            -- fração de pixels válidos no polígono
    vv_medio_db      REAL,
    vh_medio_db      REAL,
    nuvem_poligono_pct REAL,          -- % de nuvem/sombra no polígono (só S2)
    vv_nucleo_db     REAL,            -- mediana do VV no núcleo de água permanente
    vv_nucleo_delta_db REAL,          -- diferença para a mediana da série
    suspeita_meteo   INTEGER,         -- 1 = suspeita de vento/chuva
    scl_agua_nucleo  REAL,            -- S2: fração do núcleo de água que a SCL chama de "água"
    nao_confiavel_scl INTEGER,        -- S2: 1 = fração acima < 50% da mediana da série
    mascara_png      TEXT,            -- nome do arquivo PNG da máscara
    limites          TEXT,            -- [[sul, oeste], [norte, leste]] em JSON
    processado_em    TEXT,
    UNIQUE (barragem, data, sensor)   -- impede duplicatas
);

-- Uma linha por data e por TRECHO de crista (mais uma linha "total").
CREATE TABLE IF NOT EXISTS crista_metricas (
    barragem        TEXT NOT NULL,
    data            TEXT NOT NULL,
    trecho          TEXT NOT NULL,
    comprimento_m   REAL,   -- comprimento do trecho
    dist_min_m      REAL,   -- menor distância água S1 -> crista (NULL se não há água)
    ext_30m_m       REAL,   -- extensão da crista com água a menos de 30 m
    ext_30m_pct     REAL,   -- idem, em % do comprimento
    UNIQUE (barragem, data, trecho)
);

-- Área S2 do lago com outros limiares MNDWI (faixa de incerteza).
CREATE TABLE IF NOT EXISTS s2_sensibilidade (
    barragem TEXT NOT NULL, data TEXT NOT NULL, limiar REAL NOT NULL,
    area_lago_ha REAL,
    UNIQUE (barragem, data, limiar)
);

-- Barragens de rejeito: polígono dividido em 3 classes (S1 + S2 a até 3 dias).
CREATE TABLE IF NOT EXISTS classes_superficie (
    barragem TEXT NOT NULL, data_s1 TEXT NOT NULL, data_s2 TEXT, dias INTEGER,
    lago_ha REAL,            -- água no S2
    praia_saturada_ha REAL,  -- escuro no S1 e não água no S2
    praia_seca_ha REAL,      -- o resto
    sem_dado_ha REAL,        -- nuvem/sem dado no S2
    UNIQUE (barragem, data_s1)
);
"""


def conectar() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(config.BANCO_SQLITE)


# Colunas criadas depois da primeira versão. Bancos antigos não as têm, então
# criar_tabelas() as acrescenta ("migração") sem apagar os dados existentes.
COLUNAS_NOVAS = {
    "nuvem_poligono_pct": "REAL",
    "vv_nucleo_db": "REAL",
    "vv_nucleo_delta_db": "REAL",
    "suspeita_meteo": "INTEGER",
    "scl_agua_nucleo": "REAL",
    "nao_confiavel_scl": "INTEGER",
}


def criar_tabelas() -> None:
    with conectar() as con:
        con.executescript(ESQUEMA)
        # PRAGMA table_info lista as colunas; o nome está na posição 1
        existentes = {linha[1] for linha in con.execute("PRAGMA table_info(observacoes)")}
        for nome, tipo in COLUNAS_NOVAS.items():
            if nome not in existentes:
                con.execute(f"ALTER TABLE observacoes ADD COLUMN {nome} {tipo}")


def existe(barragem: str, data: str, sensor: str, exigir_coluna: str = None) -> bool:
    """True se a observação já está no banco.

    Com exigir_coluna, só conta como existente se essa coluna estiver
    preenchida (útil para reprocessar linhas antigas sem o dado novo).
    """
    sql = "SELECT 1 FROM observacoes WHERE barragem=? AND data=? AND sensor=?"
    if exigir_coluna:
        sql += f" AND {exigir_coluna} IS NOT NULL"
    with conectar() as con:
        return con.execute(sql, (barragem, data, sensor)).fetchone() is not None


def salvar_observacao(obs: dict) -> None:
    """Insere (ou substitui, se já existir) uma observação."""
    obs = dict(obs)
    obs["processado_em"] = datetime.now().isoformat(timespec="seconds")
    if isinstance(obs.get("limites"), (list, tuple)):
        obs["limites"] = json.dumps(obs["limites"])

    colunas = ", ".join(obs.keys())
    marcadores = ", ".join("?" for _ in obs)  # "?" evita SQL injection
    with conectar() as con:
        con.execute(
            f"INSERT OR REPLACE INTO observacoes ({colunas}) VALUES ({marcadores})",
            list(obs.values()),
        )


def orbita_predominante(barragem: str):
    """Órbita S1 mais usada na série da barragem (ex.: 'descending/155'), ou None."""
    with conectar() as con:
        linha = con.execute(
            "SELECT orbita, COUNT(*) AS n FROM observacoes "
            "WHERE barragem=? AND sensor='S1' AND orbita IS NOT NULL "
            "GROUP BY orbita ORDER BY n DESC LIMIT 1", (barragem,)).fetchone()
    return linha[0] if linha else None


def atualizar_campos(barragem: str, data: str, sensor: str, campos: dict) -> None:
    """Altera algumas colunas de uma observação que já existe."""
    atribuicoes = ", ".join(f"{c}=?" for c in campos)
    with conectar() as con:
        con.execute(
            f"UPDATE observacoes SET {atribuicoes} WHERE barragem=? AND data=? AND sensor=?",
            [*campos.values(), barragem, data, sensor],
        )


def salvar_metricas_crista(barragem: str, linhas: list[dict]) -> None:
    """Substitui todas as métricas de crista da barragem (recalculadas juntas)."""
    with conectar() as con:
        con.execute("DELETE FROM crista_metricas WHERE barragem=?", (barragem,))
        con.executemany(
            "INSERT INTO crista_metricas (barragem, data, trecho, comprimento_m, dist_min_m, "
            "ext_30m_m, ext_30m_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(barragem, l["data"], l["trecho"], l["comprimento_m"], l["dist_min_m"],
              l["ext_30m_m"], l["ext_30m_pct"]) for l in linhas])


def obter_metricas_crista(barragem: str) -> pd.DataFrame:
    with conectar() as con:
        df = pd.read_sql_query(
            "SELECT * FROM crista_metricas WHERE barragem=? ORDER BY data, trecho",
            con, params=(barragem,))
    df["data"] = pd.to_datetime(df["data"])
    return df


def substituir_tabela(tabela: str, barragem: str, linhas: list[dict]) -> None:
    """Apaga as linhas da barragem numa tabela auxiliar e grava as novas."""
    with conectar() as con:
        con.execute(f"DELETE FROM {tabela} WHERE barragem=?", (barragem,))
        if linhas:
            cols = ["barragem", *linhas[0].keys()]
            con.executemany(
                f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [[barragem, *l.values()] for l in linhas])


def ler_tabela(tabela: str, barragem: str, ordem: str) -> pd.DataFrame:
    with conectar() as con:
        df = pd.read_sql_query(f"SELECT * FROM {tabela} WHERE barragem=? ORDER BY {ordem}",
                               con, params=(barragem,))
    for col in ("data", "data_s1", "data_s2"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
    return df


def obter_serie(barragem: str, sensor: str = "S1") -> pd.DataFrame:
    """Devolve a série temporal como DataFrame do pandas, ordenada por data."""
    with conectar() as con:
        df = pd.read_sql_query(
            "SELECT * FROM observacoes WHERE barragem=? AND sensor=? ORDER BY data",
            con, params=(barragem, sensor),
        )
    df["data"] = pd.to_datetime(df["data"])
    return df
