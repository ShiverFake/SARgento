"""
View: formatação de números e textos para o usuário (padrão brasileiro).

Python e JavaScript escrevem 1234.5; no Brasil escrevemos 1.234,5.
Toda a formatação "para humanos" fica aqui, separada dos cálculos (models).
"""
import math

import config


def num_br(valor, casas: int = 2, sinal: bool = False, vazio: str = "–") -> str:
    """num_br(1234.567) -> '1.234,57'   num_br(-3.2, 1, sinal=True) -> '-3,2'

    Truque: formata no padrão americano (1,234.57) e troca os separadores
    usando um caractere provisório (_) para não misturar as trocas.
    """
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return vazio
    if math.isnan(v):
        return vazio
    texto = f"{v:+,.{casas}f}" if sinal else f"{v:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def _casas_auto(v: float) -> int:
    return 0 if float(v).is_integer() else 1


ROTULOS_STATUS = {
    "normal": "Normal",
    "anomalia": "Anomalia",
    "retorno": "Retorno ao normal",
    "suspeita": "Suspeita de vento/chuva",
}


def texto_motivo(linha) -> str:
    """Explica, em português, o status de uma passagem."""
    if linha.status == "suspeita":
        return (f"VV do núcleo {num_br(linha.vv_nucleo_delta_db, 1, sinal=True)} dB acima da "
                f"mediana da série; fora do cálculo de anomalias")
    if linha.status == "retorno":
        return f"voltou ao nível anterior à anomalia (ref. {num_br(linha.ref_retorno_ha)} ha)"
    partes = []
    if linha.anomalia_z:
        partes.append(f"|z| = {num_br(abs(linha.z), 1)} > "
                      f"{num_br(config.LIMIAR_Z, _casas_auto(config.LIMIAR_Z))}")
    if linha.anomalia_pct:
        partes.append(f"variação de {num_br(linha.var_pct, 1, sinal=True)}% "
                      f"({num_br(linha.var_ha, 1, sinal=True)} ha) vs. passagem anterior")
    if getattr(linha, "anomalia_abs", False):
        partes.append(f"variação de {num_br(linha.var_ha, 2, sinal=True)} ha vs. passagem anterior "
                      f"(área < {num_br(config.AREA_MIN_PCT_HA, 0)} ha: regra em ha)")
    return "; ".join(partes)
