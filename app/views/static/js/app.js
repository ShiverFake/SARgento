// =========================================================================
// SARgento – tela de monitoramento: mapa (Leaflet) + gráficos (Chart.js)
// O navegador pede os dados à API do Flask (servidor na VM) ou, na vitrine
// estática, aos arquivos .json gerados por tools/build_vitrine.py.
// =========================================================================

// ---------- Aparência geral dos gráficos ----------
Chart.defaults.font.family = '"Plex", "Segoe UI", system-ui, sans-serif';
Chart.defaults.font.size = 11.5;
Chart.defaults.color = '#56656f';
// Legendas compactas, com o mesmo símbolo de cada série (linha, quadrado, triângulo...)
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.boxWidth = 9;
Chart.defaults.plugins.legend.labels.boxHeight = 9;
Chart.defaults.plugins.legend.labels.padding = 10;
// Balões (tooltips) com fundo opaco e escuro, texto claro: legíveis sobre qualquer linha.
Chart.defaults.plugins.tooltip.backgroundColor = '#14212b';
Chart.defaults.plugins.tooltip.titleColor = '#ffffff';
Chart.defaults.plugins.tooltip.bodyColor = '#ffffff';
Chart.defaults.plugins.tooltip.footerColor = '#ffffff';
Chart.defaults.plugins.tooltip.borderColor = '#ffffff';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.titleFont = { size: 13, weight: 'bold' };
Chart.defaults.plugins.tooltip.bodyFont = { size: 13 };
Chart.defaults.plugins.tooltip.boxPadding = 4;

// ---------- Endereços dos dados ----------
// window.SARGENTO vem do template base: { base: "/" ou "../..", vitrine: true/false }.
// No servidor: /api/barragens. Na vitrine: <base>api/barragens.json (mesmo caminho + .json).
const CFG = window.SARGENTO || { base: '/', vitrine: false };
const VITRINE = CFG.vitrine === true;
const BASE = CFG.base || '/';
const urlApi = caminho => (VITRINE ? `${BASE}api/${caminho}.json` : `/api/${caminho}`);
const urlBarragens = () => urlApi('barragens');
const urlSerie = slug => urlApi(`barragens/${slug}/serie`);
// Relatório: no servidor é gerado na hora; na vitrine é um arquivo pronto.
const urlRelatorio = slug => (VITRINE ? `${BASE}relatorios/${slug}.html` : `/api/barragens/${slug}/relatorio`);
// Arquivos que a API devolve com caminho absoluto ("/mascaras/x.png") viram relativos na vitrine.
const urlArquivo = url => (url && url.startsWith('/') ? BASE + url.slice(1) : url);

// ---------- Mapa ----------
const mapa = L.map('mapa').setView([-19.9, -44.0], 7);

const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}).addTo(mapa);

const satelite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, attribution: 'Imagens &copy; Esri' });

L.control.layers({ 'Mapa (OSM)': osm, 'Satélite (Esri)': satelite }).addTo(mapa);

// Estado atual da tela
let grafico = null;          // objeto Chart.js
let camadaMascara = null;    // imagem PNG sobreposta
let camadaPoligono = null;   // contorno do reservatório
let serieAtual = null;       // dados da barragem selecionada
let barragemAtual = null;    // cadastro da barragem selecionada (tipo, cristas...)

