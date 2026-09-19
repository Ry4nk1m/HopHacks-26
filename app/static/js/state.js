// Holds the app's shared state and simple local storage helpers.

// Global app state, shared across all modules by importing this object.
export const S = {
  cfg: null,
  user: null,
  flags: [],
  conditions: null,
  mission: null, // { mission, flag }
  filter: 'all',
  overlay: 'off',
  voice: true,
  pos: null, // real GPS fix { lat, lon, acc, ts }
  fake: null, // demo location { lat, lon }
  locState: 'unknown', // unknown | waiting | ok | denied | unavailable
};

// Wraps localStorage so reads and writes never throw if storage is blocked.
export const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* storage unavailable */ } },
};

// 311 and user reports are sorted by what they are about, so a fallen tree shows under Trees and a damaged inlet under Drains
const TREE_CATS = new Set(['fallen_tree', 'broken_branch', 'tree_issue']);
const DRAIN_CATS = new Set(['damaged_inlet', 'blocked_drain', 'storm_inlet_choke']);
const isReport = (f) => f.type === 'flood_report' || f.type === 'problem_report';
function reportCategory(f) {
  const c = f.context || {};
  return (c.city && c.city.category) || (c.report && c.report.category) || null;
}
const isTreeReport = (f) => f.type === 'problem_report' && TREE_CATS.has(reportCategory(f));
const isDrainReport = (f) => f.type === 'problem_report' && DRAIN_CATS.has(reportCategory(f));

// One test function per filter chip, used to decide which flags to show.
const FILTERS = {
  all: () => true,
  tree: (f) => f.type === 'tree_water' || isTreeReport(f),
  drain: (f) => f.type === 'drain_clear' || isDrainReport(f),
  cooling: (f) => f.type === 'cooling_check',
  reports: (f) => isReport(f) && !isTreeReport(f) && !isDrainReport(f),
};

// Return the flags that match the currently selected filter.
export function visibleFlags() {
  return S.flags.filter(FILTERS[S.filter] || FILTERS.all);
}
