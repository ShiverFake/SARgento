"""
Funções compartilhadas pelos controllers web e de relatório:
juntam série S1 + anomalias + S2 + comparação num só pacote.

Fonte dos alertas: cada barragem define (coluna fonte_alertas do sigbm.csv)
qual série gera os alertas "de verdade":
  - S1: radar, passa sempre, mas em barragem de rejeito mede água + rejeito saturado;
  - S2: óptico, mede o lago, mas só nas datas sem nuvem.
As anomalias da OUTRA série continuam visíveis, marcadas como
"baixa confiabilidade", e ficam fora da contagem do cabeçalho.
"""
import json

import numpy as np
import pandas as pd

import config
from app.models import database
from app.models.anomalias import detectar_anomalias
from app.models.barragem import buscar_por_slug
from app.models.sentinel2 import (comparar_s1_s2, estatisticas_validacao, motivo_nao_confiavel,
                                  s2_confiavel)
from app.views.formatacao import ROTULOS_STATUS, texto_motivo

COLUNAS_ALERTA = ["status", "status_texto", "anomalia", "retorno", "motivo", "mediana_movel",
                  "z", "var_pct", "var_ha", "confiabilidade", "fonte", "revisao"]


def _revisoes(nome: str) -> dict:
    """Revisões manuais de alertas (data/revisoes.json): {(data, fonte): texto}.

    Usadas quando a conferência visual (RGB) mostra que um alerta é provável
    falso positivo. O alerta continua listado, com a revisão, mas sai da contagem.
    """
    caminho = config.DATA_DIR / "revisoes.json"
    if not caminho.exists():
        return {}
    todas = json.loads(caminho.read_text(encoding="utf-8"))
    return {(r["data"], r["fonte"]): f"{r['classificacao']} — {r['motivo']} (revisado em {r['revisado_em']})"
            for r in todas.get(nome, [])}


def _marcar(df: pd.DataFrame, fonte: str, principal: str, revisoes: dict) -> pd.DataFrame:
    df["motivo"] = [texto_motivo(linha) for linha in df.itertuples()]
    df["status_texto"] = df["status"].map(ROTULOS_STATUS)
    df["fonte"] = fonte
    df["confiabilidade"] = "principal" if fonte == principal else "baixa"
    df["revisao"] = [revisoes.get((d.strftime("%Y-%m-%d"), fonte)) for d in df["data"]]
    revisada = df["revisao"].notna() & df["anomalia"].astype(bool)
    df.loc[revisada, "confiabilidade"] = "revisada"   # fora da contagem
    return df


def montar_series(slug: str) -> dict:
    barragem = buscar_por_slug(slug)
    principal = barragem.fonte_alertas if barragem else "S1"
    ruido = barragem.ruido_ha if barragem else None
    revisoes = _revisoes(barragem.nome) if barragem else {}

    s1 = database.obter_serie(slug, "S1")
    s2 = database.obter_serie(slug, "S2")
    if not s1.empty:
        s1 = _marcar(detectar_anomalias(s1, ruido_ha=ruido), "S1", principal, revisoes)

    # Anomalias do S2: só nas datas confiáveis (pouca nuvem E aprovadas na SCL)
    confiavel = s2_confiavel(s2)
    s2_alertas = pd.DataFrame()
    if not s2.empty and confiavel.any():
        s2_alertas = _marcar(detectar_anomalias(s2[confiavel].copy(), ruido_ha=ruido), "S2", principal, revisoes)
        s2 = s2.merge(s2_alertas[["data"] + COLUNAS_ALERTA], on="data", how="left")

    if not s2.empty:
        s2["motivo_nao_confiavel"] = [motivo_nao_confiavel(l) for _, l in s2.iterrows()]

    n_anomalias = 0
    for df, fonte in ((s1, "S1"), (s2_alertas, "S2")):
        if fonte == principal and not df.empty:
            # só anomalias da fonte principal e NÃO revisadas como falso positivo
            n_anomalias = int((df["anomalia"].astype(bool) & (df["confiabilidade"] == "principal")).sum())

    validacao = comparar_s1_s2(s1, s2)
    return {"s1": s1, "s2": s2, "validacao": validacao, **estatisticas_validacao(validacao),
            "fonte_alertas": principal, "n_anomalias": n_anomalias,
            "nome": barragem.nome if barragem else slug, "ruido_ha": ruido,
            "crista": database.obter_metricas_crista(slug),
            "sensibilidade": database.ler_tabela("s2_sensibilidade", slug, "data, limiar"),
            "classes": database.ler_tabela("classes_superficie", slug, "data_s1")}


def contagem_alertas(series: dict) -> dict:
    """Alertas (status 'anomalia') separados pelo motivo de contarem ou não."""
    oc = ocorrencias(series)
    an = oc[oc["status"] == "anomalia"] if not oc.empty else oc
    return {"contam": int((an["confiabilidade"] == "principal").sum()) if len(an) else 0,
            "revisados": int((an["confiabilidade"] == "revisada").sum()) if len(an) else 0,
            "baixa": int((an["confiabilidade"] == "baixa").sum()) if len(an) else 0}