// ---------- Utilidades ----------
function semAcento(t) {
  return (t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toUpperCase();
}

// Cor do marcador: categoria de risco (CRI) das monitoradas; azul-aço para controle.
function corRisco(categoria, papel) {
  if (papel === 'controle') return '#2f6f8f';
  const c = semAcento(categoria);
  if (c.includes('ALTO')) return '#d7263d';
  if (c.includes('MEDIO')) return '#f49d37';
  if (c.includes('BAIXO')) return '#3f88c5';
  return '#777';
}

// Evita que textos vindos dos dados quebrem o HTML montado aqui.
function esc(t) {
  return String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

const SELO = {
  controle: '<span class="selo selo-controle">Controle</span>',
  monitorada: '<span class="selo selo-monitorada">Monitorada</span>',
};

// Números no padrão brasileiro: 1.234,56
// toLocaleString('pt-BR') já troca ponto por vírgula para nós.
function fmt(v, casas = 2, sinal = false) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '–';
  const texto = Number(v).toLocaleString('pt-BR', {
    minimumFractionDigits: casas, maximumFractionDigits: casas
  });
  return sinal && v > 0 ? '+' + texto : texto;
}

function dataBR(iso) {
  const [a, m, d] = iso.split('-');
  return `${d}/${m}/${a}`;
}

// ---------- 1. Carrega as barragens e coloca no mapa ----------
let todasBarragens = [];

async function carregarBarragens() {
  const resp = await fetch(urlBarragens());
  if (!resp.ok) throw new Error(`Não foi possível carregar as estruturas (HTTP ${resp.status}).`);
  const barragens = await resp.json();
  todasBarragens = barragens;
  const pontos = [];

  barragens.forEach(b => {
    const marcador = L.circleMarker([b.lat, b.lon], {
      radius: 9, color: '#fff', weight: 2,
      fillColor: corRisco(b.categoria_risco, b.papel), fillOpacity: 0.95
    }).addTo(mapa);

    marcador.bindTooltip(
      `<b>${esc(b.nome)}</b> (${b.papel === 'controle' ? 'controle' : 'monitorada'})<br>` +
      (b.papel === 'controle' ? '' : `CRI ${esc(b.categoria_risco)}, DPA ${esc(b.dano_potencial || '–')}<br>`) +
      `Última passagem: ${b.ultima_data ? dataBR(b.ultima_data) : 'sem dados'}<br>` +
      `Anomalias (fonte ${b.fonte_alertas}): ${b.n_anomalias}`);
    marcador.on('click', () => selecionarBarragem(b));
    pontos.push([b.lat, b.lon]);
  });

  // Botões para trocar de estrutura sem procurar no mapa
  const seletor = document.getElementById('seletorEstrutura');
  seletor.innerHTML = barragens.map(b =>
    `<button type="button" data-slug="${b.slug}" aria-pressed="false">${esc(b.nome)}</button>`).join('');
  seletor.querySelectorAll('button').forEach(bt => bt.addEventListener('click', () =>
    selecionarBarragem(barragens.find(b => b.slug === bt.dataset.slug))));

  // Página de uma estrutura (/estrutura/<slug>/): já abre com ela selecionada
  const inicial = document.body.dataset.estrutura || new URLSearchParams(location.search).get('estrutura');
  const alvo = barragens.find(b => b.slug === inicial);
  if (alvo) selecionarBarragem(alvo);
  else if (pontos.length) mapa.fitBounds(pontos, { padding: [40, 40], maxZoom: 13 });
}

// ---------- 2. Seleciona uma barragem ----------
async function selecionarBarragem(b) {
  document.getElementById('vazio').hidden = true;
  document.getElementById('detalhe').hidden = false;
  document.getElementById('detalheDados').hidden = false;
  document.getElementById('titulo').textContent = b.nome;
  document.getElementById('seloPapel').innerHTML = SELO[b.papel] || SELO.monitorada;
  document.getElementById('linkRelatorio').href = urlRelatorio(b.slug);
  document.querySelectorAll('#seletorEstrutura button').forEach(bt =>
    bt.setAttribute('aria-pressed', String(bt.dataset.slug === b.slug)));

  // Contorno do reservatório + linhas de crista (magenta)
  if (camadaPoligono) mapa.removeLayer(camadaPoligono);
  camadaPoligono = L.featureGroup().addTo(mapa);
  if (b.poligono) {
    L.geoJSON(b.poligono, { style: { color: '#ffd166', weight: 2, fill: false } }).addTo(camadaPoligono);
  }
  Object.entries(b.cristas || {}).forEach(([trecho, linha]) => {
    L.geoJSON(linha, { style: { color: '#ff00ff', weight: 4 } })
      .bindTooltip(`Crista: ${trecho}`).addTo(camadaPoligono);
  });
  if (b.poligono) mapa.fitBounds(camadaPoligono.getBounds(), { padding: [30, 30] });
  else mapa.setView([b.lat, b.lon], 14);

  const resp = await fetch(urlSerie(b.slug));
  if (!resp.ok) throw new Error(`Não foi possível carregar a série de ${b.nome} (HTTP ${resp.status}).`);
  serieAtual = await resp.json();

  // Ficha da estrutura: um dado por célula, rótulo em cima
  const s1 = serieAtual.s1;
  const nSusp = s1.filter(d => d.suspeita).length;
  const orbita = s1.length ? s1[0].orbita.replace('descending', 'descendente').replace('ascending', 'ascendente').replace('/', ' ') : '–';
  const itens = [
    ['CRI', b.papel === 'controle' ? 'não se aplica' : b.categoria_risco],
    ['DPA', b.papel === 'controle' ? 'não se aplica' : (b.dano_potencial || '–')],
    ['Órbita S1', orbita],
    ['Passagens S1', s1.length ? `${s1.length} (${dataBR(s1[0].data)} a ${dataBR(s1[s1.length - 1].data)})` : 'nenhuma'],
    ['Polígono', s1.length ? `${fmt(s1[0].area_poligono_ha, 1)} ha` : '–'],
    ['Fonte dos alertas', serieAtual.fonte_alertas === 'S2' ? 'S2 (lago)' : 'S1 (radar)'],
    ['Anomalias', String(serieAtual.n_anomalias)],
    ['Eventos', String((serieAtual.eventos || []).length)],
  ];
  if (nSusp) itens.push(['Suspeitas vento/chuva', String(nSusp)]);
  document.getElementById('ficha').innerHTML = itens
    .map(([r, v]) => `<div><dt>${r}</dt><dd>${esc(v)}</dd></div>`).join('');
  const obs = document.getElementById('observacao');
  obs.hidden = !b.observacao;
  obs.textContent = b.observacao || '';
  if (!s1.length) document.getElementById('ficha').insertAdjacentHTML('afterend',
    '<p class="dica">Nenhuma passagem processada ainda. Rode o pipeline no servidor.</p>');

  barragemAtual = b;
  const rejeito = b.tipo === 'rejeito';
  document.getElementById('tituloArea').textContent = rejeito
    ? 'Superfície escura no radar (água + rejeito saturado) e lago S2'
    : 'Área do lago e da praia';
  document.getElementById('avisoRejeito').hidden = !rejeito;

  // Ordem dos gráficos: a "série principal" da barragem vem primeiro
  const blocoArea = document.getElementById('blocoArea');
  const blocoCrista = document.getElementById('blocoCrista');
  const temCrista = Object.keys(serieAtual.crista || {}).length > 0;
  blocoCrista.hidden = !temCrista;
  const avisoCrista = document.getElementById('avisoCrista');
  avisoCrista.hidden = b.crista_interpretavel !== false;
  avisoCrista.textContent = b.crista_nota ? `Métrica ${b.crista_nota}.` : '';
  document.getElementById('blocoClasses').hidden = !(serieAtual.classes || []).length;
  const graficos = document.getElementById('graficos');
  if (temCrista && b.serie_principal === 'crista') graficos.prepend(blocoCrista);
  else graficos.prepend(blocoArea);

  preencherSeletor();
  desenharGrafico();
  desenharGraficoClasses();
  desenharGraficoCrista();
  preencherTabelas();

  // Mostra a máscara mais recente por padrão
  if (s1.length) mostrarMascara('S1', s1[s1.length - 1].data);
}

// ---------- 3. Seletor de datas ----------
function preencherSeletor() {
  const sel = document.getElementById('seletorData');
  sel.innerHTML = '';
  const opcoes = [
    ...serieAtual.s1.map(d => ({ sensor: 'S1', data: d.data, status: d.status })),
    ...serieAtual.s2.map(d => ({ sensor: 'S2', data: d.data, status: 'normal' })),
  ].sort((a, b) => b.data.localeCompare(a.data));   // mais recente primeiro

  const marcas = { anomalia: ' ⚠', suspeita: ' (vento/chuva?)', retorno: ' (retorno)' };
  opcoes.forEach(o => {
    const opt = document.createElement('option');
    opt.value = `${o.sensor}|${o.data}`;
    opt.textContent = `${dataBR(o.data)}, ${o.sensor}${marcas[o.status] || ''}`;
    sel.appendChild(opt);
  });
}

document.getElementById('seletorData').addEventListener('change', e => {
  const [sensor, data] = e.target.value.split('|');
  mostrarMascara(sensor, data);
});

document.getElementById('mostrarMascara').addEventListener('change', e => {
  if (!camadaMascara) return;
  if (e.target.checked) camadaMascara.addTo(mapa); else mapa.removeLayer(camadaMascara);
});

// ---------- 4. Sobreposição da máscara ----------
function mostrarMascara(sensor, data) {
  const lista = sensor === 'S2' ? serieAtual.s2 : serieAtual.s1;
  const obs = lista.find(d => d.data === data);
  if (!obs || !obs.mascara_url || !obs.limites) return;

  if (camadaMascara) mapa.removeLayer(camadaMascara);
  // imageOverlay "cola" o PNG no mapa entre os cantos [[sul,oeste],[norte,leste]]
  camadaMascara = L.imageOverlay(urlArquivo(obs.mascara_url), obs.limites, { opacity: 0.75 });
  if (document.getElementById('mostrarMascara').checked) camadaMascara.addTo(mapa);

  document.getElementById('seletorData').value = `${sensor}|${data}`;
}

// ---------- 5. Gráfico ----------
function desenharGrafico() {
  const { s1, s2 } = serieAtual;
  const limiteNuvem = serieAtual.nuvem_max_poligono_pct;

  // Com eixo do tipo "time", cada ponto é {x: data, y: valor}.
  // Assim o espaço entre os pontos é proporcional ao tempo real
  // (um buraco de 2 meses sem imagem aparece como buraco).
  const pontos = (lista, campo) => lista.map(d => ({ x: d.data, y: d[campo], obs: d }));
  // Confiável = pouca nuvem no polígono E aprovado no controle de qualidade da SCL
  const s2Confiavel = d => !d.motivo_nao_confiavel;

  // As linhas ligam só as passagens confiáveis; as suspeitas de vento/chuva
  // aparecem soltas, com um "×" cinza, para não desenhar uma queda falsa.
  const s1Confiavel = s1.filter(d => !d.suspeita);
  const rejeito = barragemAtual && barragemAtual.tipo === 'rejeito';
  const conjuntos = [
    { label: rejeito ? 'Superfície escura S1: água + rejeito saturado (ha)' : 'Lago S1 (ha)',
      data: pontos(s1Confiavel, 'area_lago_ha'), yAxisID: 'y',
      borderColor: '#1e78c8', backgroundColor: '#1e78c8', tension: 0.2, pointRadius: 2 },
    { label: (rejeito ? 'Restante do polígono S1 (ha)' : 'Praia S1 (ha)') + ' – eixo direito',
      data: pontos(s1Confiavel, 'area_praia_ha'), yAxisID: 'y2',
      borderColor: '#c9a227', backgroundColor: '#c9a227', tension: 0.2, pointRadius: 2,
      borderDash: [5, 4] },
    // Anomalias S1: vermelhas se o S1 é a fonte principal; senão cinza vazadas
    // ("baixa confiabilidade", fora da contagem do cabeçalho).
    (() => {
      const principal = serieAtual.fonte_alertas === 'S1';
      return { label: principal ? 'Anomalia S1' : 'Anomalia S1 (baixa confiabilidade)',
        data: pontos(s1.filter(d => d.status === 'anomalia'), 'area_lago_ha'),
        yAxisID: 'y', showLine: false, pointRadius: 7, pointStyle: 'triangle', borderWidth: 2,
        backgroundColor: principal ? '#d7263d' : 'rgba(0,0,0,0)', borderColor: principal ? '#d7263d' : '#9aa3ad' };
    })(),
    { label: 'Retorno ao normal', data: pontos(s1.filter(d => d.status === 'retorno'), 'area_lago_ha'),
      yAxisID: 'y', showLine: false, pointRadius: 6, pointStyle: 'circle', borderWidth: 2,
      backgroundColor: 'rgba(0,0,0,0)', borderColor: '#7b4fb3' },
    { label: 'Suspeita de vento/chuva', data: pontos(s1.filter(d => d.suspeita), 'area_lago_ha'),
      yAxisID: 'y', showLine: false, pointRadius: 7, pointStyle: 'crossRot', borderWidth: 2,
      backgroundColor: '#6b7280', borderColor: '#6b7280' },
  ];
  if (s2.length) {
    const lim = barragemAtual && barragemAtual.mndwi_limiar !== null && barragemAtual.mndwi_limiar !== undefined
      ? ` > ${fmt(barragemAtual.mndwi_limiar, 2)}` : '';
    conjuntos.push({
      label: `Lago S2 / MNDWI${lim} (ha)`, data: pontos(s2, 'area_lago_ha'), yAxisID: 'y',
      showLine: false, pointRadius: 5, pointStyle: 'rect', borderColor: '#2a9d8f',
      // Quadrado cheio = confiável; vazado = nuvem demais no polígono
      pointBackgroundColor: s2.map(d => (s2Confiavel(d) ? '#2a9d8f' : 'rgba(0,0,0,0)')),
    });
    // Anomalias S2 (losango): vermelhas se o S2 é a fonte principal
    const principalS2 = serieAtual.fonte_alertas === 'S2';
    // Cor por ponto: vermelho cheio = alerta que conta; cinza vazado = baixa
    // confiabilidade ou revisado como provável falso positivo.
    const anomS2 = s2.filter(d => d.status === 'anomalia');
    const conta = d => d.confiabilidade === 'principal';
    conjuntos.push({
      label: principalS2 ? 'Anomalia S2' : 'Anomalia S2 (baixa confiabilidade)',
      data: pontos(anomS2, 'area_lago_ha'), yAxisID: 'y',
      showLine: false, pointRadius: 8, pointStyle: 'rectRot', borderWidth: 2,
      pointBackgroundColor: anomS2.map(d => (conta(d) ? '#d7263d' : 'rgba(0,0,0,0)')),
      pointBorderColor: anomS2.map(d => (conta(d) ? '#d7263d' : '#9aa3ad')),
      backgroundColor: '#d7263d', borderColor: '#d7263d' });
    // Faixa de incerteza do S2: área com o menor e o maior limiar (só datas confiáveis)
    const sens = serieAtual.sensibilidade || {};
    const chaves = Object.keys(sens).sort();
    const confiaveis = new Set(s2.filter(s2Confiavel).map(d => d.data));
    [chaves[0], chaves[chaves.length - 1]].filter((k, i, a) => k && a.indexOf(k) === i).forEach(k => {
      conjuntos.push({
        label: `S2 com MNDWI > ${k.replace('.', ',')} (faixa de incerteza)`, yAxisID: 'y',
        data: sens[k].filter(d => confiaveis.has(d.data)).map(d => ({ x: d.data, y: d.area_lago_ha, obs: d })),
        borderColor: 'rgba(42,157,143,0.45)', backgroundColor: 'rgba(42,157,143,0.45)',
        borderDash: [2, 3], borderWidth: 1, pointRadius: 0 });
    });
  }

  if (grafico) grafico.destroy();
  grafico = new Chart(document.getElementById('grafico'), {
    type: 'line',
    data: { datasets: conjuntos },
    options: {
      locale: 'pt-BR',             // vírgula decimal nos eixos e balões
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: {
          type: 'time',
          time: {
            tooltipFormat: 'dd/MM/yyyy',
            displayFormats: { day: 'dd/MM', week: 'dd/MM', month: 'MM/yyyy', quarter: 'MM/yyyy' }
          }
        },
        y: { position: 'left', title: { display: true, text: 'Lago (ha)' } },
        // Eixo secundário: a praia costuma ter escala bem diferente do lago
        y2: {
          position: 'right', title: { display: true, text: 'Praia (ha)' },
          grid: { drawOnChartArea: false }   // evita duas grades sobrepostas
        }
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => {
              const d = ctx.raw.obs;
              let texto = `${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`;
              if (ctx.dataset.label.startsWith('Lago S2')) {
                texto += `, nuvem ${fmt(d.nuvem_poligono_pct, 1)}%` +
                  (s2Confiavel(d) ? '' : ` (não confiável: ${d.motivo_nao_confiavel})`);
              }
              return texto;
            },
            afterBody: itens => {
              const d = itens[0].raw.obs;
              if (!d.status || d.status === 'normal') return '';
              return `${d.status_texto}: ${d.motivo}`;
            }
          }
        }
      },
      onClick: (_evt, elementos) => {
        if (!elementos.length) return;
        const el = elementos[0];
        const d = conjuntos[el.datasetIndex].data[el.index].obs;
        mostrarMascara(s2.includes(d) ? 'S2' : 'S1', d.data);
      }
    }
  });
}

