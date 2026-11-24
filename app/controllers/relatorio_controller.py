"""
Controller: relatórios HTML

Gera um arquivo HTML autocontido (abre sem internet e sem o servidor) em
reports/relatorio_<barragem>_<data>.html, com:
  - resumo da série
  - gráfico simples (SVG desenhado aqui mesmo em Python)
  - tabela das variações anômalas
  - comparação S1 x S2 (se houver)
"""
import base64
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

import config
from app.controllers.servico_series import contagem_alertas, eventos_alerta, montar_series, ocorrencias
from app.models import referencias
from app.models.sentinel2 import s2_confiavel
from app.models.barragem import Barragem
from app.views.formatacao import num_br

PASTA_TEMPLATES = Path(__file__).resolve().parent.parent / "views" / "templates"


def _svg_serie(s1: pd.DataFrame, s2: pd.DataFrame, largura=860, altura=260) -> Markup:
    """Desenha um gráfico de linha em SVG (formato de imagem vetorial em texto)."""
    if s1.empty:
        return Markup("<p>Sem dados.</p>")
    m = 40  # margem em pixels
    datas = pd.concat([s1["data"], s2["data"]]) if not s2.empty else s1["data"]
    t0, t1 = datas.min().value, datas.max().value
    valores = pd.concat([s1["area_lago_ha"], s2["area_lago_ha"]]) if not s2.empty else s1["area_lago_ha"]
    v0, v1 = float(valores.min()), float(valores.max())
    if v1 == v0:
        v0, v1 = v0 - 1, v1 + 1

    def x(d):  # data -> coordenada horizontal
        return m + (d.value - t0) / max(t1 - t0, 1) * (largura - 2 * m)

    def y(v):  # área -> coordenada vertical (SVG cresce para baixo!)
        return altura - m - (v - v0) / (v1 - v0) * (altura - 2 * m)

    # A linha liga só as passagens confiáveis; as suspeitas aparecem como "×".
    confiaveis = s1[~s1["suspeita"]]
    pontos = " ".join(f"{x(d):.1f},{y(v):.1f}"
                      for d, v in zip(confiaveis["data"], confiaveis["area_lago_ha"]))
    partes = [f'<svg viewBox="0 0 {largura} {altura}" width="100%" role="img" '
              f'aria-label="Série da área do lago">',
              f'<line x1="{m}" y1="{altura-m}" x2="{largura-m}" y2="{altura-m}" stroke="#999"/>',
              f'<line x1="{m}" y1="{m}" x2="{m}" y2="{altura-m}" stroke="#999"/>',
              f'<text x="4" y="{m}" font-size="11">{num_br(v1, 1)} ha</text>',
              f'<text x="4" y="{altura-m}" font-size="11">{num_br(v0, 1)} ha</text>',
              f'<text x="{m}" y="{altura-10}" font-size="11">{datas.min():%d/%m/%Y}</text>',
              f'<text x="{largura-m}" y="{altura-10}" font-size="11" text-anchor="end">{datas.max():%d/%m/%Y}</text>',
              f'<polyline points="{pontos}" fill="none" stroke="#1e78c8" stroke-width="2"/>']
    for _, r in s1[s1["anomalia"]].iterrows():
        # Anomalia da fonte principal = círculo vermelho; da outra fonte = vazado cinza
        estilo = ('fill="#d7263d"' if r["confiabilidade"] == "principal"
                  else 'fill="none" stroke="#9aa3ad" stroke-width="1.5"')
        partes.append(f'<circle cx="{x(r["data"]):.1f}" cy="{y(r["area_lago_ha"]):.1f}" r="5" '
                      f'{estilo}><title>{r["data"]:%d/%m/%Y} (S1): {r["motivo"]}</title></circle>')
    if "anomalia" in s2.columns:
        for _, r in s2[s2["anomalia"].fillna(False).astype(bool)].iterrows():
            cx, cy = x(r["data"]), y(r["area_lago_ha"])
            estilo = ('fill="#d7263d"' if r["confiabilidade"] == "principal"
                      else 'fill="none" stroke="#9aa3ad" stroke-width="1.5"')
            partes.append(f'<polygon points="{cx:.1f},{cy-7:.1f} {cx+7:.1f},{cy:.1f} {cx:.1f},{cy+7:.1f} '
                          f'{cx-7:.1f},{cy:.1f}" {estilo}><title>{r["data"]:%d/%m/%Y} (S2): '
                          f'{r["motivo"]}</title></polygon>')
    for _, r in s1[s1["retorno"]].iterrows():
        partes.append(f'<circle cx="{x(r["data"]):.1f}" cy="{y(r["area_lago_ha"]):.1f}" r="5" '
                      f'fill="none" stroke="#7b4fb3" stroke-width="2">'
                      f'<title>{r["data"]:%d/%m/%Y}: {r["motivo"]}</title></circle>')
    for _, r in s1[s1["suspeita"]].iterrows():
        cx, cy = x(r["data"]), y(r["area_lago_ha"])
        partes.append(f'<g stroke="#6b7280" stroke-width="2"><title>{r["data"]:%d/%m/%Y}: '
                      f'{r["motivo"]}</title><line x1="{cx-5:.1f}" y1="{cy-5:.1f}" x2="{cx+5:.1f}" '
                      f'y2="{cy+5:.1f}"/><line x1="{cx-5:.1f}" y1="{cy+5:.1f}" x2="{cx+5:.1f}" '
                      f'y2="{cy-5:.1f}"/></g>')
    for _, r in s2.iterrows():
        # Quadrado cheio = S2 confiável; vazado = nuvem demais ou reprovado na SCL
        motivo = r.get("motivo_nao_confiavel") or ""
        preenchimento = 'fill="#2a9d8f"' if not motivo else 'fill="none" stroke="#2a9d8f" stroke-width="1.5"'
        partes.append(f'<rect x="{x(r["data"])-4:.1f}" y="{y(r["area_lago_ha"])-4:.1f}" '
                      f'width="8" height="8" {preenchimento}><title>S2 {r["data"]:%d/%m/%Y}: '
                      f'{num_br(r["area_lago_ha"])} ha, nuvem {num_br(r.get("nuvem_poligono_pct"), 1)}%'
                      f'{" — não confiável (" + motivo + ")" if motivo else ""}</title></rect>')
    partes.append("</svg>")
    return Markup("".join(partes))


