# SARgento – Monitoramento de barragens de rejeito por radar

Projeto acadêmico (ADS – UNIFADESA) que acompanha a superfície dos reservatórios
de barragens de rejeito com imagens de radar **Sentinel-1** (gratuitas, enxergam
através de nuvens, dia e noite), com validação por **Sentinel-2** (óptico).

> **Projeto acadêmico (ADS – UNIFADESA). Não é monitoramento oficial nem avaliação
> de segurança. A situação oficial das barragens está no SIGBM/ANM.**

O site tem três telas: **Início** (as estruturas em cards, com mini-mapa),
**Monitoramento** (mapa + séries, alertas, validação e relatório de cada estrutura)
e **Sobre o método** (dados, pipeline, validação, limitações e créditos). A pasta
do projeto continua se chamando `monitor-barragens`.

O que o sistema faz, para cada barragem:

1. Busca as cenas Sentinel-1 RTC (VV e VH) no **Microsoft Planetary Computer**, usando **uma única órbita**.
2. Aplica o **filtro de speckle de Lee 7×7** e converte para **decibéis (dB)**.
3. Separa água de não-água com o **limiar de Otsu** (preso entre −20 e −15 dB) e limpa a máscara com **morfologia**.
4. Calcula a área do **lago** e da **praia** (polígono − lago) em hectares.
5. Salva tudo num banco **SQLite**.
6. Detecta **anomalias** (mediana móvel + MAD com |z| > 3, e variação percentual entre passagens).
7. Mostra tudo num **mapa web** (Flask + Leaflet + Chart.js) e gera **relatórios HTML**.

---

## Estrutura (padrão MVC)

```
monitor-barragens/
├── main.py                 ← comece por aqui (linha de comando)
├── config.py               ← TODOS os parâmetros ajustáveis
├── requirements.txt
├── data/
│   ├── sigbm.csv           ← lista de barragens (nome, lat, lon, categoria_risco)
│   ├── poligonos.geojson   ← polígonos dos reservatórios (ligados pelo "nome")
│   ├── monitor.db          ← criado automaticamente (série temporal)
│   └── mascaras/           ← PNGs das máscaras de água (criado automaticamente)
├── reports/                ← relatórios HTML gerados
├── docs/paineis/           ← painéis RGB de conferência visual
├── tools/                  ← build_vitrine.py, servir_dist.py
├── dist/                   ← vitrine estática gerada (publicada na Vercel)
├── vercel.json             ← configuração da Vercel (sem build, cabeçalhos de segurança)
└── app/
    ├── models/             ← M: dados e ciência (não sabe nada de web)
    │   ├── barragem.py         lê CSV + GeoJSON
    │   ├── database.py         SQLite
    │   ├── geo.py              projeções UTM, rasterização
    │   ├── sentinel1.py        busca/carrega Sentinel-1
    │   ├── processamento.py    Lee, dB, Otsu, morfologia, áreas
    │   ├── anomalias.py        mediana móvel + MAD, variação %
    │   └── sentinel2.py        validação MNDWI
    ├── views/              ← V: o que o usuário vê
    │   ├── templates/          base, inicio, monitor, metodo, relatorio
    │   └── static/             css/sargento.css, js/, fontes, img/ (favicon) e vendor/ (Leaflet, Chart.js)
    └── controllers/        ← C: liga models às views
        ├── pipeline_controller.py   processamento (terminal)
        ├── relatorio_controller.py  relatórios HTML
        ├── servico_series.py        junta série + anomalias + validação
        └── web_controller.py        rotas Flask
```

**Por que MVC?** Cada parte tem uma responsabilidade. Se um dia você trocar o
Flask por outra coisa, os *models* continuam iguais. Se quiser mudar o filtro
de speckle, mexe só em `processamento.py`.

---

## Instalação na VM Ubuntu Server

```bash
# 1. Pacotes do sistema
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

# 2. Copie a pasta do projeto para a VM (ex.: via scp, git ou WinSCP) e entre nela
cd ~/monitor-barragens

# 3. Ambiente virtual (isola as bibliotecas deste projeto)
python3 -m venv .venv
source .venv/bin/activate        # repita este comando sempre que abrir um terminal novo

# 4. Bibliotecas
pip install --upgrade pip
pip install -r requirements.txt
```