// ---------- 5a. Classes da superfície (barragens de rejeito) ----------
let graficoClasses = null;
function desenharGraficoClasses() {
  if (graficoClasses) { graficoClasses.destroy(); graficoClasses = null; }
  const c = serieAtual.classes || [];
  if (!c.length) return;
  const serie = (campo, rotulo, cor) => ({
    label: rotulo, data: c.map(d => ({ x: d.data_s1, y: d[campo], obs: d })),
    borderColor: cor, backgroundColor: cor + '55', fill: true, pointRadius: 2, tension: 0.15 });
  graficoClasses = new Chart(document.getElementById('graficoClasses'), {
    type: 'line',
    data: { datasets: [
      serie('lago_ha', 'Lago (água no S2)', '#1e78c8'),
      serie('praia_saturada_ha', 'Praia saturada (escuro S1, não água S2)', '#8a5a2b'),
      serie('praia_seca_ha', 'Praia seca', '#d9b44a') ] },
    options: {
      locale: 'pt-BR', maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { type: 'time', time: { tooltipFormat: 'dd/MM/yyyy',
             displayFormats: { day: 'dd/MM', week: 'dd/MM', month: 'MM/yyyy', quarter: 'MM/yyyy' } } },
        y: { stacked: true, title: { display: true, text: 'hectares (empilhado)' } } },
      plugins: { tooltip: { callbacks: {
        label: ctx => `${ctx.dataset.label}: ${fmt(ctx.parsed.y)} ha`,
        afterBody: it => { const d = it[0].raw.obs;
          return `S1 ${dataBR(d.data_s1)} × S2 ${dataBR(d.data_s2)} (${d.dias >= 0 ? '+' : ''}${d.dias} d)`; } } } }
    }
  });
}

