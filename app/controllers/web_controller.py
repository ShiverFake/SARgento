"""
Controller: rotas web (Flask)

Uma "rota" liga um endereço (URL) a uma função Python.
  /                                 -> início: as estruturas em cards
  /mapa                             -> monitoramento (mapa + painel), sem seleção
  /estrutura/<slug>                 -> monitoramento já com a estrutura escolhida
  /metodo                           -> sobre o método
  /api/barragens                    -> lista de estruturas (JSON)
  /api/barragens/<slug>/serie       -> série temporal + anomalias (JSON)
  /api/barragens/<slug>/relatorio   -> gera o relatório HTML e abre
  /mascaras/<arquivo>               -> imagens PNG das máscaras
"""
import json

from flask import Blueprint, abort, jsonify, render_template, send_from_directory
from shapely.geometry import mapping

import config
from app.controllers import relatorio_controller
from app.controllers.servico_series import (contagem_alertas, df_para_registros, eventos_alerta,
                                            montar_series, ocorrencias)
from app.models.barragem import buscar_por_slug, carregar_barragens
from app.views.formatacao import num_br

# Blueprint = um "pacote de rotas" que depois é registrado no app.
bp = Blueprint("web", __name__)

MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _mes_ano(data) -> str:
    return f"{MESES[data.month - 1]}/{data.year}"


def _card(b) -> dict:
    """Resumo de uma estrutura para os cards da página inicial."""
    series = montar_series(b.slug)
    s1 = series["s1"]
    return {
        "slug": b.slug, "nome": b.nome, "papel": b.papel, "observacao": b.observacao,
        "categoria_risco": b.categoria_risco, "dano_potencial": b.dano_potencial,
        "fonte_alertas": series["fonte_alertas"],
        "n_eventos": int(len(eventos_alerta(series))),
        "n_passagens": int(len(s1)),
        "periodo": f"{_mes_ano(s1['data'].min())} a {_mes_ano(s1['data'].max())}" if not s1.empty else "sem dados",
        # geometria para o mini-mapa (desenhado no navegador)
        "geo": json.dumps({"poligono": mapping(b.poligono) if b.poligono is not None else None,
                           "cristas": [mapping(l) for l in b.cristas.values()],
                           "ponto": [b.lat, b.lon]}),
    }


@bp.route("/")
def inicio():
    barragens = carregar_barragens()
    # Monitoradas primeiro, controle por último
    cards = [_card(b) for b in sorted(barragens, key=lambda x: x.papel == "controle")]
    return render_template("inicio.html", pagina="inicio", cards=cards)


@bp.route("/mapa")
def mapa():
    return render_template("monitor.html", pagina="monitor", estrutura=None)


@bp.route("/estrutura/<slug>")
def estrutura(slug):
    b = buscar_por_slug(slug)
    if b is None:
        abort(404)
    return render_template("monitor.html", pagina="monitor", estrutura=b)


@bp.route("/metodo")
def metodo():
    """Sobre o método: textos fixos + números lidos dos dados (viés, erro, limitações)."""
    notas_arq = config.DATA_DIR / "notas.json"
    notas = json.loads(notas_arq.read_text(encoding="utf-8")) if notas_arq.exists() else {}
    barragens = carregar_barragens()
    validacoes = {}
    for b in barragens:
        s = montar_series(b.slug)
        validacoes[b.slug] = {k: s[k] for k in ("n_validas", "erro_medio_abs_ha", "vies_medio_ha",
                                                "erro_medio_abs_pct", "vies_medio_pct", "n_validas_pct")}
    # Limitações: cada texto uma vez só, com as estruturas em que aparece
    limitacoes = {}
    for b in barragens:
        for l in notas.get(b.nome, {}).get("limitacoes", []):
            item = limitacoes.setdefault(l["titulo"], {"texto": l["texto"], "estruturas": []})
            item["estruturas"].append(b.nome)
    return render_template("metodo.html", pagina="metodo", metodologia=notas.get("metodologia", {}),
                           validacoes=validacoes, limitacoes=limitacoes, barragens=barragens,
                           num=num_br, config=config)


