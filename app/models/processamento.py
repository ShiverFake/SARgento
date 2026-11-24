"""
Model: processamento das imagens

Ideia física: a água parada é um "espelho" para o radar. O sinal bate e vai
embora para longe do satélite, então quase nada volta -> pixel ESCURO
(retroespalhamento baixo, tipicamente < -18 dB em VV). Terra, vegetação e
construções espalham o sinal em várias direções e parte volta -> pixel claro.

Etapas:
1. Filtro de Lee 7x7 (reduz o "chuvisco" chamado speckle)
2. Conversão para decibéis (dB)
3. Limiar de Otsu (separa automaticamente escuro x claro)
4. Limpeza morfológica (remove pixels soltos e furinhos)
5. Áreas em hectares
"""
import numpy as np
from PIL import Image
from scipy import ndimage

import config


# ---------------------------------------------------------------------------
# 1. Filtro de speckle de Lee
# ---------------------------------------------------------------------------
def filtro_lee(img: np.ndarray, tamanho: int = config.TAMANHO_LEE,
               enl: float = config.ENL) -> np.ndarray:
    """Filtro de Lee clássico, aplicado em escala LINEAR (não em dB).

    Por que linear? O speckle é um ruído MULTIPLICATIVO na escala linear; o
    modelo estatístico do Lee foi feito para isso. Por isso filtramos
    primeiro e convertemos para dB depois.

    Em cada janela 7x7:
      - área homogênea (ex.: meio do lago) -> usa a MÉDIA da janela (suaviza)
      - área com borda/detalhe              -> mantém o pixel ORIGINAL
    O peso W entre os dois vem do coeficiente de variação local (Ci)
    comparado ao do ruído (Cu = 1/sqrt(ENL)).
    """
    valido = np.isfinite(img) & (img > 0)
    if not valido.any():
        return np.full_like(img, np.nan, dtype="float32")

    # Pixels sem dado atrapalhariam as médias: preenchemos temporariamente.
    x = np.where(valido, img, np.nanmean(img[valido])).astype("float64")

    media = ndimage.uniform_filter(x, tamanho)          # média local
    media_quad = ndimage.uniform_filter(x * x, tamanho)  # média dos quadrados
    variancia = np.maximum(media_quad - media ** 2, 0)  # variância local

    cu2 = 1.0 / enl  # (coef. de variação do ruído)²
    with np.errstate(divide="ignore", invalid="ignore"):
        ci2 = variancia / media ** 2        # (coef. de variação local)²
        peso = 1.0 - cu2 / ci2
    peso = np.clip(np.nan_to_num(peso, nan=0.0, neginf=0.0, posinf=1.0), 0, 1)

    saida = media + peso * (x - media)
    saida[~valido] = np.nan
    return saida.astype("float32")