CORES_TRECHO = ["#d7263d", "#1e78c8", "#2a9d8f", "#7b4fb3", "#c9a227"]


def _svg_crista(crista: pd.DataFrame, largura=860, altura=230) -> Markup:
    """Gráfico SVG: % da crista com água a < 30 m, uma linha por trecho."""
    trechos = [t for t in crista["trecho"].unique() if t != "total"]
    if not trechos:
        return Markup("")
    m = 40
    t0, t1 = crista["data"].min().value, crista["data"].max().value
    x = lambda d: m + (d.value - t0) / max(t1 - t0, 1) * (largura - 2 * m)
    y = lambda v: altura - m - v / 100 * (altura - 2 * m)
    partes = [f'<svg viewBox="0 0 {largura} {altura}" width="100%" role="img" '
              f'aria-label="Extensão da crista com água próxima">',
              f'<line x1="{m}" y1="{altura-m}" x2="{largura-m}" y2="{altura-m}" stroke="#999"/>',
              f'<line x1="{m}" y1="{m}" x2="{m}" y2="{altura-m}" stroke="#999"/>',
              f'<text x="4" y="{m+4}" font-size="11">100%</text>',
              f'<text x="4" y="{altura-m}" font-size="11">0%</text>',
              f'<text x="{m}" y="{altura-10}" font-size="11">{crista["data"].min():%d/%m/%Y}</text>',
              f'<text x="{largura-m}" y="{altura-10}" font-size="11" text-anchor="end">'
              f'{crista["data"].max():%d/%m/%Y}</text>']
    for i, tr in enumerate(trechos):
        sub = crista[crista["trecho"] == tr]
        pts = " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in zip(sub["data"], sub["ext_30m_pct"]))
        cor = CORES_TRECHO[i % len(CORES_TRECHO)]
        partes.append(f'<polyline points="{pts}" fill="none" stroke="{cor}" stroke-width="2"/>')
        partes.append(f'<text x="{largura-m-4}" y="{m + 14*i + 4}" font-size="11" fill="{cor}" '
                      f'text-anchor="end">{tr}</text>')
    partes.append("</svg>")
    return Markup("".join(partes))