> Copiando do Windows para a VM com o `scp` (no PowerShell, na pasta que contém o projeto):
> `scp -r monitor-barragens SEU_USUARIO@IP_DA_VM:~/`

Não é preciso criar conta nem chave: os dados do Planetary Computer são
públicos e a biblioteca `planetary-computer` gera os tokens sozinha.

---

## Primeiro teste: a estrutura de controle

A **Lagoa da Pampulha** (BH) é a estrutura de **controle** (`papel = controle`
no `sigbm.csv`). Ela **não é uma barragem de rejeito**: é um espelho d'água urbano
usado para validar o método fora de barragens de rejeito, onde o radar só pode
estar vendo água. O polígono é aproximado.

```bash
source .venv/bin/activate

# Confere se a barragem e o polígono foram lidos
python main.py barragens

# Processa 3 meses (comece pequeno!)
python main.py processar --inicio 2024-06-01 --fim 2024-08-31

# Opcional: também Sentinel-2 para validação
python main.py processar --inicio 2024-06-01 --fim 2024-08-31 --validar-s2

# Gera o relatório em reports/
python main.py relatorio

# Sobe a interface web
python main.py web
```

O que esperar: a área do lago deve ficar razoavelmente estável entre as datas,
e o limiar perto de −17 a −19 dB. Se der certo, amplie o período e troque pela
barragem real.

### Abrindo no navegador do Windows

1. Descubra o IP da VM: `hostname -I` (o primeiro endereço da lista; abaixo, `IP_DA_VM`).
2. Se o firewall estiver ativo: `sudo ufw allow 5000/tcp`.
3. No Windows, abra `http://IP_DA_VM:5000`.

> O servidor do Flask é de **desenvolvimento**. Para uso contínuo, veja abaixo
> como deixar o site sempre ligado com o gunicorn. Não exponha a porta para a
> internet sem autenticação.

### Site sempre ligado (serviço systemd)

O arquivo `/etc/systemd/system/monitor-barragens.service` faz o site subir
sozinho quando a VM liga, usando o gunicorn (servidor mais robusto):

