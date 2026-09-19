const handlers = {};

export function on(name, fn) {
  (handlers[name] = handlers[name] || []).push(fn);
}

export function emit(name, data) {
  (handlers[name] || []).slice().forEach((fn) => fn(data));
}