# ---------------------------------------------------------------------------
# 2. Conversão para decibéis
# ---------------------------------------------------------------------------
def para_db(linear: np.ndarray) -> np.ndarray:
    """dB = 10 * log10(valor linear). Valores <= 0 viram NaN (sem dado)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(linear > 0, 10.0 * np.log10(linear), np.nan).astype("float32")


# ---------------------------------------------------------------------------
# 3. Limiar de Otsu
# ---------------------------------------------------------------------------
def limiar_otsu(valores: np.ndarray, minimo: float = config.LIMIAR_MIN_DB,
                maximo: float = config.LIMIAR_MAX_DB, bins: int = 256):
    """Calcula o limiar de Otsu e o limita ao intervalo [minimo, maximo].

    Otsu testa todos os cortes possíveis do histograma e escolhe aquele que
    deixa os dois grupos (água / não-água) o mais "separados" possível
    (maior variância ENTRE as classes).

    Por que limitar entre -20 e -15 dB? Se numa data quase não houver água
    no recorte, o histograma não terá dois picos e o Otsu pode cair num
    valor absurdo. O limite é um "cinto de segurança" físico.

    Retorna (limiar_usado, limiar_bruto).
    """
    v = valores[np.isfinite(valores)]
    if v.size < 100:  # poucos pixels: não dá para confiar no histograma
        meio = (minimo + maximo) / 2
        return meio, float("nan")

    contagem, bordas = np.histogram(v, bins=bins)
    centros = (bordas[:-1] + bordas[1:]) / 2
    p = contagem / contagem.sum()       # probabilidade de cada faixa

    w0 = np.cumsum(p)                   # peso da classe "abaixo do corte"
    w1 = 1.0 - w0                       # peso da classe "acima do corte"
    mu = np.cumsum(p * centros)         # média acumulada
    mu_total = mu[-1]

    with np.errstate(divide="ignore", invalid="ignore"):
        var_entre = (mu_total * w0 - mu) ** 2 / (w0 * w1)
    var_entre = np.nan_to_num(var_entre, nan=0.0, posinf=0.0)

    bruto = float(centros[np.argmax(var_entre)])
    return float(np.clip(bruto, minimo, maximo)), bruto


# ---------------------------------------------------------------------------
# 4. Limpeza morfológica
# ---------------------------------------------------------------------------
def _remover_pequenos(mascara: np.ndarray, min_pixels: int) -> np.ndarray:
    """Remove manchas (componentes conectados) com menos de min_pixels."""
    rotulos, n = ndimage.label(mascara)  # numera cada mancha: 1, 2, 3...
    if n == 0:
        return mascara
    tamanhos = np.bincount(rotulos.ravel())  # nº de pixels de cada mancha
    pequenos = tamanhos < min_pixels
    pequenos[0] = False                      # rótulo 0 = fundo
    return mascara & ~pequenos[rotulos]


def elemento_estruturante(nome: str) -> np.ndarray:
    """A "janela" usada pela morfologia, sempre 3x3:

        cruz          quadrado
        . X .         X X X
        X X X         X X X
        . X .         X X X

    Na cruz, só os 4 vizinhos de lado contam; no quadrado, também as diagonais.
    """
    conectividade = {"cruz": 1, "quadrado": 2}[nome]
    return ndimage.generate_binary_structure(2, conectividade)


def limpar_mascara(agua: np.ndarray, min_pixels: int = config.MIN_PIXELS_OBJETO,
                   elemento: str = config.MORFOLOGIA_ELEMENTO) -> np.ndarray:
    """Abertura + fechamento + remoção de manchas e buracos pequenos.

    - ABERTURA (erosão -> dilatação): apaga pontinhos isolados de "água".
    - FECHAMENTO (dilatação -> erosão): fecha falhas finas dentro do lago.
    """
    janela = elemento_estruturante(elemento)
    agua = ndimage.binary_opening(agua, structure=janela)
    agua = ndimage.binary_closing(agua, structure=janela)
    agua = _remover_pequenos(agua, min_pixels)         # manchas de água pequenas
    agua = ~_remover_pequenos(~agua, min_pixels)       # buracos pequenos no lago
    return agua


# ---------------------------------------------------------------------------
# 5. Áreas
# ---------------------------------------------------------------------------
def calcular_areas(agua: np.ndarray, dentro: np.ndarray, valido: np.ndarray,
                   area_pixel_m2: float) -> dict:
    """Áreas em hectares (1 ha = 10.000 m²).

    - polígono = todos os pixels dentro do reservatório
    - lago     = pixels de água dentro do polígono
    - praia    = polígono - lago (rejeito exposto, sem lâmina d'água)
    """
    px_para_ha = area_pixel_m2 / 10_000
    n_poligono = int(dentro.sum())
    n_lago = int((agua & dentro).sum())
    n_praia = int((dentro & ~agua & valido).sum())
    cobertura = float((valido & dentro).sum() / n_poligono) if n_poligono else 0.0
    return {
        "area_poligono_ha": round(n_poligono * px_para_ha, 3),
        "area_lago_ha": round(n_lago * px_para_ha, 3),
        "area_praia_ha": round(n_praia * px_para_ha, 3),
        "cobertura_valida": round(cobertura, 3),
    }


# ---------------------------------------------------------------------------
# Pipeline completo de UMA data Sentinel-1
# ---------------------------------------------------------------------------
def processar_cena_s1(vv_linear, vh_linear, dentro, area_pixel_m2):
    """Executa as etapas 1–5 numa data.

    Devolve (resultados, mascara_agua, vv_db_filtrado). O VV filtrado é
    guardado para o controle de qualidade (app/models/qualidade.py).
    """
    vv_db = para_db(filtro_lee(vv_linear))
    vh_db = para_db(filtro_lee(vh_linear))
    valido = np.isfinite(vv_db)

    # O Otsu usa o recorte inteiro (polígono + margem), pois precisa ver
    # tanto água quanto terra para achar o melhor corte.
    limiar, bruto = limiar_otsu(vv_db)
    agua = limpar_mascara((vv_db < limiar) & valido)

    resultado = calcular_areas(agua, dentro, valido, area_pixel_m2)
    resultado.update({
        "limiar": round(limiar, 2),
        "limiar_otsu_bruto": None if np.isnan(bruto) else round(bruto, 2),
        "vv_medio_db": _media_segura(vv_db[dentro]),
        "vh_medio_db": _media_segura(vh_db[dentro]),
    })
    return resultado, agua, vv_db


def _media_segura(valores):
    v = valores[np.isfinite(valores)]
    return round(float(v.mean()), 2) if v.size else None


def salvar_png_mascara(agua: np.ndarray, caminho, dentro: np.ndarray = None) -> None:
    """Salva a máscara como PNG transparente: água = azul, resto = invisível.

    Com 'dentro', só a água DENTRO do polígono aparece no mapa. (As áreas
    sempre foram calculadas só dentro do polígono; o recorte é só visual.)
    """
    if dentro is not None:
        agua = agua & dentro
    rgba = np.zeros((*agua.shape, 4), dtype=np.uint8)
    rgba[agua] = (30, 144, 255, 200)  # (R, G, B, opacidade)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(caminho)
