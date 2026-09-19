// Simple event bus used to pass messages between modules without importing each other directly.

const handlers = {};

// Register a function to run whenever the named event is emitted.
export function on(name, fn) {
  (handlers[name] = handlers[name] || []).push(fn);
}

// Run all functions registered for this event name, passing along the data.
export function emit(name, data) {
  (handlers[name] || []).slice().forEach((fn) => fn(data));
}
