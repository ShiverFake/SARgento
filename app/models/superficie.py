"""
Model: superfície do reservatório combinando S1 e S2

1) Faixa de incerteza do S2
   A área do lago S2 depende do limiar MNDWI escolhido. Para não esconder
   isso, recalculamos a área com vários limiares (ex.: 0,20 / 0,25 / 0,30)
   a partir do MNDWI salvo em disco, sem baixar nada de novo.

2) Classes da superfície (só barragens de rejeito)
   Nas barragens de rejeito o radar não distingue água de rejeito
   saturado: os dois são lisos e escuros. O S2 distingue (água tem MNDWI
   alto), mas só funciona sem nuvem. Combinando os dois, nas datas em que há
   S2 confiável a até 3 dias da passagem S1:
       lago            = água no S2
       praia saturada  = escuro no S1  E  não é água no S2
       praia seca      = o resto do polígono
   Pixels com nuvem no S2 ficam como "sem dado".
"""
import logging

import numpy as np
import pandas as pd
from affine import Affine

import config
from app.models import database, processamento, qualidade, sentinel2
from app.models.barragem import Barragem

log = logging.getLogger(__name__)


def salvar_raster_s2(slug: str, data: str, mndwi, valido, dentro, affine, agua_scl) -> None:
    """agua_scl = pixels que a própria ESA (banda SCL) classificou como água (classe 6)."""
    config.PASTA_RASTERS.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(qualidade.caminho_raster(slug, data, "S2"),
                        mndwi=mndwi.astype("float16"), valido=valido.astype(bool),
                        dentro=dentro.astype(bool), agua_scl=agua_scl.astype(bool),
                        affine=np.array(tuple(affine)[:6], dtype="float64"))


def atualizar_qualidade_s2(barragem: Barragem) -> None:
    """Controle de qualidade do S2 pela banda SCL.

    1. Núcleo de água do S2: pixels que foram água (MNDWI acima do limiar) em
       >= 90% das datas sem nuvem, com erosão de 2 pixels (mesma regra do S1).
    2. Em cada data, duas frações desse núcleo: a que a SCL classificou como
       "água" e a que o nosso MNDWI detectou como água.
    3. A data é marcada "não confiável (SCL)" quando as DUAS ficam baixas
       (SCL < 50% e MNDWI < 80% das respectivas medianas): sinal de névoa ou
       fumaça que passou pelo filtro de nuvem e "apagou" parte do lago.
       Só a SCL não basta: em lagos verdes e turvos ela às vezes não reconhece
       a água numa cena limpa, e o MNDWI continua correto.
    """
    s2 = database.obter_serie(barragem.slug, "S2")
    limiar = barragem.mndwi_limiar if barragem.mndwi_limiar is not None else config.LIMIAR_MNDWI
    dados = {}
    for _, r in s2.iterrows():
        caminho = qualidade.caminho_raster(barragem.slug, r["data"].strftime("%Y-%m-%d"), "S2")
        if not caminho.exists():
            continue
        with np.load(caminho) as z:
            if "agua_scl" not in z:
                continue  # raster antigo, sem a SCL
            dados[r["data"].strftime("%Y-%m-%d")] = {
                "agua": processamento.limpar_mascara((z["mndwi"].astype("float32") > limiar) & z["valido"]),
                "valido": z["valido"], "agua_scl": z["agua_scl"], "dentro": z["dentro"],
                "sem_nuvem": r["nuvem_poligono_pct"] <= config.NUVEM_MAX_POLIGONO_PCT}
    limpas = [d for d in dados.values() if d["sem_nuvem"]]
    if len(limpas) < config.NUCLEO_MIN_DATAS:
        log.warning("QC S2: só %d datas sem nuvem com SCL salva; regra não aplicada.", len(limpas))
        return
    formato = limpas[0]["agua"].shape
    pilha = np.stack([d["agua"] for d in limpas if d["agua"].shape == formato])
    nucleo = qualidade.calcular_nucleo(pilha, limpas[0]["dentro"])
    if nucleo.sum() < 10:
        log.info("QC S2: sem núcleo de água permanente no S2 (%d px); regra não se aplica a esta estrutura.",
                 nucleo.sum())
        for data in dados:
            database.atualizar_campos(barragem.slug, data, "S2", {"scl_agua_nucleo": None, "nao_confiavel_scl": 0})
        return

    fr_scl, fr_mndwi = {}, {}
    for data, d in dados.items():
        if d["agua"].shape != formato:
            continue
        olhar = nucleo & d["valido"]          # ignora pixels cobertos por nuvem
        ok = olhar.sum() >= 10
        fr_scl[data] = float(d["agua_scl"][olhar].mean()) if ok else np.nan
        fr_mndwi[data] = float(d["agua"][olhar].mean()) if ok else np.nan
    sem_nuvem = [d for d in fr_scl if dados[d]["sem_nuvem"]]
    ref_scl = np.nanmedian([fr_scl[d] for d in sem_nuvem])
    ref_mndwi = np.nanmedian([fr_mndwi[d] for d in sem_nuvem])
    marcadas = []
    for data in fr_scl:
        f, m = fr_scl[data], fr_mndwi[data]
        ruim = bool(np.isfinite(f) and np.isfinite(m)
                    and f < config.SCL_FRACAO_MIN_REL * ref_scl
                    and m < config.MNDWI_FRACAO_MIN_REL * ref_mndwi)
        database.atualizar_campos(barragem.slug, data, "S2", {
            "scl_agua_nucleo": None if np.isnan(f) else round(f, 3), "nao_confiavel_scl": int(ruim)})
        if ruim and dados[data]["sem_nuvem"]:
            marcadas.append(f"{data} (SCL {100*f:.0f}%, MNDWI {100*m:.0f}%)")
    log.info("QC S2: núcleo %.1f ha; medianas no núcleo: SCL %.0f%%, MNDWI %.0f%%; marcadas (sem nuvem): %s",
             nucleo.sum() / 100, 100 * ref_scl, 100 * ref_mndwi, ", ".join(marcadas) or "nenhuma")


