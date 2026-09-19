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

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* storage unavailable */ } },
};

const FILTERS = {
  all: () => true,
  tree: (f) => f.type === 'tree_water',
  drain: (f) => f.type === 'drain_clear',
  cooling: (f) => f.type === 'cooling_check',
  reports: (f) => f.type === 'flood_report' || f.type === 'problem_report',
};

export function visibleFlags() {
  return S.flags.filter(FILTERS[S.filter] || FILTERS.all);
}