@bp.route("/api/barragens")
def api_barragens():
    saida = []
    for b in carregar_barragens():
        series = montar_series(b.slug)
        s1 = series["s1"]
        saida.append({
            "slug": b.slug,
            "nome": b.nome,
            "lat": b.lat,
            "lon": b.lon,
            "categoria_risco": b.categoria_risco,
            "dano_potencial": b.dano_potencial,
            "situacao": b.situacao,
            "observacao": b.observacao,
            "serie_principal": b.serie_principal,
            "tem_crista": bool(b.cristas),
            "tipo": b.tipo,
            "papel": b.papel,
            "mndwi_limiar": b.mndwi_limiar,
            "crista_interpretavel": b.crista_interpretavel,
            "crista_nota": b.crista_nota,
            "cristas": {tr: mapping(l) for tr, l in b.cristas.items()},
            "poligono": mapping(b.poligono) if b.poligono is not None else None,
            "n_observacoes": int(len(s1)),
            "n_anomalias": series["n_anomalias"],          # só a fonte principal
            "fonte_alertas": series["fonte_alertas"],
            "n_suspeitas": int(s1["suspeita"].sum()) if not s1.empty else 0,
            "ultima_data": s1["data"].iloc[-1].strftime("%Y-%m-%d") if not s1.empty else None,
            "ultima_area_lago_ha": float(s1["area_lago_ha"].iloc[-1]) if not s1.empty else None,
        })
    return jsonify(saida)


@bp.route("/api/barragens/<slug>/serie")
def api_serie(slug):
    if buscar_por_slug(slug) is None:
        abort(404)
    series = montar_series(slug)
    registros = {k: df_para_registros(series[k]) for k in ("s1", "s2", "validacao")}
    for chave in ("erro_medio_abs_ha", "vies_medio_ha", "n_validas",
                  "erro_medio_abs_pct", "vies_medio_pct", "n_validas_pct"):
        registros[chave] = series[chave]
    registros["area_min_pct_ha"] = config.AREA_MIN_PCT_HA
    registros["fonte_alertas"] = series["fonte_alertas"]
    registros["n_anomalias"] = series["n_anomalias"]
    registros["ocorrencias"] = df_para_registros(ocorrencias(series))
    registros["contagem"] = contagem_alertas(series)
    registros["eventos"] = df_para_registros(eventos_alerta(series))
    registros["classes"] = df_para_registros(series["classes"])
    # Faixa de incerteza do S2: {"0.2": [{data, area_lago_ha}], "0.3": [...]}
    registros["sensibilidade"] = {}
    for r in df_para_registros(series["sensibilidade"]):
        registros["sensibilidade"].setdefault(f"{r['limiar']:.2f}", []).append(r)
    # Métricas de crista agrupadas por trecho: {"reto norte": [...], "total": [...]}
    registros["crista"] = {}
    for r in df_para_registros(series["crista"]):
        registros["crista"].setdefault(r["trecho"], []).append(r)
    registros["crista_limite_m"] = config.CRISTA_DISTANCIA_M
    registros["crista_precisao_m"] = config.CRISTA_PRECISAO_M
    registros["nuvem_max_poligono_pct"] = config.NUVEM_MAX_POLIGONO_PCT
    # Acrescenta a URL da máscara para o navegador poder pedir a imagem.
    for chave in ("s1", "s2"):
        for r in registros[chave]:
            r["mascara_url"] = f"/mascaras/{r['mascara_png']}" if r.get("mascara_png") else None
    return jsonify(registros)


@bp.route("/api/barragens/<slug>/relatorio")
def api_relatorio(slug):
    b = buscar_por_slug(slug)
    if b is None:
        abort(404)
    caminho = relatorio_controller.gerar_relatorio(b)
    return send_from_directory(config.PASTA_RELATORIOS, caminho.name)


@bp.route("/referencias/<path:arquivo>")
def referencia(arquivo):
    return send_from_directory(config.PASTA_REFERENCIAS, arquivo)


@bp.route("/mascaras/<path:arquivo>")
def mascara(arquivo):
    # send_from_directory impede acessar arquivos fora da pasta (segurança)
    return send_from_directory(config.PASTA_MASCARAS, arquivo)
