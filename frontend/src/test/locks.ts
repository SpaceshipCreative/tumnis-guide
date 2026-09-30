// `navigator.locks` for jsdom (P0-25): `request(name, callback)` runs callbacks for one
// name one after another on a promise chain, like the Web Locks API's exclusive mode.
// Two actors in one test then behave like two tabs sharing the browser's lock manager.
type Callback = (lock: { name: string; mode: "exclusive" }) => unknown;

/** Installs the polyfill; answers a function that removes it. */
export function locksPolyfill(): () => void {
  const tails = new Map<string, Promise<unknown>>();
  const locks = {
    request(name: string, callback: Callback): Promise<unknown> {
      const before = tails.get(name) ?? Promise.resolve();
      const run = before.then(() => callback({ name, mode: "exclusive" }));
      tails.set(
        name,
        run.catch(() => undefined),
      );
      return run;
    },
  };
  Object.defineProperty(navigator, "locks", {
    value: locks,
    configurable: true,
  });
  return () => {
    Reflect.deleteProperty(navigator, "locks");
  };
}