def eventos_alerta(series: dict) -> pd.DataFrame:
    """Agrupa alertas CONSECUTIVOS da fonte principal em eventos.

    Consecutivos = sem nenhuma passagem normal entre eles na série da fonte
    principal (S1: todas as passagens fora das suspeitas de vento/chuva;
    S2: só as datas confiáveis). Ex.: o enchimento do lago oeste gerou alertas
    em 4 passagens seguidas -> 1 evento com 4 datas.
    """
    colunas = ["inicio", "fim", "n_datas", "datas", "area_antes_ha", "area_fim_ha",
               "variacao_ha", "area_min_ha", "area_max_ha", "z_max", "fonte",
               "area_depois_ha", "sentido", "tipo", "principal", "anotacao"]
    fonte = series["fonte_alertas"]
    df = series["s1"] if fonte == "S1" else series["s2"]
    if df.empty or "status" not in df.columns:
        return pd.DataFrame(columns=colunas)
    df = df[df["status"].notna() & (df["status"] != "suspeita")].sort_values("data").reset_index(drop=True)
    conta = (df["status"] == "anomalia") & (df["confiabilidade"] == "principal")

    grupos, atual = [], None
    for i in df.index:
        if conta[i]:
            if atual is None:
                atual = {"linhas": [], "antes": df.at[i - 1, "area_lago_ha"] if i > 0 else np.nan}
                grupos.append(atual)
            atual["linhas"].append(i)
        else:
            atual = None   # uma passagem que não é alerta encerra o evento

    anotacoes = _anotacoes_eventos(series.get("nome"))
    saida = []
    for g in grupos:
        sub = df.loc[g["linhas"]]
        fim = sub["area_lago_ha"].iloc[-1]
        ultimo = g["linhas"][-1]
        depois = df.at[ultimo + 1, "area_lago_ha"] if ultimo + 1 in df.index else np.nan
        variacao = fim - g["antes"] if pd.notna(g["antes"]) else np.nan

        # Oscilação de uma data: um único alerta e, na passagem seguinte, a área
        # volta: fica a menos do ruído/limite de variação do nível de antes, OU
        # fica mais perto do nível de antes do que do valor do alerta
        # (ex.: 22,7 -> 36,9 -> 14,0 ha: subiu e desceu de novo).
        antes = g["antes"]
        tolerancia = max(series.get("ruido_ha") or 0,
                         config.LIMIAR_VARIACAO_MIN_HA if (pd.notna(antes) and antes >= config.AREA_MIN_PCT_HA)
                         else config.LIMIAR_VARIACAO_ABS_HA)
        voltou = (pd.notna(depois) and pd.notna(antes)
                  and (abs(depois - antes) <= tolerancia or abs(depois - antes) < abs(depois - fim)))
        tipo = "oscilação de uma data" if (len(sub) == 1 and voltou) else "sustentado"

        inicio = sub["data"].iloc[0]
        saida.append({"inicio": inicio, "fim": sub["data"].iloc[-1], "n_datas": len(sub),
                      "datas": ", ".join(f"{d:%d/%m/%Y}" for d in sub["data"]),
                      "area_antes_ha": antes, "area_fim_ha": fim, "variacao_ha": variacao,
                      "area_min_ha": sub["area_lago_ha"].min(), "area_max_ha": sub["area_lago_ha"].max(),
                      "z_max": sub["z"].abs().max(), "fonte": fonte, "area_depois_ha": depois,
                      "sentido": "" if pd.isna(variacao) else ("aumento" if variacao > 0 else "redução"),
                      "tipo": tipo, "principal": False,
                      "anotacao": anotacoes.get(inicio.strftime("%Y-%m-%d"), "")})
    ev = pd.DataFrame(saida, columns=colunas)
    # Principal = o evento sustentado com a maior variação absoluta
    sust = ev[ev["tipo"] == "sustentado"]
    if not sust.empty:
        ev.loc[sust["variacao_ha"].abs().idxmax(), "principal"] = True
    return ev


def _anotacoes_eventos(nome) -> dict:
    """Textos de data/notas.json -> "anotacoes_eventos" -> nome -> {data_inicio: texto}."""
    caminho = config.DATA_DIR / "notas.json"
    if not nome or not caminho.exists():
        return {}
    return json.loads(caminho.read_text(encoding="utf-8")).get("anotacoes_eventos", {}).get(nome, {})


def ocorrencias(series: dict) -> pd.DataFrame:
    """Tudo que não é "normal", das duas fontes, em ordem de data."""
    partes = []
    for chave, area in (("s1", "area_lago_ha"), ("s2", "area_lago_ha")):
        df = series[chave]
        if df.empty or "status" not in df.columns:
            continue
        sel = df[df["status"].notna() & (df["status"] != "normal")]
        partes.append(sel[["data", area, "vv_nucleo_db"] + COLUNAS_ALERTA].rename(columns={area: "area_ha"}))
    if not partes:
        return pd.DataFrame(columns=["data", "area_ha", "vv_nucleo_db"] + COLUNAS_ALERTA)
    return pd.concat(partes).sort_values(["data", "fonte"]).reset_index(drop=True)


def df_para_registros(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> lista de dicionários prontos para JSON.

    O JSON não aceita NaN, então trocamos NaN por None (vira null).
    """
    if df.empty:
        return []
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
    registros = df.astype(object).where(pd.notna(df), None).to_dict(orient="records")
    for r in registros:
        for k, v in r.items():
            if isinstance(v, (np.floating, np.integer)):
                r[k] = v.item()
            elif isinstance(v, np.bool_):
                r[k] = bool(v)
        if isinstance(r.get("limites"), str):
            r["limites"] = json.loads(r["limites"])
    return registros
