// Handles GPS location: watching position, computing distances, and the demo "fake location" mode.

import { S } from './state.js';
import { emit } from './bus.js';

const R = 6371008.8; // earth radius in meters

// Distance in meters between two lat/lon points, using the haversine formula.
export function haversine(lat1, lon1, lat2, lon2) {
  const p1 = (lat1 * Math.PI) / 180, p2 = (lat2 * Math.PI) / 180;
  const dp = p2 - p1, dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

// Build a GeoJSON polygon approximating a circle of the given radius around a point.
export function circlePolygon(lat, lon, radiusM, steps = 48) {
  const ring = [];
  const dLat = radiusM / 111320, dLon = radiusM / (111320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI;
    ring.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } };
}

/** Best current position: a demo location if set, otherwise the latest GPS fix. */
export function getPos() {
  if (S.fake) return { lat: S.fake.lat, lon: S.fake.lon, acc: 8, ts: Date.now(), fake: true };
  return S.pos;
}

// Turn on the demo fake location and tell listeners the position changed.
export function setFake(lat, lon) {
  S.fake = { lat, lon };
  emit('pos');
}

// Turn off the demo fake location, going back to real GPS.
export function clearFake() {
  S.fake = null;
  emit('pos');
}

let watchId = null;

// Handle a new GPS reading: smooth out small jitter, then save it and notify listeners.
function onFix(p) {
  const { latitude: lat, longitude: lon, accuracy: acc } = p.coords;
  let fix = { lat, lon, acc, ts: p.timestamp || Date.now() };
  // damp jitter when we already have a decent fix and the new one is close
  if (S.pos && acc <= 60 && S.pos.acc <= 60 && haversine(S.pos.lat, S.pos.lon, lat, lon) < 25) {
    fix = { lat: S.pos.lat + 0.4 * (lat - S.pos.lat), lon: S.pos.lon + 0.4 * (lon - S.pos.lon), acc, ts: fix.ts };
  }
  S.pos = fix;
  S.locState = 'ok';
  emit('pos');
}

// Handle a GPS error: record whether permission was denied or location is unavailable.
function onError(err) {
  if (err && err.code === 1) S.locState = 'denied';
  else if (!S.pos) S.locState = 'unavailable';
  emit('pos');
}

// Start watching the device's GPS position, if not already watching.
export function startLocation() {
  if (!navigator.geolocation) { S.locState = 'unavailable'; emit('pos'); return; }
  if (watchId != null) return;
  S.locState = 'waiting';
  emit('pos');
  watchId = navigator.geolocation.watchPosition(onFix, onError, { enableHighAccuracy: true, maximumAge: 3000, timeout: 20000 });
}

/** A position fresh enough to act on (used when arriving, submitting or confirming). */
export function freshPos() {
  if (S.fake) return Promise.resolve(getPos());
  if (S.pos && Date.now() - S.pos.ts < 20000) return Promise.resolve(S.pos);
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) { reject(new Error('unavailable')); return; }
    navigator.geolocation.getCurrentPosition((p) => { onFix(p); resolve(S.pos); }, (e) => reject(e), { enableHighAccuracy: true, timeout: 10000, maximumAge: 5000 });
  });
}
