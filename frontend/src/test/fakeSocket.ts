// A scripted WebSocket for the live-socket tests: `vi.stubGlobal("WebSocket",
// FakeSocket)`, then drive each instance with open(), receive() and drop().
export class FakeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeSocket[] = [];

  readyState = FakeSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closedByClient = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  static reset(): void {
    FakeSocket.instances = [];
  }

  static latest(): FakeSocket {
    const socket = FakeSocket.instances.at(-1);
    if (!socket) throw new Error("no socket was opened");
    return socket;
  }

  /** The server accepted the connection. */
  open(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** The server sent a frame (objects are sent as JSON). */
  receive(data: unknown): void {
    const text = typeof data === "string" ? data : JSON.stringify(data);
    this.onmessage?.(new MessageEvent("message", { data: text }));
  }

  /** The connection dropped (network, proxy or server restart). */
  drop(): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1006 }));
  }

  send(): void {
    // The live socket is one-way; nothing is sent.
  }

  close(): void {
    this.closedByClient = true;
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }
}