def _resumo_crista(crista: pd.DataFrame) -> list[dict]:
    """Uma linha por trecho: comprimento, última data e extremos do período."""
    saida = []
    for tr in [t for t in crista["trecho"].unique() if t != "total"] + ["total"]:
        sub = crista[crista["trecho"] == tr].sort_values("data")
        if sub.empty:
            continue
        ult = sub.iloc[-1]
        saida.append({"trecho": tr, "comprimento_m": ult["comprimento_m"], "ultima_data": ult["data"],
                      "ult_pct": ult["ext_30m_pct"], "ult_m": ult["ext_30m_m"], "ult_dist": ult["dist_min_m"],
                      "min_pct": sub["ext_30m_pct"].min(), "max_pct": sub["ext_30m_pct"].max(),
                      "med_pct": sub["ext_30m_pct"].median(),
                      "dist_min_periodo": sub["dist_min_m"].min(),
                      "datas_sem_agua": int(sub["dist_min_m"].isna().sum())})
    return saida


def _referencias_embutidas(slug: str) -> list[dict]:
    """Lê os painéis de referência e embute cada PNG como base64 (relatório autocontido)."""
    saida = []
    for r in referencias.carregar(slug):
        r = dict(r)
        caminho = config.PASTA_REFERENCIAS / r["arquivo"] if r.get("arquivo") else None
        if caminho and caminho.exists():
            r["img"] = "data:image/png;base64," + base64.b64encode(caminho.read_bytes()).decode()
        saida.append(r)
    return saida


def _svg_classes(classes: pd.DataFrame, largura=860, altura=230) -> Markup:
    """Gráfico SVG de áreas empilhadas: lago, praia saturada e praia seca."""
    if classes.empty:
        return Markup("")
    m = 40
    t0, t1 = classes["data_s1"].min().value, classes["data_s1"].max().value
    topo = float((classes["lago_ha"] + classes["praia_saturada_ha"] + classes["praia_seca_ha"]).max()) or 1
    x = lambda d: m + (d.value - t0) / max(t1 - t0, 1) * (largura - 2 * m)
    y = lambda v: altura - m - v / topo * (altura - 2 * m)
    partes = [f'<svg viewBox="0 0 {largura} {altura}" width="100%" role="img" aria-label="Classes da superfície">',
              f'<text x="4" y="{m+4}" font-size="11">{num_br(topo, 0)} ha</text>',
              f'<text x="4" y="{altura-m}" font-size="11">0</text>',
              f'<text x="{m}" y="{altura-10}" font-size="11">{classes["data_s1"].min():%d/%m/%Y}</text>',
              f'<text x="{largura-m}" y="{altura-10}" font-size="11" text-anchor="end">'
              f'{classes["data_s1"].max():%d/%m/%Y}</text>']
    base = np.zeros(len(classes))
    for col, cor in [("lago_ha", "#1e78c8"), ("praia_saturada_ha", "#8a5a2b"), ("praia_seca_ha", "#d9b44a")]:
        topo_c = base + classes[col].to_numpy()
        cima = [f"{x(d):.1f},{y(v):.1f}" for d, v in zip(classes["data_s1"], topo_c)]
        baixo = [f"{x(d):.1f},{y(v):.1f}" for d, v in zip(classes["data_s1"][::-1], base[::-1])]
        partes.append(f'<polygon points="{" ".join(cima + baixo)}" fill="{cor}" fill-opacity="0.75"/>')
        base = topo_c
    partes.append("</svg>")
    return Markup("".join(partes))


