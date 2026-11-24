"""
Configurações centrais do projeto.

Todos os parâmetros "ajustáveis" ficam aqui, num só lugar. Assim você não
precisa caçar números mágicos espalhados pelo código: se quiser mudar o
limiar de anomalia ou a resolução, mude aqui.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Pastas e arquivos
# ---------------------------------------------------------------------------
# Path(__file__) é o caminho deste arquivo; .parent é a pasta onde ele está.
BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
CSV_BARRAGENS = DATA_DIR / "sigbm.csv"             # lista de barragens
GEOJSON_POLIGONOS = DATA_DIR / "poligonos.geojson"  # polígonos dos reservatórios
GEOJSON_CRISTAS = DATA_DIR / "cristas.geojson"      # linhas de crista (opcional)
PASTA_REFERENCIAS = DATA_DIR / "referencias"        # painéis RGB de conferência
BANCO_SQLITE = DATA_DIR / "monitor.db"              # série temporal
PASTA_MASCARAS = DATA_DIR / "mascaras"              # PNGs das máscaras de água
PASTA_RELATORIOS = BASE_DIR / "reports"             # relatórios HTML
PASTA_RASTERS = DATA_DIR / "rasters"                # VV filtrado + máscara por data (.npz)

# ---------------------------------------------------------------------------
# Microsoft Planetary Computer (catálogo STAC)
# ---------------------------------------------------------------------------
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLECAO_S1 = "sentinel-1-rtc"   # Sentinel-1 já corrigido de terreno (RTC)
COLECAO_S2 = "sentinel-2-l2a"   # Sentinel-2 reflectância de superfície

# ---------------------------------------------------------------------------
# Geometria / grade
# ---------------------------------------------------------------------------
RESOLUCAO_M = 10   # tamanho do pixel em metros
BUFFER_M = 500     # margem em volta do polígono (ajuda o Otsu a ver terra + água)

# ---------------------------------------------------------------------------
# Órbita do Sentinel-1
# ---------------------------------------------------------------------------
# Comparar imagens de órbitas diferentes mistura geometrias de visada
# diferentes (sombra/encurtamento mudam). Por isso usamos UMA órbita só.
# None = na 1ª execução o programa escolhe a órbita mais frequente; depois
#        reaproveita a órbita já registrada no banco para aquela barragem.
ORBITA_RELATIVA = None   # ex.: 53
DIRECAO_ORBITA = None    # "ascending", "descending" ou None

# ---------------------------------------------------------------------------
# Pré-processamento SAR
# ---------------------------------------------------------------------------
TAMANHO_LEE = 7   # janela do filtro de Lee (7x7)
ENL = 4.4         # "número equivalente de looks" típico do S1 IW GRD

# ---------------------------------------------------------------------------
# Máscara de água
# ---------------------------------------------------------------------------
LIMIAR_MIN_DB = -20.0   # o limiar de Otsu é "preso" entre estes valores
LIMIAR_MAX_DB = -15.0
MIN_PIXELS_OBJETO = 10  # manchas menores que isso são consideradas ruído
MORFOLOGIA_ELEMENTO = "cruz"  # janela 3x3 da limpeza: "cruz" ou "quadrado"
COBERTURA_MINIMA = 0.9  # fração mínima de pixels válidos dentro do polígono

# ---------------------------------------------------------------------------
# Qualidade da cena (núcleo de água permanente)
# ---------------------------------------------------------------------------
NUCLEO_FREQ_MIN = 0.9        # pixel é "água permanente" se foi água em >= 90% das datas
NUCLEO_EROSAO_PX = 2         # afasta o núcleo 2 pixels das margens
NUCLEO_MIN_DATAS = 5         # nº mínimo de datas para calcular o núcleo
LIMIAR_SUSPEITA_DB = 3.0     # VV do núcleo >= mediana + 3 dB => suspeita de vento/chuva

# ---------------------------------------------------------------------------
# Métricas de crista (observação, não é avaliação de segurança)
# ---------------------------------------------------------------------------
CRISTA_DISTANCIA_M = 30.0    # "água a menos de 30 m da crista"
CRISTA_PASSO_M = 5.0         # a linha é amostrada a cada 5 m
CRISTA_PRECISAO_M = 20       # precisão aproximada (pixel de 10 m + distorções do talude)

# ---------------------------------------------------------------------------
# Datas RGB de referência (conferência visual independente)
# ---------------------------------------------------------------------------
# 2 cenas na seca (jun–set) e 2 na chuva (dez–mar), a mais limpa de cada período
MESES_SECA = (6, 9)
MESES_CHUVA = (12, 3)
REFERENCIA_NUVEM_CENA_MAX = 60   # % de nuvem na cena para entrar como candidata
REFERENCIA_CANDIDATAS = 6        # quantas candidatas avaliar por período

# ---------------------------------------------------------------------------
# Detecção de anomalias
# ---------------------------------------------------------------------------
JANELA_MEDIANA = 5          # nº de passagens anteriores usadas como referência
LIMIAR_Z = 3.0              # |z robusto| acima disso = anomalia
LIMIAR_VARIACAO_PCT = 20.0  # variação entre passagens consecutivas (%)
# Em áreas pequenas a % explode (0,1 -> 0,2 ha = +100%). Abaixo de AREA_MIN_PCT_HA
# a regra passa a ser a variação ABSOLUTA. 1 ha = 20% de 5 ha: as duas regras
# coincidem na fronteira.
AREA_MIN_PCT_HA = 5.0
LIMIAR_VARIACAO_ABS_HA = 1.0
# Acima de 5 ha, a variação precisa passar de 20% E de 5 ha ao mesmo tempo
# (em poças de 15–30 ha, ±20% são só 3–6 ha e acontecem o tempo todo).
LIMIAR_VARIACAO_MIN_HA = 5.0
# Pisos do MAD no z robusto: 3% da mediana e 0,5 ha (vale o maior).
# Sem eles, numa série muito estável o MAD fica minúsculo e qualquer
# oscilação de poucos pixels vira |z| enorme.
MAD_MIN_REL = 0.03
MAD_MIN_HA = 0.5

# ---------------------------------------------------------------------------
# Classes da superfície em barragens de rejeito (S1 + S2)
# ---------------------------------------------------------------------------
CLASSES_TOLERANCIA_DIAS = 3   # S2 confiável a até 3 dias da passagem S1

# ---------------------------------------------------------------------------
# Controle de qualidade do S2 pela banda SCL
# ---------------------------------------------------------------------------
# Névoa ou fumaça fina podem passar pelo filtro de nuvem e "apagar" a água.
# Data "não confiável (SCL)" quando as DUAS condições acontecem juntas, dentro
# do núcleo de água do S2:
#   - a fração que a SCL chama de "água" (classe 6) < 50% da mediana da série;
#   - a fração que o nosso MNDWI detecta como água  < 80% da mediana da série.
# Só a SCL não basta: na Eustáquio ela não reconhece o lago verde e turvo em
# datas limpas (ex.: 20/10/2026), embora o MNDWI o detecte normalmente.
SCL_FRACAO_MIN_REL = 0.5
MNDWI_FRACAO_MIN_REL = 0.8

# ---------------------------------------------------------------------------
# Validação com Sentinel-2
# ---------------------------------------------------------------------------
NUVEM_MAX_S2 = 10               # % máxima de nuvem na CENA inteira (filtro de busca)
NUVEM_MAX_POLIGONO_PCT = 2.0    # % máxima de nuvem/sombra DENTRO do polígono
                                # acima disso a data S2 fica "não confiável"
LIMIAR_MNDWI = 0.0              # MNDWI > 0 => água
TOLERANCIA_DIAS_VALIDACAO = 6   # distância máxima entre datas S1 e S2

# ---------------------------------------------------------------------------
# Servidor web
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"  # 0.0.0.0 = aceita conexões de outras máquinas (seu Windows)
PORTA = 5000