```ini
[Unit]
Description=SARgento (Flask + gunicorn)
After=network-online.target
Wants=network-online.target

[Service]
User=SEU_USUARIO
WorkingDirectory=CAMINHO_DO_PROJETO
ExecStart=CAMINHO_DO_PROJETO/.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 "app:criar_app()"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Troque `SEU_USUARIO` pelo seu usuário na VM e `CAMINHO_DO_PROJETO` pelo caminho
completo da pasta do projeto (rode `pwd` dentro dela para ver).

Comandos úteis:

```bash
sudo systemctl status monitor-barragens     # está rodando?
sudo systemctl restart monitor-barragens    # depois de mudar o código
sudo journalctl -u monitor-barragens -n 50  # últimas mensagens/erros
```

---

## Cadastrando barragens reais

**`data/sigbm.csv`** (separado por vírgula, UTF-8):

```csv
nome,lat,lon,categoria_risco
Barragem Exemplo I,-20.1234,-43.5678,ALTO
```

**`data/poligonos.geojson`**: um *Feature* por reservatório, com a propriedade
`nome` **idêntica** à do CSV. Para desenhar o polígono, use o site
<https://geojson.io>: desenhe sobre a imagem de satélite, cole o nome em
`properties` e salve. Desenhe o contorno de **todo o reservatório** (lago +
praia de rejeito), sem incluir o maciço/talude da barragem.

Sem polígono, o sistema usa um círculo de 500 m em volta do ponto e avisa —
os números ficam apenas indicativos.

### Colunas opcionais do `sigbm.csv`

Podem ficar vazias. Para escrever "N/A", "NA" etc., pode usar o texto à vontade.

| Coluna | Exemplo | Efeito |
|---|---|---|
| `dano_potencial` | `Alto` | DPA do SIGBM, mostrado no site e no relatório |
| `situacao` | `Ativa; método Linha de Centro` | texto livre no relatório |
| `orbita` | `descending/126` | força a órbita do Sentinel-1 desta barragem |
| `mndwi_otsu_min`, `mndwi_otsu_max` | `0.15`, `0.30` | o S2 passa a usar **Otsu no MNDWI**, limitado à faixa (veja abaixo) |
| `serie_principal` | `crista` ou `area` | qual gráfico aparece primeiro no site |
| `observacao` | `O lago fica junto ao pé do dique...` | destacada no relatório |
| `tipo` | `rejeito` ou `lago` | em `rejeito`, a série S1 vira "superfície escura no radar" e são calculadas as classes da superfície |
| `mndwi_limiar` | `0.25` | limiar MNDWI **fixo** desta barragem (tem prioridade sobre o Otsu) |
| `mndwi_incerteza` | `0.20;0.25;0.30` | limiares usados na faixa de incerteza da área S2 |
| `fonte_alertas` | `S1` ou `S2` | série que gera os alertas (veja "Fonte dos alertas") |
| `ruido_ha` | `5` | ruído típico da medição: nenhuma regra alerta para mudanças menores que isso |

### Barragens de rejeito: o que o radar realmente mede

O rejeito saturado é liso e escuro no radar, como a água. Por isso, em
barragens com `tipo = rejeito`:

- a série S1 se chama **superfície escura no radar (água + rejeito saturado)**;
- o **lago** vem do Sentinel-2 (MNDWI), com um limiar calibrado em imagens RGB
  e uma **faixa de incerteza** (área recalculada com outros limiares);
- nas datas com S2 confiável a até 3 dias de uma passagem S1, o polígono é
  dividido em três **classes**: lago (água no S2), praia saturada (escuro no
  S1 e não água no S2) e praia seca (o resto).

Para isso o processamento guarda o MNDWI de cada cena em `data/rasters/*_S2.npz`.
Dados processados antes disso precisam de uma vez:

```bash
python main.py processar --apenas-s2 --inicio 2025-04-01 --fim 2026-11-23 --barragem "Eustáquio"
```

### Regras de alerta

- **z robusto:** |z| > 3, com MAD mínimo igual ao maior entre 3% da mediana e 0,5 ha
  (sem esse piso, séries muito estáveis geram |z| enorme por oscilações de pixel);
- **variação entre passagens:** com área anterior ≥ 5 ha, alerta quando a
  variação passa de **20% e de 5 ha ao mesmo tempo**; abaixo de 5 ha, alerta
  quando passa de **1 ha** (em áreas pequenas a % não tem sentido);
- **validação S1 × S2:** erro e viés sempre em **hectares**; a % só é
  calculada nas datas em que a área S2 passa de 5 ha.

**Ruído por estrutura (`ruido_ha`):** quando preenchido, a variação entre
passagens precisa passar desse valor e o piso do MAD vira `ruido_ha × 0,6745 / 3`
(assim |z| > 3 exige um desvio maior que o ruído). Como estimar: a diferença de
área entre datas S2 com até 5 dias de intervalo (p90) ou a dispersão da
validação S1 − S2. Valores atuais: lago oeste 5 ha (dispersão da validação de
2,4 ha) e Santo Antônio 7 ha (p90 de 6,7 ha entre datas S2 próximas).

### Revisão manual de alertas (`data/revisoes.json`, opcional)

Quando a conferência visual (RGB) mostra que um alerta é provável falso
positivo, registre-o aqui. O alerta continua listado com a justificativa, mas
sai da contagem:

```json
{"Barragem X": [{"data": "2025-09-30", "fonte": "S2",
  "classificacao": "provável falso positivo por turbidez",
  "motivo": "...", "revisado_em": "24/11/2026 (conferência visual RGB)"}]}
```

### Fonte dos alertas (`fonte_alertas` no `sigbm.csv`)

`S1` ou `S2`: qual série gera os alertas da estrutura. As anomalias da outra
série aparecem em cinza como **baixa confiabilidade** e não entram na contagem
(cabeçalho, mapa, tabela e relatório mostram sempre o mesmo número).
Use `S2` em barragens de rejeito, onde o S1 mede água + rejeito saturado.

### Controle de qualidade do S2 pela banda SCL

Névoa ou fumaça fina podem passar pelo filtro de nuvem e "apagar" parte do
lago no MNDWI. Para pegar esses casos, o programa monta um **núcleo de água do
S2**: pixels que foram água em ≥ 90% das datas sem nuvem, com erosão de 2 px.
Em cada data, mede duas frações desse núcleo: a que a própria ESA (banda SCL)
classificou como água e a que o nosso MNDWI detectou como água. A data vira
"não confiável (SCL)" quando **as duas ficam baixas ao mesmo tempo** (SCL abaixo
de 50% e MNDWI abaixo de 80% das respectivas medianas) e sai da validação,
das classes e dos alertas. Só a SCL não basta: na Eustáquio ela deixou de
reconhecer o lago verde e turvo em 4 cenas limpas, nas quais o MNDWI estava correto.
Estruturas sem núcleo de água estável no S2 (ex.: lagos que encheram no meio da
série) ficam sem essa verificação, e o log avisa.

### Limitações e eventos no relatório (`data/notas.json`, opcional)

Textos que aparecem nas seções "Limitações demonstradas" e "Eventos observados".
A chave `"todas"` vale para todas as barragens; as demais usam o `nome` do CSV:

```json
{"todas": {"limitacoes": [{"titulo": "...", "texto": "..."}]},
 "Barragem X": {"limitacoes": [...], "eventos": [{"titulo": "...", "texto": "..."}]}}
```

**Por que Otsu no MNDWI em barragem de rejeito?** A praia de rejeito úmida e
clara tem MNDWI positivo (cerca de +0,15 na Eustáquio). Com o limiar fixo
(MNDWI > 0), o S2 contaria quase toda a praia como lago. O Otsu procura, em
cada cena, o corte entre lago e praia, e a faixa impede valores absurdos.

### Linhas de crista (`data/cristas.geojson`, opcional)

Uma `LineString` por **trecho**, seguindo a **borda de montante** da crista
(o lado do reservatório), com as propriedades `nome` (igual ao CSV) e `trecho`.
Para cada passagem do S1, o sistema calcula, por trecho:

- **distância mínima** entre a água e a crista (vazia nas datas sem água);
- **extensão da crista com água a menos de 30 m**, em metros e em % do trecho.

Se a métrica não fizer sentido numa barragem (ex.: praia seca lisa, que também
fica escura no radar, encostada na crista), marque os trechos no GeoJSON com
`"interpretavel": false` e um `"nota"`. Os valores continuam sendo calculados,
mas aparecem com o aviso no site e no relatório.

> **Observação, não é avaliação de segurança.** Precisão aproximada de ±20 m
> (pixel de 10 m e distorções do radar no talude). Não informa borda livre,
> nível d'água, percolação nem estabilidade.

### Datas RGB de referência

```bash
python main.py referencias --barragem "Eustáquio"
```

Escolhe 4 cenas Sentinel-2 por barragem: as mais limpas **dentro do polígono**,
2 na seca (jun–set) e 2 na chuva (dez–mar). Para cada uma, gera um painel com
a imagem RGB e o polígono, a máscara S2 e a máscara S1 da passagem mais próxima.
Se o período tiver só uma estação de um tipo, ela é dividida em duas metades.
Os painéis vão para `data/referencias/` e entram no relatório. Eles existem
porque **rejeito encharcado pode enganar o S1 e o S2 ao mesmo tempo**, e a
imagem colorida é uma conferência independente.

---

## Atualização automática (opcional)

O Sentinel-1 passa a cada 6–12 dias na mesma órbita. Para processar sozinho
toda segunda-feira às 6h, rode `crontab -e` e acrescente:

```cron
0 6 * * 1 cd ~/monitor-barragens && .venv/bin/python main.py processar --inicio $(date -d '30 days ago' +\%F) --fim $(date +\%F) && .venv/bin/python main.py relatorio
```

Datas já processadas são puladas automaticamente (use `--reprocessar` para refazer).

---

## Ajustando parâmetros

Tudo está em `config.py`. Os mais importantes:

| Parâmetro | Padrão | Para que serve |
|---|---|---|
| `ORBITA_RELATIVA` / `DIRECAO_ORBITA` | automático | força uma órbita específica |
| `LIMIAR_MIN_DB` / `LIMIAR_MAX_DB` | −20 / −15 | trava do limiar de Otsu |
| `BUFFER_M` | 500 | margem em volta do polígono usada pelo Otsu |
| `COBERTURA_MINIMA` | 0,9 | descarta datas com falhas sobre o reservatório |
| `JANELA_MEDIANA` | 5 | nº de passagens anteriores usadas como "normal" |
| `LIMIAR_Z` | 3 | sensibilidade da regra MAD |
| `LIMIAR_VARIACAO_PCT` | 20 | variação máxima tolerada entre passagens |

> Se mudar a órbita, apague `data/monitor.db` (ou use `--reprocessar`) para
> não misturar geometrias diferentes na mesma série.

---

## Qualidade da cena e status das passagens

Cada passagem do Sentinel-1 recebe um status:

| Status | Significado | No gráfico |
|---|---|---|
| Normal | nada de especial | ponto azul |
| Anomalia | \|z\| > 3 ou variação > 20% | triângulo vermelho |
| Retorno ao normal | logo após uma anomalia, voltou ao nível de antes | círculo roxo vazado |
| Suspeita de vento/chuva | a água estava "encrespada" na hora da passagem | × cinza |

**Como a suspeita é detectada:** o programa monta um *núcleo de água
permanente*, formado pelos pixels que foram água em pelo menos 90% das datas,
afastado 2 pixels das margens. Água calma tem VV baixo (cerca de −20 dB). Se, numa
passagem, a mediana do VV nesse núcleo fica **3 dB ou mais acima** da mediana
da série, a água estava agitada por vento ou chuva, e a área medida naquele dia não
é confiável. Essas passagens ficam **fora** do cálculo de anomalias.

O núcleo precisa dos arquivos `data/rasters/*.npz`, gerados a cada passagem
processada. Para dados processados antes dessa função, rode uma vez:

```bash
python main.py processar --inicio 2023-01-01 --fim 2024-12-31 --reprocessar
```

Depois, `python main.py qualidade` recalcula o núcleo sem baixar nada.

---

## Cobertura do Sentinel-1: atenção em Paracatu (MG)

A frequência de passagens depende de quais satélites Sentinel-1 estão
ativos e do plano de aquisição da ESA, que não cobre todas as regiões igualmente.
Em Paracatu (barragens Eustáquio e Santo Antônio), a consulta ao Planetary
Computer (coleções GRD e RTC, órbita descendente 126) mostrou:

| Período | Satélite | Passagens |
|---|---|---|
| até dez/2021 | Sentinel-1B | a cada 12 dias (28 datas em 2021) |
| jan/2022 a mar/2025 | — | **nenhuma**: o S1B parou em dez/2021 e o S1A não cobria a região |
| a partir de abr/2025 | Sentinel-1C | a cada 12 dias |

Por isso a série dessas barragens começa em **2025-04-01**, na órbita
**descendente 126**. Antes de cadastrar uma barragem em região nova,
verifique a cobertura do período desejado.

---

## Publicação (vitrine estática na Vercel)

A Vercel não roda o pipeline (SQLite, rasters, GDAL/rasterio pesados, sistema de
arquivos somente leitura). Por isso o processamento continua na VM e publicamos
uma **vitrine estática**: as mesmas telas, gráficos e relatórios, lendo arquivos
`.json` no lugar da API. Todas as estruturas entram (monitoradas e de controle);
as ações de processamento ficam escondidas e o cabeçalho avisa que é uma demonstração.

```bash
.venv/bin/python tools/build_vitrine.py     # gera dist/ e confere os links no fim
.venv/bin/python tools/servir_dist.py 8200  # (opcional) vê dist/ com os cabeçalhos da Vercel
```

A pasta `dist/` vai para o repositório e a Vercel publica o que está nela, sem
rodar nada (configuração em `vercel.json`). Depois de reprocessar, gere a vitrine
de novo e envie as mudanças.

## Limitações importantes (leia!)

- **Rejeito saturado** (praia muito molhada e lisa) pode ficar escuro no radar e
  ser confundido com água → lago superestimado.
- **Vento** cria ondinhas na água, deixando-a mais clara → lago subestimado.
- **Relevo íngreme** gera sombra de radar (também escura). O produto RTC corrige
  o brilho do terreno, mas não elimina a sombra.
- O polígono é desenhado à mão: um contorno ruim afeta diretamente a área de praia.
- As anomalias são **sinais para investigação**, não diagnóstico de segurança.
  Sempre confirme com inspeção visual (use a camada "Satélite" e o Sentinel-2)
  e com a equipe responsável pela barragem.

---

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `Nenhuma cena Sentinel-1` | período curto demais ou bbox fora da cobertura |
| Todas as datas "descartadas" | polígono parcialmente fora da cena; tente outra órbita |
| Lago com área zero | polígono no lugar errado (confira em geojson.io) |
| Página abre mas o mapa está vazio | rode `processar` antes; confira `python main.py barragens` |
| Não abre do Windows | firewall (`sudo ufw allow 5000/tcp`) ou servidor iniciado sem `--host 0.0.0.0` |