def _resumo_sensibilidade(sens: pd.DataFrame, s2: pd.DataFrame) -> list[dict]:
    """Mediana e extremos da área S2 por limiar, só nas datas S2 confiáveis."""
    if sens.empty:
        return []
    confiaveis = set(s2.loc[s2_confiavel(s2), "data"])
    sens = sens[sens["data"].isin(confiaveis)]
    return [{"limiar": lim, "n": len(g), "mediana": g["area_lago_ha"].median(),
             "minimo": g["area_lago_ha"].min(), "maximo": g["area_lago_ha"].max()}
            for lim, g in sens.groupby("limiar")]


def _notas(barragem: Barragem) -> tuple[list, list, dict]:
    """Limitações, eventos e metodologia registrados em data/notas.json."""
    caminho = config.DATA_DIR / "notas.json"
    if not caminho.exists():
        return [], [], {}
    notas = json.loads(caminho.read_text(encoding="utf-8"))
    geral, propria = notas.get("todas", {}), notas.get(barragem.nome, {})
    return (propria.get("limitacoes", []) + geral.get("limitacoes", []),
            propria.get("eventos", []), notas.get("metodologia", {}))


def gerar_relatorio(barragem: Barragem) -> Path:
    series = montar_series(barragem.slug)
    s1, s2, validacao = series["s1"], series["s2"], series["validacao"]
    # Ocorrências = tudo que não é "normal" (anomalia, retorno, suspeita)
    lista_ocorrencias = ocorrencias(series)        # S1 e S2, com a confiabilidade
    n_anomalias = series["n_anomalias"]            # só a fonte principal
    n_suspeitas = int(s1["suspeita"].sum()) if not s1.empty else 0

    env = Environment(loader=FileSystemLoader(PASTA_TEMPLATES),
                      autoescape=select_autoescape(["html"]))
    # Filtro "num": no template, {{ valor|num(1) }} vira "1.234,5"
    env.filters["num"] = num_br
    html = env.get_template("relatorio.html").render(
        barragem=barragem,
        gerado_em=datetime.now().strftime("%d/%m/%Y %H:%M"),
        s1=s1, s2=s2, ocorrencias=lista_ocorrencias, validacao=validacao,
        contagem=contagem_alertas(series), eventos_alerta=eventos_alerta(series),
        fonte_alertas=series["fonte_alertas"],
        n_anomalias=n_anomalias, n_suspeitas=n_suspeitas,
        erro_medio=series["erro_medio_abs_pct"], vies_medio=series["vies_medio_pct"],
        erro_medio_ha=series["erro_medio_abs_ha"], vies_medio_ha=series["vies_medio_ha"],
        n_validas=series["n_validas"], n_validas_pct=series["n_validas_pct"],
        classes=series["classes"], classes_svg=_svg_classes(series["classes"]),
        sensibilidade=_resumo_sensibilidade(series["sensibilidade"], s2),
        limitacoes=_notas(barragem)[0], eventos=_notas(barragem)[1], metodologia=_notas(barragem)[2],
        crista_svg=_svg_crista(series["crista"]) if not series["crista"].empty else None,
        crista_resumo=_resumo_crista(series["crista"]) if not series["crista"].empty else [],
        referencias=_referencias_embutidas(barragem.slug),
        grafico=_svg_serie(s1, s2),
        config=config,
    )

    config.PASTA_RELATORIOS.mkdir(parents=True, exist_ok=True)
    caminho = config.PASTA_RELATORIOS / f"relatorio_{barragem.slug}_{datetime.now():%Y%m%d}.html"
    caminho.write_text(html, encoding="utf-8")
    return caminho