// ---------- 5b. Gráfico da crista ----------
// Eixo esquerdo: % da crista com água a menos de 30 m (série principal).
// Eixo direito: menor distância água–crista (m), tracejada.
let graficoCrista = null;
const CORES_TRECHO = ['#d7263d', '#1e78c8', '#2a9d8f', '#7b4fb3', '#c9a227'];

function desenharGraficoCrista() {
  if (graficoCrista) { graficoCrista.destroy(); graficoCrista = null; }
  const crista = serieAtual.crista || {};
  const trechos = Object.keys(crista).filter(t => t !== 'total');
  if (!trechos.length) return;
  document.getElementById('precisaoCrista').textContent = serieAtual.crista_precisao_m;
  const limite = fmt(serieAtual.crista_limite_m, 0);

  const conjuntos = [];
  trechos.forEach((tr, i) => {
    const cor = CORES_TRECHO[i % CORES_TRECHO.length];
    const linhas = crista[tr];
    conjuntos.push({
      label: `${tr}: água a < ${limite} m (% da crista)`, yAxisID: 'y',
      data: linhas.map(d => ({ x: d.data, y: d.ext_30m_pct, obs: d })),
      borderColor: cor, backgroundColor: cor, pointRadius: 2, tension: 0.15 });
    conjuntos.push({
      label: `${tr}: distância mínima (m)`, yAxisID: 'y2',
      // null = sem água naquela data: a linha fica interrompida (spanGaps: false)
      data: linhas.map(d => ({ x: d.data, y: d.dist_min_m, obs: d })),
      borderColor: cor, backgroundColor: cor, borderDash: [4, 4], pointRadius: 1, spanGaps: false });
  });

  graficoCrista = new Chart(document.getElementById('graficoCrista'), {
    type: 'line',
    data: { datasets: conjuntos },
    options: {
      locale: 'pt-BR', maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: { type: 'time', time: { tooltipFormat: 'dd/MM/yyyy',
             displayFormats: { day: 'dd/MM', week: 'dd/MM', month: 'MM/yyyy', quarter: 'MM/yyyy' } } },
        y: { position: 'left', min: 0, max: 100, title: { display: true, text: `% da crista com água a < ${limite} m` } },
        y2: { position: 'right', min: 0, title: { display: true, text: 'distância mínima (m)' },
              grid: { drawOnChartArea: false } }
      },
      plugins: {
        tooltip: { callbacks: {
          label: ctx => {
            const d = ctx.raw.obs;
            return ctx.dataset.yAxisID === 'y'
              ? `${ctx.dataset.label}: ${fmt(d.ext_30m_pct, 1)}% (${fmt(d.ext_30m_m, 0)} m de ${fmt(d.comprimento_m, 0)} m)`
              : `${ctx.dataset.label}: ${d.dist_min_m === null ? 'sem água' : fmt(d.dist_min_m, 0) + ' m'}`;
          } } }
      },
      onClick: (_e, el) => {
        if (el.length) mostrarMascara('S1', conjuntos[el[0].datasetIndex].data[el[0].index].x);
      }
    }
  });
}

