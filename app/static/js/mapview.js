// Sets up and drives the MapLibre map: base style, flag/user/mission layers, overlays, and pan/zoom actions.

import { S, visibleFlags } from './state.js';
import { emit } from './bus.js';
import { circlePolygon, getPos } from './geo.js';
import { TYPE_COLOR, TYPE_GLYPH } from './ui.js';

// Backup raster map style, used if the main vector style fails to load.
const RASTER_FALLBACK = {
  version: 8,
  sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, maxzoom: 19, attribution: '© OpenStreetMap contributors' } },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};
const FONT = ['Noto Sans Bold'];
const EMPTY = { type: 'FeatureCollection', features: [] };

let map = null;
let ready = false;
let baseErrors = 0;
let usedFallback = false;
let pulseTimer = null;

export const isReady = () => ready;

// Draw a flag icon (colored circle with a glyph) onto a canvas, for use as a map marker image.
function drawIcon(type, dim) {
  const s = 64, c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  g.save();
  g.shadowColor = 'rgba(15,23,42,0.38)'; g.shadowBlur = 7; g.shadowOffsetY = 2.5;
  g.beginPath(); g.arc(32, 30, 25, 0, Math.PI * 2);
  g.fillStyle = dim ? '#94a3b8' : TYPE_COLOR[type]; g.fill();
  g.restore();
  g.beginPath(); g.arc(32, 30, 25, 0, Math.PI * 2);
  g.lineWidth = 3.5; g.strokeStyle = '#ffffff'; g.stroke();
  g.translate(0, -2);
  const glyph = dim ? '#ffffff' : TYPE_GLYPH[type];
  g.strokeStyle = glyph; g.fillStyle = glyph; g.lineWidth = 4; g.lineCap = 'round'; g.lineJoin = 'round';
  if (type === 'tree_water') {
    g.beginPath(); g.arc(32, 27, 10, 0, Math.PI * 2); g.fill();
    g.beginPath(); g.moveTo(32, 36); g.lineTo(32, 47); g.stroke();
  } else if (type === 'drain_clear') {
    g.beginPath(); g.moveTo(32, 15); g.bezierCurveTo(32, 15, 44, 27, 44, 36); g.arc(32, 36, 12, 0, Math.PI, false); g.bezierCurveTo(20, 27, 32, 15, 32, 15); g.closePath(); g.fill();
  } else if (type === 'cooling_check') {
    [0, 60, 120].forEach((deg) => {
      const a = (deg * Math.PI) / 180;
      g.beginPath(); g.moveTo(32 - Math.cos(a) * 12, 32 - Math.sin(a) * 12); g.lineTo(32 + Math.cos(a) * 12, 32 + Math.sin(a) * 12); g.stroke();
    });
  } else if (type === 'flood_report') {
    [26, 38].forEach((y) => {
      g.beginPath(); g.moveTo(17, y);
      g.bezierCurveTo(22, y - 6, 27, y - 6, 32, y); g.bezierCurveTo(37, y + 6, 42, y + 6, 47, y); g.stroke();
    });
  } else {
    g.beginPath(); g.moveTo(32, 17); g.lineTo(32, 35); g.stroke();
    g.beginPath(); g.arc(32, 44, 2.6, 0, Math.PI * 2); g.fill();
  }
  return g.getImageData(0, 0, s, s);
}

// Register a normal and a dimmed marker image for each flag type.
function addIcons() {
  Object.keys(TYPE_COLOR).forEach((type) => {
    [false, true].forEach((dim) => {
      const name = dim ? `${type}-dim` : type;
      if (!map.hasImage(name)) map.addImage(name, drawIcon(type, dim), { pixelRatio: 2 });
    });
  });
}

// Add the satellite overlay image sources/layers (vegetation, heat) and the NASA daily image layer.
function addOverlays() {
  const L = S.cfg.layers;
  if (L.bounds) {
    ['ndvi', 'lst'].forEach((name) => {
      if (!map.getSource(`ov-${name}`)) map.addSource(`ov-${name}`, { type: 'image', url: `/api/layers/${name}.png`, coordinates: L.bounds });
      if (!map.getLayer(`ov-${name}`)) map.addLayer({ id: `ov-${name}`, type: 'raster', source: `ov-${name}`, layout: { visibility: 'none' }, paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 0 } });
    });
  }
}

