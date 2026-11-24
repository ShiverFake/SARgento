"""
Model: detecção de anomalias na série temporal

Duas regras, aplicadas à área do lago:

1) Z-SCORE ROBUSTO (mediana móvel + MAD)
   Para cada data, olhamos as N passagens ANTERIORES (o "normal recente"):
     mediana = valor típico
     MAD     = mediana dos desvios absolutos (o "espalhamento" típico)
     z       = 0,6745 * (valor - mediana) / MAD
   Usamos mediana e MAD em vez de média e desvio-padrão porque eles NÃO são
   distorcidos por um valor maluco isolado (são "robustos").
   O fator 0,6745 deixa o z comparável ao z-score comum. |z| > 3 = anomalia.

2) VARIAÇÃO ENTRE PASSAGENS
   Compara cada data com a anterior. Pega mudanças bruscas mesmo quando
   ainda não há histórico para a regra 1.
   - se a área anterior é >= 5 ha: alerta quando a variação passa de 20%
     E de 5 ha ao mesmo tempo (em poças de 20 ha, ±20% são só 4 ha);
   - se é < 5 ha: alerta quando a variação passa de 1 ha (em áreas pequenas
     a % não tem sentido: 0,1 -> 0,3 ha seria "+200%").

Cada passagem recebe um STATUS:
  "normal"    -> nada de especial
  "anomalia"  -> disparou a regra 1 e/ou a 2
  "retorno"   -> logo após uma anomalia, voltou ao nível de ANTES dela.
                 (Sem isso, a volta ao normal disparava a regra 2 e virava
                 uma "anomalia nova" falsa.)
  "suspeita"  -> vento/chuva na hora da passagem (ver qualidade.py).
                 Fica FORA dos cálculos: não entra na mediana, no MAD nem
                 na variação %, para não contaminar as passagens seguintes.
"""
import numpy as np
import pandas as pd

import config

COLUNAS_NUMERICAS = ["mediana_movel", "mad", "z", "var_pct", "var_ha", "ref_retorno_ha"]


def _mad(valores: np.ndarray) -> float:
    return float(np.median(np.abs(valores - np.median(valores))))


def _voltou_ao_normal(valor, base, limiar_z, limiar_pct, var_abs_ha, var_min_ha) -> bool:
    """A passagem está de volta ao nível de antes da anomalia?

    'base' guarda como era a série logo ANTES da anomalia começar.
    - Se havia histórico (mediana e MAD): voltou se |z| em relação àquela
      referência for <= limiar_z.
    - Se não havia (começo da série): voltou se a diferença para a passagem
      anterior à anomalia for <= limiar_pct.
    """
    if pd.notna(base["mediana"]) and pd.notna(base["mad"]):
        return abs(0.6745 * (valor - base["mediana"]) / base["mad"]) <= limiar_z
    if pd.notna(base["anterior"]):
        dif = abs(valor - base["anterior"])
        if base["anterior"] < config.AREA_MIN_PCT_HA:   # área pequena: compara em ha
            return dif <= var_abs_ha
        # "voltou" = não dispararia a regra de variação (que exige % E ha)
        return 100 * dif / base["anterior"] <= limiar_pct or dif <= var_min_ha
    return False


