// =========================================================================
// SARgento – página inicial: um mini-mapa de satélite em cada card.
// A geometria (polígono, cristas, ponto) vem no atributo data-geo do card.
// Os mapas são só para ver: sem zoom, sem arrastar (o clique abre o card).
// =========================================================================
document.querySelectorAll('.card-mapa').forEach(div => {
  const geo = JSON.parse(div.dataset.geo);
  const mapa = L.map(div, {
    zoomControl: false, attributionControl: false,   // créditos no rodapé da página
    dragging: false, scrollWheelZoom: false, doubleClickZoom: false,
    boxZoom: false, keyboard: false, touchZoom: false,
  });
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 18 }).addTo(mapa);

  const grupo = L.featureGroup().addTo(mapa);
  if (geo.poligono) {
    L.geoJSON(geo.poligono, { style: { color: '#ffd166', weight: 2, fill: true, fillOpacity: 0.12 } }).addTo(grupo);
  }
  geo.cristas.forEach(linha => L.geoJSON(linha, { style: { color: '#ff3dff', weight: 3 } }).addTo(grupo));

  if (grupo.getLayers().length) mapa.fitBounds(grupo.getBounds(), { padding: [12, 12] });
  else mapa.setView(geo.ponto, 14);
});
