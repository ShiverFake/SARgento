"""Gera a vitrine estática do SARgento em dist/ (para publicar na Vercel ou em qualquer servidor de arquivos).

Adaptado do build_vitrine.py do SismoS2 (~/deteccao-terremoto-s2). Como funciona:
1. O próprio Flask, em modo vitrine, renderiza as páginas (mesmos templates do servidor).
2. O que o JavaScript pede a /api/... é gravado como arquivo .json no mesmo caminho.
3. Máscaras PNG (recortadas pelo polígono) e relatórios HTML são copiados.
4. Os links absolutos ("/metodo") viram relativos ("../metodo/"), para o site funcionar
   em qualquer endereço; no fim, todos os links locais são conferidos.
Nada de GeoTIFF, banco de dados ou rasters vai para dist/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from app import MARCADOR_BASE, criar_app  # noqa: E402
from app.controllers.relatorio_controller import gerar_relatorio  # noqa: E402
from app.models.barragem import carregar_barragens  # noqa: E402

SIZE_LIMIT_MB = 100
ATTR_RE = re.compile(r'(\s(?:href|src|action|poster)=")(/(?!/)[^"]*)"')


def is_page(path: str) -> bool:
    last = path.rstrip("/").split("/")[-1]
    return not path.startswith(("/static/", "/api/", "/mascaras/", "/relatorios/")) and "." not in last


def relative(target: str, depth: int) -> str:
    """'/estrutura/abc#x' numa página de profundidade 1 → '../estrutura/abc/#x'."""
    cut = min([i for i in (target.find("?"), target.find("#")) if i >= 0] or [len(target)])
    path, rest = target[:cut], target[cut:]
    if is_page(path):
        path = path.rstrip("/") + "/" if path != "/" else ""
    out = "../" * depth + path.lstrip("/")
    return (out or "./") + rest


def page_file(out: str, route: str) -> str:
    return os.path.join(out, route.strip("/"), "index.html") if route != "/" else os.path.join(out, "index.html")


def write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def build(out: str) -> dict:
    app = criar_app(vitrine=True)
    client = app.test_client()
    barragens = carregar_barragens()          # todas: monitoradas e de controle

    if os.path.exists(out):
        is_dist = os.path.abspath(out).startswith(ROOT) and os.path.basename(os.path.abspath(out)) == "dist"
        if not (is_dist or os.path.exists(os.path.join(out, "index.html"))):
            raise SystemExit(f"{out} existe e não parece uma vitrine: escolha outra pasta.")
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copytree(os.path.join(ROOT, "app", "views", "static"), os.path.join(out, "static"))

    def get(route: str):
        r = client.get(route)
        if r.status_code != 200:
            raise SystemExit(f"{route} respondeu {r.status_code}")
        return r.data

    # 1) dados que o JavaScript pede a /api/ -> arquivos .json no mesmo caminho
    write(os.path.join(out, "api", "barragens.json"), get("/api/barragens"))
    n_mascaras = 0
    for b in barragens:
        dados = get(f"/api/barragens/{b.slug}/serie")
        write(os.path.join(out, "api", "barragens", b.slug, "serie.json"), dados)

        # 2) máscaras PNG usadas no mapa (o pipeline já as grava recortadas pelo polígono)
        serie = json.loads(dados)
        for chave in ("s1", "s2"):
            for r in serie[chave]:
                if r.get("mascara_png"):
                    origem = config.PASTA_MASCARAS / r["mascara_png"]
                    if origem.exists():
                        destino = os.path.join(out, "mascaras", r["mascara_png"])
                        os.makedirs(os.path.dirname(destino), exist_ok=True)
                        shutil.copy2(origem, destino)
                        n_mascaras += 1

        # 3) relatório HTML autocontido (gerado agora, com os números do momento)
        with app.app_context():
            caminho = gerar_relatorio(b)
        write(os.path.join(out, "relatorios", f"{b.slug}.html"), caminho.read_bytes())

    # 4) páginas HTML com links relativos
    routes = ["/", "/mapa", "/metodo"] + [f"/estrutura/{b.slug}" for b in barragens]
    for route in routes:
        depth = 0 if route == "/" else len(route.strip("/").split("/"))
        html = get(route).decode("utf-8")
        html = ATTR_RE.sub(lambda m: f'{m.group(1)}{relative(m.group(2), depth)}"', html)
        html = html.replace(MARCADOR_BASE, "../" * depth or "./")
        write(page_file(out, route), html.encode("utf-8"))

    return {"pages": routes, "estruturas": [b.nome for b in barragens], "mascaras": n_mascaras}


def check_links(out: str) -> list:
    """Links locais (href/src) de cada página e máscaras citadas nos .json que não existem em dist/."""
    missing = []
    for base, _dirs, files in os.walk(out):
        for f in files:
            if not f.endswith(".html"):
                continue
            page = os.path.join(base, f)
            html = open(page, encoding="utf-8").read()
            for m in re.finditer(r'\s(?:href|src)="([^"]*)"', html):
                valor = m.group(1)
                # endereços com esquema (https:, data:, mailto:) e âncoras não são arquivos locais
                if not valor or valor.startswith(("#", "//")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", valor):
                    continue
                target = re.split(r"[?#]", valor)[0]
                if not target:
                    continue
                path = os.path.normpath(os.path.join(base, target))
                if target.endswith("/") or os.path.isdir(path):
                    path = os.path.join(path, "index.html")
                if not os.path.exists(path):
                    missing.append((os.path.relpath(page, out), target))
    # o JavaScript carrega as máscaras pelo caminho que está no .json
    for base, _dirs, files in os.walk(os.path.join(out, "api")):
        for f in files:
            if f == "serie.json":
                serie = json.load(open(os.path.join(base, f), encoding="utf-8"))
                for chave in ("s1", "s2"):
                    for r in serie[chave]:
                        url = r.get("mascara_url")
                        if url and not os.path.exists(os.path.join(out, url.lstrip("/"))):
                            missing.append((os.path.relpath(os.path.join(base, f), out), url))
    return missing


def folder_mb(path: str) -> float:
    return sum(os.path.getsize(os.path.join(b, f)) for b, _d, fs in os.walk(path) for f in fs) / 1e6


def optimize_pngs(out: str) -> float:
    """Recomprime os PNGs sem perda (Pillow optimize)."""
    from PIL import Image

    saved = 0
    for base, _d, files in os.walk(out):
        for f in files:
            if f.endswith(".png"):
                p = os.path.join(base, f)
                before = os.path.getsize(p)
                with Image.open(p) as im:
                    im.load()
                    im.save(p + ".tmp", format="PNG", optimize=True)
                if os.path.getsize(p + ".tmp") < before:
                    os.replace(p + ".tmp", p)
                    saved += before - os.path.getsize(p)
                else:
                    os.remove(p + ".tmp")
    return saved / 1e6


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()

    info = build(args.out)
    size = folder_mb(args.out)
    if size > SIZE_LIMIT_MB:
        saved = optimize_pngs(args.out)
        print(f"dist/ passou de {SIZE_LIMIT_MB} MB ({size:.1f} MB): PNGs recomprimidos, {saved:.1f} MB a menos")
        size = folder_mb(args.out)
    missing = check_links(args.out)
    n_files = sum(len(fs) for _b, _d, fs in os.walk(args.out))
    print(f"vitrine em {args.out}: {len(info['pages'])} páginas, {len(info['estruturas'])} estruturas "
          f"({', '.join(info['estruturas'])}), {info['mascaras']} máscaras, {n_files} arquivos, {size:.1f} MB")
    if missing:
        print(f"{len(missing)} link(s) local(is) sem arquivo:")
        for page, target in missing[:30]:
            print(f"  {page}: {target}")
        raise SystemExit(1)
    print("links locais: todos encontrados")


if __name__ == "__main__":
    main()