def detectar_anomalias(df: pd.DataFrame, coluna: str = "area_lago_ha",
                       janela: int = config.JANELA_MEDIANA,
                       limiar_z: float = config.LIMIAR_Z,
                       limiar_pct: float = config.LIMIAR_VARIACAO_PCT,
                       ruido_ha: float = None) -> pd.DataFrame:
    """Acrescenta: mediana_movel, mad, z, var_pct, anomalia_z, anomalia_pct,
    anomalia, retorno, ref_retorno_ha, suspeita e status.

    ruido_ha (opcional, coluna do sigbm.csv): ruído típico da medição desta
    estrutura. Com ele, NENHUMA regra alerta para mudanças menores que o ruído:
      - a variação entre passagens precisa passar de ruido_ha;
      - o piso do MAD vira ruido_ha x 0,6745 / 3, de modo que |z| > 3 só
        acontece com desvio da mediana maior que ruido_ha.

    O texto explicativo ("motivo") é montado na camada de view
    (app/views/formatacao.py), pois envolve formatação para o usuário.
    """
    df = df.sort_values("data").reset_index(drop=True).copy()

    if "suspeita_meteo" in df.columns:
        df["suspeita"] = df["suspeita_meteo"].fillna(0).astype(int).astype(bool)
    else:
        df["suspeita"] = False

    for c in COLUNAS_NUMERICAS:
        df[c] = np.nan
    for c in ("anomalia_z", "anomalia_pct", "anomalia_abs", "anomalia", "retorno"):
        df[c] = False
    df["status"] = np.where(df["suspeita"], "suspeita", "normal")

    # Daqui em diante só as passagens confiáveis. Como 'serie' mantém o índice
    # original, os resultados voltam para a linha certa do DataFrame.
    serie = df.loc[~df["suspeita"], coluna].astype(float)
    if serie.empty:
        return df

    # shift(1) = desloca uma posição, para a janela conter só o PASSADO.
    # (Se o valor atual entrasse na própria referência, ele "se esconderia".)
    passado = serie.shift(1)
    minimo = max(3, janela // 2 + 1)
    mediana = passado.rolling(janela, min_periods=minimo).median()
    mad = passado.rolling(janela, min_periods=minimo).apply(_mad, raw=True)
    # Pisos do MAD: 3% da mediana (numa série muito estável o MAD real fica
    # minúsculo) e 0,5 ha (em áreas pequenas, oscilações de poucos pixels não
    # podem virar |z| enorme). Vale o maior dos dois.
    piso_ha = config.MAD_MIN_HA
    if ruido_ha:
        piso_ha = max(piso_ha, ruido_ha * 0.6745 / limiar_z)
    mad = np.maximum(mad, (mediana.abs() * config.MAD_MIN_REL).clip(lower=piso_ha))
    var_abs_ha = max(config.LIMIAR_VARIACAO_ABS_HA, ruido_ha or 0)   # regra de área pequena
    var_min_ha = max(config.LIMIAR_VARIACAO_MIN_HA, ruido_ha or 0)   # o "E > 5 ha" da regra %
    z = 0.6745 * (serie - mediana) / mad
    var_ha = serie.diff()               # em relação à passagem confiável anterior
    # % só quando a área anterior é >= 5 ha (senão fica vazia)
    var_pct = (100 * var_ha / passado).where(passado >= config.AREA_MIN_PCT_HA)
    pequena = passado < config.AREA_MIN_PCT_HA

    df.loc[serie.index, "mediana_movel"] = mediana
    df.loc[serie.index, "mad"] = mad
    df.loc[serie.index, "z"] = z
    df.loc[serie.index, "var_pct"] = var_pct
    df.loc[serie.index, "var_ha"] = var_ha

    # Percorre a série em ordem, lembrando se estamos "dentro" de uma anomalia.
    em_anomalia = False
    base = None
    for i in serie.index:
        valor = serie[i]
        if em_anomalia and _voltou_ao_normal(valor, base, limiar_z, limiar_pct, var_abs_ha, var_min_ha):
            df.at[i, "retorno"] = True
            df.at[i, "status"] = "retorno"
            df.at[i, "ref_retorno_ha"] = base["nivel"]
            em_anomalia = False
            continue

        dispara_z = bool(abs(z[i]) > limiar_z) if pd.notna(z[i]) else False
        dispara_pct = (pd.notna(var_pct[i]) and abs(var_pct[i]) > limiar_pct
                       and abs(var_ha[i]) > var_min_ha)
        dispara_abs = (bool(pequena[i]) and pd.notna(var_ha[i])
                       and abs(var_ha[i]) > var_abs_ha)
        if dispara_z or dispara_pct or dispara_abs:
            if not em_anomalia:
                # Começo de uma anomalia: guarda como a série estava antes dela.
                base = {"mediana": mediana[i], "mad": mad[i], "anterior": passado[i]}
                base["nivel"] = base["mediana"] if pd.notna(base["mediana"]) else base["anterior"]
                em_anomalia = True
            df.at[i, "anomalia_z"] = dispara_z
            df.at[i, "anomalia_pct"] = dispara_pct
            df.at[i, "anomalia_abs"] = dispara_abs
            df.at[i, "anomalia"] = True
            df.at[i, "status"] = "anomalia"
        else:
            em_anomalia = False  # estabilizou: a anomalia acabou
    return df