// Add the map layers that draw flags: clusters, cluster counts, a pulse for urgent flags, the selection ring, and the flag icons.
function addFlagLayers() {
  if (!map.getSource('flags')) map.addSource('flags', { type: 'geojson', data: EMPTY, cluster: true, clusterRadius: 44, clusterMaxZoom: 16 });
  const hasGlyphs = !!map.getStyle().glyphs;
  map.addLayer({ id: 'clusters', type: 'circle', source: 'flags', filter: ['has', 'point_count'],
    paint: { 'circle-color': '#152e34', 'circle-radius': ['step', ['get', 'point_count'], 16, 10, 20, 30, 26], 'circle-stroke-width': 3, 'circle-stroke-color': '#ffffff' } });
  if (hasGlyphs) {
    map.addLayer({ id: 'cluster-count', type: 'symbol', source: 'flags', filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': FONT, 'text-size': 13, 'text-allow-overlap': true }, paint: { 'text-color': '#ffffff' } });
  }
  map.addLayer({ id: 'flag-pulse', type: 'circle', source: 'flags', filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'urgency'], 3], ['==', ['get', 'dim'], 0]],
    paint: { 'circle-radius': 22, 'circle-color': '#e46a46', 'circle-opacity': 0.25 } });
  map.addLayer({ id: 'flag-selected', type: 'circle', source: 'flags', filter: ['==', ['get', 'id'], -1],
    paint: { 'circle-radius': 26, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-width': 4, 'circle-stroke-color': '#eba745' } });
  map.addLayer({ id: 'flag-points', type: 'symbol', source: 'flags', filter: ['!', ['has', 'point_count']],
    layout: { 'icon-image': ['get', 'icon'], 'icon-size': ['match', ['get', 'urgency'], 3, 1.15, 2, 1.0, 0.85], 'icon-allow-overlap': true, 'icon-ignore-placement': true } });
}

// Add the map layers for the user's own position and the line/ring to the active mission's flag.
function addUserAndMission() {
  ['me', 'me-acc', 'mission'].forEach((id) => { if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: EMPTY }); });
  map.addLayer({ id: 'mission-ring', type: 'circle', source: 'mission', filter: ['==', ['geometry-type'], 'Point'],
    paint: { 'circle-radius': 20, 'circle-color': 'rgba(31,134,160,0.14)', 'circle-stroke-width': 2, 'circle-stroke-color': '#1f86a0' } });
  map.addLayer({ id: 'mission-line', type: 'line', source: 'mission', filter: ['==', ['geometry-type'], 'LineString'],
    paint: { 'line-color': '#1f86a0', 'line-width': 3, 'line-dasharray': [1.5, 1.5] } });
  map.addLayer({ id: 'me-acc', type: 'fill', source: 'me-acc', paint: { 'fill-color': '#1f86a0', 'fill-opacity': 0.14 } });
  map.addLayer({ id: 'me-halo', type: 'circle', source: 'me', paint: { 'circle-radius': 15, 'circle-color': ['case', ['get', 'fake'], '#eba745', '#1f86a0'], 'circle-opacity': 0.25 } });
  map.addLayer({ id: 'me-dot', type: 'circle', source: 'me', paint: { 'circle-radius': 7, 'circle-color': ['case', ['get', 'fake'], '#eba745', '#1f86a0'], 'circle-stroke-width': 3, 'circle-stroke-color': '#ffffff' } });
}

/**
 * Make the OpenFreeMap "liberty" basemap flat (2D) and crisper: sharper building footprints instead of extrusions,
 * calmer footpaths, and fewer tiny POI icons competing with our flags. Every step is guarded so other styles still work.
 */
