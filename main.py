"""
Ponto de entrada do projeto (linha de comando).

Exemplos:
    python main.py barragens
    python main.py processar --inicio 2024-01-01 --fim 2024-12-31
    python main.py processar --inicio 2024-01-01 --fim 2024-12-31 --validar-s2
    python main.py relatorio
    python main.py web
"""
import argparse
import logging
import sys

import config
from app.models.barragem import carregar_barragens


def _filtrar(barragens, nome):
    """Se --barragem foi informado, mantém só as que contêm esse texto."""
    if not nome:
        return barragens
    escolhidas = [b for b in barragens if nome.lower() in b.nome.lower() or nome == b.slug]
    if not escolhidas:
        sys.exit(f"Nenhuma barragem corresponde a '{nome}'.")
    return escolhidas


def cmd_barragens(args):
    for b in carregar_barragens():
        tem_pol = "sim" if b.poligono is not None else "NÃO"
        print(f"{b.slug:35s} risco={b.categoria_risco:8s} polígono={tem_pol}  ({b.lat}, {b.lon})")


def cmd_processar(args):
    # Import aqui dentro: só carrega as bibliotecas pesadas quando necessário.
    from app.controllers import pipeline_controller
    barragens = _filtrar(carregar_barragens(), args.barragem)
    pipeline_controller.executar(barragens, args.inicio, args.fim,
                                 validar_s2=args.validar_s2, reprocessar=args.reprocessar,
                                 apenas_s2=args.apenas_s2)


def cmd_qualidade(args):
    from app.models import crista, database, qualidade, superficie
    database.criar_tabelas()
    for b in _filtrar(carregar_barragens(), args.barragem):
        qualidade.atualizar_qualidade(b.slug)
        crista.atualizar_metricas(b)
        superficie.atualizar_qualidade_s2(b)
        superficie.atualizar_sensibilidade(b)
        superficie.atualizar_classes(b)


def cmd_referencias(args):
    from app.models import referencias
    if args.data:   # painel avulso de uma data
        for b in _filtrar(carregar_barragens(), args.barragem):
            r = referencias.gerar_data(b, args.data)
            print(f"{b.nome}: {r['arquivo']}")
            for k, v in r.items():
                if k != "arquivo":
                    print(f"   {k}: {v}")
        return
    for b in _filtrar(carregar_barragens(), args.barragem):
        if b.papel == "controle" and not args.barragem:
            continue  # estrutura de controle (ex.: Pampulha) não precisa de painéis
        for r in referencias.gerar(b):
            print(f"{b.nome}: {r['periodo']}: {r.get('arquivo') or 'sem cena utilizável'}")


def cmd_relatorio(args):
    from app.controllers.relatorio_controller import gerar_relatorio
    for b in _filtrar(carregar_barragens(), args.barragem):
        print("Relatório gerado:", gerar_relatorio(b))


def cmd_web(args):
    from app import criar_app
    print(f"Abra no navegador do Windows: http://IP_DA_VM:{args.porta}")
    criar_app().run(host=args.host, port=args.porta, debug=args.debug)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="SARgento – Monitoramento de barragens de rejeito por radar")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("barragens", help="lista as barragens cadastradas")
    p.set_defaults(func=cmd_barragens)

    p = sub.add_parser("processar", help="baixa e processa imagens")
    p.add_argument("--inicio", required=True, help="data inicial AAAA-MM-DD")
    p.add_argument("--fim", required=True, help="data final AAAA-MM-DD")
    p.add_argument("--barragem", help="processa só a barragem com este nome/slug")
    p.add_argument("--validar-s2", action="store_true", help="também processa Sentinel-2")
    p.add_argument("--reprocessar", action="store_true", help="refaz datas já salvas")
    p.add_argument("--apenas-s2", action="store_true", help="processa só o Sentinel-2 (S1 fica como está)")
    p.set_defaults(func=cmd_processar)

    p = sub.add_parser("qualidade", help="recalcula o núcleo de água e as suspeitas de vento/chuva")
    p.add_argument("--barragem")
    p.set_defaults(func=cmd_qualidade)

    p = sub.add_parser("referencias", help="gera os 4 painéis RGB de referência por barragem")
    p.add_argument("--barragem")
    p.add_argument("--data", help="gera só o painel avulso desta data S2 (AAAA-MM-DD)")
    p.set_defaults(func=cmd_referencias)

    p = sub.add_parser("relatorio", help="gera relatórios HTML em reports/")
    p.add_argument("--barragem")
    p.set_defaults(func=cmd_relatorio)

    # A versão estática para a Vercel é gerada por tools/build_vitrine.py (veja DEPLOY_VERCEL.md).

    p = sub.add_parser("web", help="inicia a interface web")
    p.add_argument("--host", default=config.HOST)
    p.add_argument("--porta", type=int, default=config.PORTA)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_web)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
