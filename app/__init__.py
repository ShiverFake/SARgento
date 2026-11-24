"""
Pacote principal da aplicação.

criar_app() é uma "fábrica": monta e devolve o objeto Flask já configurado.
Note como as pastas da VIEW (templates e static) ficam em app/views/.

Modo vitrine (vitrine=True): usado só por tools/build_vitrine.py para gerar a
versão estática do site. As páginas recebem um marcador no lugar do caminho
base (o build troca por "./" ou "../.."), escondem as ações de processamento e
mostram o aviso de demonstração no cabeçalho.
"""
from datetime import datetime

from flask import Flask

from app.models import database

MARCADOR_BASE = "__SARGENTO_BASE__"


def criar_app(vitrine: bool = False) -> Flask:
    app = Flask(
        __name__,
        template_folder="views/templates",  # HTML (Jinja2)
        static_folder="views/static",       # CSS, JavaScript, fontes, bibliotecas
    )
    app.json.ensure_ascii = False  # mantém acentos legíveis no JSON
    app.config["VITRINE"] = vitrine
    app.config["CONGELADO_EM"] = datetime.now().strftime("%d/%m/%Y")

    # {{ valor|num(1) }} em qualquer template -> "1.234,5" (vírgula decimal)
    from app.views.formatacao import num_br
    app.jinja_env.filters["num"] = num_br

    @app.context_processor
    def variaveis_globais():
        # Disponíveis em todos os templates
        return {"vitrine": app.config["VITRINE"],
                "base": MARCADOR_BASE if app.config["VITRINE"] else "/",
                "congelado_em": app.config["CONGELADO_EM"],
                "pagina": None}

    from app.controllers.web_controller import bp
    app.register_blueprint(bp)

    database.criar_tabelas()
    return app