function tuneBasemap() {
  const has = (id) => !!map.getLayer(id);
  const safe = (fn) => { try { fn(); } catch (e) { /* a style without this layer */ } };
  if (has('building-3d')) map.setLayoutProperty('building-3d', 'visibility', 'none');
  if (has('building')) {
    safe(() => map.setPaintProperty('building', 'fill-color', 'hsl(36, 20%, 83%)'));
    safe(() => map.setPaintProperty('building', 'fill-outline-color', ['interpolate', ['linear'], ['zoom'], 13, 'hsla(34, 18%, 68%, 0.35)', 15, 'hsl(34, 18%, 66%)']));
    safe(() => map.setPaintProperty('building', 'fill-opacity', 1));
    safe(() => map.setLayerZoomRange('building', 12, 24));
  }
  ['poi_r1', 'poi_transit'].forEach((id) => { if (has(id)) map.setLayoutProperty(id, 'visibility', 'none'); });
  // keep place names (useful when walking) but drop their little icons so they don't compete with our flags
  ['poi_r7', 'poi_r20'].forEach((id) => { if (has(id)) safe(() => map.setPaintProperty(id, 'icon-opacity', 0)); });
  // footpaths: a quiet warm dashed line that reads as a path, instead of noisy white dots
  ['road_path_pedestrian', 'bridge_path_pedestrian'].forEach((id) => {
    if (!has(id)) return;
    safe(() => map.setPaintProperty(id, 'line-color', '#d3c7aa'));
    safe(() => map.setPaintProperty(id, 'line-width', ['interpolate', ['linear'], ['zoom'], 14, 0.6, 16, 1.1, 18, 2, 20, 3.4]));
    safe(() => map.setPaintProperty(id, 'line-dasharray', [3, 1.8]));
    safe(() => map.setPaintProperty(id, 'line-opacity', ['interpolate', ['linear'], ['zoom'], 14, 0, 15.2, 0.9]));
    safe(() => map.setLayoutProperty(id, 'line-cap', 'butt'));
  });
  // warm palette: cream land, sage parks, teal-tinted water, peach and amber main roads
  const paint = (id, prop, value) => { if (has(id)) safe(() => map.setPaintProperty(id, prop, value)); };
  paint('background', 'background-color', '#f6f0e2');
  paint('landuse_residential', 'fill-color', '#f3ecdc');
  paint('park', 'fill-color', '#dde6c9');
  paint('landcover_grass', 'fill-color', '#dde6c9');
  paint('landcover_wood', 'fill-color', '#d3dfbd');
  paint('water', 'fill-color', '#bcd7d8');
  paint('waterway_river', 'line-color', '#bcd7d8');
  paint('road_minor', 'line-color', '#fffdf6');
  paint('road_minor_casing', 'line-color', '#e6dbc4');
  paint('road_secondary_tertiary', 'line-color', '#fbe2ad');
  paint('road_secondary_tertiary_casing', 'line-color', '#e0c082');
  paint('road_trunk_primary', 'line-color', '#f7cc86');
  paint('road_trunk_primary_casing', 'line-color', '#d9a45a');
  paint('road_motorway', 'line-color', '#f0b862');
  paint('road_motorway_casing', 'line-color', '#c98f47');
}

// Run once the map style has loaded: set up all layers and push in the current data.
function setupLayers() {
  ready = false;
  tuneBasemap();
  addIcons();
  addOverlays();
  addFlagLayers();
  addUserAndMission();
  ready = true;
  setOverlay(S.overlay);
  pushFlags();
  pushUser();
  pushMission();
  startPulse();
  emit('map:ready');
}

// Animate the pulsing circle drawn under high-urgency flags.
function startPulse() {
  if (pulseTimer) clearInterval(pulseTimer);
  let phase = 0;
  pulseTimer = setInterval(() => {
    if (!ready || document.hidden || !map.getLayer('flag-pulse')) return;
    phase = (phase + 0.12) % 1;
    map.setPaintProperty('flag-pulse', 'circle-radius', 20 + phase * 16);
    map.setPaintProperty('flag-pulse', 'circle-opacity', 0.32 * (1 - phase));
  }, 90);
}

// Check whether a map error is about the base map style/tiles failing to load.
function isBaseError(e) {
  const id = e && e.sourceId;
  if (id) return id === 'openmaptiles' || id === 'ne2_shaded' || id === 'osm';
  return !!(e && e.error && /style|fetch|network|failed/i.test(String(e.error.message || '')));
}

