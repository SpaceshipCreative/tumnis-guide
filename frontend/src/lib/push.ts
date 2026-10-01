// Browser push (P4-05, FR-8.3): the subscribe flow.

export type PushStatus = "unsupported" | "install" | "denied" | "ready" | "on";

export function pushStatus(): Promise<PushStatus> {
  return Promise.reject(new Error("not built yet"));
}

export function enablePush(): Promise<PushStatus> {
  return Promise.reject(new Error("not built yet"));
}
