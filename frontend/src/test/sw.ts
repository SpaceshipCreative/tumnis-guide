// A service worker's `clients` for tests (P4-05): `matchAll` answers the given window
// clients, each with `navigate` and `focus` spies; `openWindow` is a spy too.
import { type Mock, vi } from "vitest";

export interface FakeWindowClient {
  url: string;
  focused: boolean;
  navigate: Mock<(url: string) => Promise<FakeWindowClient>>;
  focus: Mock<() => Promise<FakeWindowClient>>;
}

export interface FakeClients {
  windows: FakeWindowClient[];
  matchAll: Mock<(options: unknown) => Promise<FakeWindowClient[]>>;
  openWindow: Mock<(url: string) => Promise<null>>;
}

export function windowClient(url: string, focused = false): FakeWindowClient {
  const client: FakeWindowClient = {
    url,
    focused,
    navigate: vi.fn((to: string) => {
      client.url = new URL(to, client.url).href;
      return Promise.resolve(client);
    }),
    focus: vi.fn(() => {
      client.focused = true;
      return Promise.resolve(client);
    }),
  };
  return client;
}

export function fakeClients(windows: FakeWindowClient[] = []): FakeClients {
  return {
    windows,
    matchAll: vi.fn(() => Promise.resolve(windows)),
    openWindow: vi.fn(() => Promise.resolve(null)),
  };
}