// Create the map, wire up click/hover handlers, the fallback-on-error logic, and long-press to teleport.
export function initMap(cfg) {
  map = new maplibregl.Map({
    container: 'map', style: cfg.map_style_url, center: [cfg.center[1], cfg.center[0]], zoom: 13, minZoom: 10.5, maxZoom: 19.5,
    maxBounds: [[cfg.aoi[0] - 0.05, cfg.aoi[1] - 0.05], [cfg.aoi[2] + 0.05, cfg.aoi[3] + 0.05]],
    attributionControl: false, dragRotate: false, pitchWithRotate: false, fadeDuration: 100,
  });
  if (cfg.dev_tools) window.__map = map; // handy for demos and debugging
  map.touchZoomRotate.disableRotation();
  map.on('style.load', setupLayers);
  map.on('error', (e) => {
    if (usedFallback || !isBaseError(e)) return;
    baseErrors += 1;
    if (baseErrors >= 4) {
      usedFallback = true;
      emit('map:fallback');
      map.setStyle(RASTER_FALLBACK);
    }
  });
  map.on('click', 'flag-points', (e) => { if (e.features && e.features[0]) emit('flag:select', Number(e.features[0].properties.id)); });
  map.on('click', 'clusters', async (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: ['clusters'] })[0];
    if (!f) return;
    const z = await map.getSource('flags').getClusterExpansionZoom(f.properties.cluster_id);
    map.easeTo({ center: f.geometry.coordinates, zoom: Math.min(z + 0.5, 19) });
  });
  ['flag-points', 'clusters'].forEach((layer) => {
    map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
  });
  // long-press to teleport (demo tools only)
  let lp = null;
  const cancel = () => { if (lp) { clearTimeout(lp); lp = null; } };
  const arm = (e) => {
    if (!S.cfg.dev_tools || (e.originalEvent && e.originalEvent.touches && e.originalEvent.touches.length > 1)) return;
    cancel();
    lp = setTimeout(() => { lp = null; emit('map:longpress', { lat: e.lngLat.lat, lon: e.lngLat.lng }); }, 700);
  };
  map.on('mousedown', arm); map.on('touchstart', arm);
  ['mouseup', 'touchend', 'touchcancel', 'dragstart', 'zoomstart', 'touchmove'].forEach((ev) => map.on(ev, cancel));
  return map;
}

// Send the current visible flags to the map as GeoJSON points.
export function pushFlags() {
  if (!ready) return;
  const list = visibleFlags();
  map.getSource('flags').setData({
    type: 'FeatureCollection',
    features: list.map((f) => {
      const dim = f.claimed || f.status === 'pending';
      return { type: 'Feature', geometry: { type: 'Point', coordinates: [f.lon, f.lat] },
        properties: { id: f.id, urgency: f.urgency, icon: dim ? `${f.type}-dim` : f.type, dim: dim ? 1 : 0 } };
    }),
  });
}

// Update the map with the user's current position and accuracy circle.
export function pushUser() {
  if (!ready) return;
  const p = getPos();
  if (!p) { map.getSource('me').setData(EMPTY); map.getSource('me-acc').setData(EMPTY); return; }
  map.getSource('me').setData({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] }, properties: { fake: !!p.fake } });
  map.getSource('me-acc').setData(p.fake ? EMPTY : circlePolygon(p.lat, p.lon, Math.max(p.acc || 0, 3)));
}

// Update the map with the active mission's flag and a line from the user to it.
export function pushMission() {
  if (!ready) return;
  const src = map.getSource('mission');
  if (!S.mission) { src.setData(EMPTY); return; }
  const f = S.mission.flag, p = getPos();
  const feats = [{ type: 'Feature', geometry: { type: 'Point', coordinates: [f.lon, f.lat] }, properties: {} }];
  if (p) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: [[p.lon, p.lat], [f.lon, f.lat]] }, properties: {} });
  src.setData({ type: 'FeatureCollection', features: feats });
}

// Switch which satellite overlay layer is visible.
export function setOverlay(name) {
  S.overlay = name;
  if (!ready) return;
  ['ndvi', 'lst'].forEach((n) => {
    if (map.getLayer(`ov-${n}`)) map.setLayoutProperty(`ov-${n}`, 'visibility', n === name ? 'visible' : 'none');
  });
}

// Highlight the flag with the given id (or clear the highlight if id is null).
export function setSelected(id) {
  if (ready && map.getLayer('flag-selected')) map.setFilter('flag-selected', ['==', ['get', 'id'], id == null ? -1 : id]);
}

// Animate the map to center on a point at a given zoom level.
export function flyTo(lat, lon, zoom) {
  if (map) map.flyTo({ center: [lon, lat], zoom: zoom || Math.max(map.getZoom(), 17), speed: 1.4, essential: true });
}

// Instantly move the map to a point, with no animation.
export function jumpTo(lat, lon, zoom) {
  if (map) map.jumpTo({ center: [lon, lat], zoom: zoom || map.getZoom() });
}

// Fly the map to the user's current position, if known.
export function flyToUser() {
  const p = getPos();
  if (p) flyTo(p.lat, p.lon, 17);
}

export function resize() { if (map) map.resize(); }
export const getMap = () => map;