def atualizar_sensibilidade(barragem: Barragem) -> None:
    """Área S2 com cada limiar da faixa de incerteza, para todas as datas S2."""
    if not barragem.mndwi_incerteza:
        return
    linhas = []
    for data in (d.strftime("%Y-%m-%d") for d in database.obter_serie(barragem.slug, "S2")["data"]):
        caminho = qualidade.caminho_raster(barragem.slug, data, "S2")
        if not caminho.exists():
            continue
        with np.load(caminho) as z:
            mndwi, valido, dentro = z["mndwi"].astype("float32"), z["valido"], z["dentro"]
            px = abs(Affine(*z["affine"]).a) ** 2
        for lim in barragem.mndwi_incerteza:
            linhas.append({"data": data, "limiar": lim,
                           "area_lago_ha": sentinel2.area_com_limiar(mndwi, valido, dentro, lim, px)})
    database.substituir_tabela("s2_sensibilidade", barragem.slug, linhas)
    log.info("Sensibilidade S2: %d datas x %d limiares", len(linhas) // max(len(barragem.mndwi_incerteza), 1),
             len(barragem.mndwi_incerteza))


def atualizar_classes(barragem: Barragem) -> None:
    """Classes lago / praia saturada / praia seca nas datas com S1 + S2 confiável."""
    if not barragem.rejeito:
        return
    s1 = database.obter_serie(barragem.slug, "S1")
    s2 = database.obter_serie(barragem.slug, "S2")
    s2 = s2[sentinel2.s2_confiavel(s2)]          # sem nuvem E aprovada na SCL
    limiar = barragem.mndwi_limiar if barragem.mndwi_limiar is not None else config.LIMIAR_MNDWI
    linhas = []
    for d1 in s1["data"]:
        if s2.empty:
            break
        dif = (s2["data"] - d1).dt.days.abs()
        if dif.min() > config.CLASSES_TOLERANCIA_DIAS:
            continue
        d2 = s2.loc[dif.idxmin(), "data"]
        c1 = qualidade.caminho_raster(barragem.slug, d1.strftime("%Y-%m-%d"), "S1")
        c2 = qualidade.caminho_raster(barragem.slug, d2.strftime("%Y-%m-%d"), "S2")
        if not (c1.exists() and c2.exists()):
            continue
        with np.load(c1) as a, np.load(c2) as b:
            escuro, dentro = a["agua"], a["dentro"]
            mndwi, valido = b["mndwi"].astype("float32"), b["valido"]
            px_ha = abs(Affine(*b["affine"]).a) ** 2 / 1e4
        if escuro.shape != mndwi.shape:
            log.warning("Classes: grades S1 e S2 diferentes em %s; pulando", d1.date())
            continue
        lago = processamento.limpar_mascara((mndwi > limiar) & valido) & dentro
        saturada = escuro & dentro & valido & ~lago
        seca = dentro & valido & ~lago & ~saturada
        linhas.append({"data_s1": d1.strftime("%Y-%m-%d"), "data_s2": d2.strftime("%Y-%m-%d"),
                       "dias": int((d2 - d1).days),
                       "lago_ha": round(lago.sum() * px_ha, 2),
                       "praia_saturada_ha": round(saturada.sum() * px_ha, 2),
                       "praia_seca_ha": round(seca.sum() * px_ha, 2),
                       "sem_dado_ha": round((dentro & ~valido).sum() * px_ha, 2)})
    database.substituir_tabela("classes_superficie", barragem.slug, linhas)
    log.info("Classes da superfície: %d datas com S1 + S2 a até %d dias", len(linhas),
             config.CLASSES_TOLERANCIA_DIAS)