// ---------- 6. Tabelas ----------
function preencherTabelas() {
  // Ocorrências das duas fontes; as da fonte não principal ficam em cinza
  const corpoA = document.querySelector('#tabelaAnomalias tbody');
  const ocorrencias = serieAtual.ocorrencias || [];
  // Mesma contagem do cabeçalho, do mapa e do relatório: só a fonte principal
  const c = serieAtual.contagem || { contam: 0, revisados: 0, baixa: 0 };
  const secundaria = serieAtual.fonte_alertas === 'S2' ? 'S1' : 'S2';
  const eventos = serieAtual.eventos || [];
  document.getElementById('contagemAnomalias').innerHTML =
    `<table><thead><tr><th>Contam (${serieAtual.fonte_alertas})</th>` +
    `<th>Fora: revisado manualmente</th><th>Fora: baixa confiabilidade (fonte secundária)</th>` +
    `<th>Eventos</th></tr></thead>` +
    `<tbody><tr><td class="num"><b>${c.contam}</b></td><td class="num">${c.revisados}</td>` +
    `<td class="num">${c.baixa}</td><td class="num">${eventos.length}</td></tr></tbody></table>` +
    `<p class="dica"><b>Revisado manualmente:</b> alerta da fonte principal que a conferência visual ` +
    `do RGB classificou como provável falso positivo (justificativa no balão do selo). ` +
    `<b>Baixa confiabilidade:</b> alerta da série secundária (${secundaria}), mostrado só para comparação. ` +
    `<b>Evento:</b> alertas consecutivos da fonte principal agrupados.` +
    (eventos.length ? ' Eventos: ' + eventos.map(e => e.n_datas > 1
      ? `${dataBR(e.inicio)}–${dataBR(e.fim)} (${e.n_datas} datas)` : dataBR(e.inicio)).join('; ') : '') +
    `</p>`;
  corpoA.innerHTML = ocorrencias.length
    ? ocorrencias.map(d => {
        const baixa = d.confiabilidade === 'baixa';
        const revisada = d.confiabilidade === 'revisada';
        const selo = baixa ? ' <span class="selo">baixa confiabilidade</span>'
          : revisada ? ` <span class="selo" title="${d.revisao}">revisado: provável falso positivo</span>` : '';
        return `<tr class="${baixa || revisada || d.status === 'suspeita' ? 'nao-confiavel' : ''}">` +
          `<td>${dataBR(d.data)}</td><td>${d.fonte}</td>` +
          `<td>${d.status_texto}${selo}</td>` +
          `<td class="num">${fmt(d.area_ha)}</td><td>${d.motivo}</td></tr>`;
      }).join('')
    : '<tr><td colspan="5">Nenhuma ocorrência.</td></tr>';

  const v = serieAtual.validacao;
  document.getElementById('notaValidacao').textContent =
    `Datas com mais de ${fmt(serieAtual.nuvem_max_poligono_pct, 0)}% de nuvem/sombra no polígono ` +
    'ficam fora do erro médio.';

  const corpoV = document.querySelector('#tabelaValidacao tbody');
  corpoV.innerHTML = v.length
    ? v.map(d => `<tr class="${d.confiavel ? '' : 'nao-confiavel'}">` +
        `<td>${dataBR(d.data_s2)}${d.confiavel ? '' : ` <span class="selo">não confiável (${d.motivo_nao_confiavel})</span>`}</td>` +
        `<td>${dataBR(d.data_s1)}</td><td class="num">${fmt(d.nuvem_poligono_pct, 1)}%</td>` +
        `<td class="num">${fmt(d.lago_s1_ha)}</td><td class="num">${fmt(d.lago_s2_ha)}</td>` +
        `<td class="num">${fmt(d.diferenca_ha, 2, true)}</td>` +
        `<td class="num">${d.diferenca_pct === null ? '–' : fmt(d.diferenca_pct, 1, true) + '%'}</td></tr>`).join('')
    : '<tr><td colspan="7">Sem dados S2 (rode o pipeline com --validar-s2).</td></tr>';

  // Em ha para todas as datas confiáveis; em % só onde a área S2 passa de 5 ha
  const val = (x, casas, suf, sinal) => (x === null ? '–' : fmt(x, casas, sinal) + suf);
  const minPct = fmt(serieAtual.area_min_pct_ha, 0);
  document.querySelector('#tabelaValidacao tfoot').innerHTML = v.length
    ? `<tr><td colspan="5">Erro médio absoluto (${serieAtual.n_validas} data(s) confiável(is))</td>` +
      `<td class="num">${val(serieAtual.erro_medio_abs_ha, 2, ' ha', false)}</td>` +
      `<td class="num">${val(serieAtual.erro_medio_abs_pct, 1, '%', false)}</td></tr>` +
      `<tr><td colspan="5">Viés médio S1 − S2 (% só nas ${serieAtual.n_validas_pct} datas com S2 &gt; ${minPct} ha)</td>` +
      `<td class="num">${val(serieAtual.vies_medio_ha, 2, ' ha', true)}</td>` +
      `<td class="num">${val(serieAtual.vies_medio_pct, 1, '%', true)}</td></tr>`
    : '';
}

carregarBarragens().catch(err => {
  // Mostra o problema na própria tela, com o que fazer
  const caixa = document.getElementById('erroCarga');
  caixa.hidden = false;
  caixa.textContent = `${err.message} Confira se o servidor está rodando e recarregue a página.`;
  console.error(err);
});
