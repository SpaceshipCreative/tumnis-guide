// A keyboard sensor that handles every key after the pick-up, one rendered move at a time
// (P0-24, UX 7).
//
// dnd-kit's KeyboardSensor has two races with a fast typist (or a test):
// - it starts listening for keys in a timeout after the pick-up, so a key that comes
//   first is lost;
// - it reads the drop target (`over`) from state React updates a few milliseconds after
//   an arrow key moves the card, so a Space right after an arrow drops the card where it
//   was before that move.
// This sensor listens from the moment it is created (ignoring the key that picked the
// card up), and holds each key that arrives while a move is still rendering, replaying
// it in order once what the move changes has held still for a frame.
import { KeyboardSensor, type KeyboardSensorProps } from "@dnd-kit/core";

/** The longest a held key waits for a move to settle, in frames (about half a second). */
const MAX_SETTLE_FRAMES = 30;

type KeyHandler = (event: KeyboardEvent) => void;

interface SensorInternals {
  handleKeyDown: KeyHandler;
  detach: () => void;
}

export class SettledKeyboardSensor extends KeyboardSensor {
  constructor(props: KeyboardSensorProps) {
    super(props);
    // The parent's members are private in its types; they are plain fields at runtime.
    const self = this as unknown as SensorInternals;
    const handle = self.handleKeyDown;
    const detach = self.detach.bind(this);
    const target = props.event.target;
    const doc =
      target instanceof Node ? (target.ownerDocument ?? document) : document;
    const queue: KeyboardEvent[] = [];
    let settling = false;
    let detached = false;

    // What a move changes: the card's rect, the target under it and the board's scroll
    // (on a phone an arrow scrolls the board instead of moving the card).
    const snapshot = () => {
      const { over, collisionRect, scrollableAncestors } =
        props.context.current;
      return JSON.stringify([
        over?.id ?? null,
        collisionRect?.left ?? null,
        collisionRect?.top ?? null,
        scrollableAncestors.map((el) => [el.scrollLeft, el.scrollTop]),
      ]);
    };

    const settle = () => {
      settling = true;
      let frames = 0;
      let last = snapshot();
      const tick = () => {
        frames += 1;
        const now = snapshot();
        const stable = now === last;
        last = now;
        if ((frames >= 3 && stable) || frames >= MAX_SETTLE_FRAMES) {
          settling = false;
          drain();
        } else {
          requestAnimationFrame(tick);
        }
      };
      requestAnimationFrame(tick);
    };

    const run = (event: KeyboardEvent) => {
      handle(event);
      // A key that neither dropped nor cancelled may have moved the card: wait for that
      // move to render before the next key.
      if (!detached) settle();
    };

    const drain = () => {
      while (!settling && !detached && queue.length > 0) {
        const next = queue.shift();
        if (next) run(next);
      }
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event === props.event || detached) return; // the pick-up key
      if (settling || queue.length > 0) {
        event.preventDefault();
        queue.push(event);
        return;
      }
      run(event);
    };

    doc.addEventListener("keydown", onKeyDown);
    // The parent adds `handleKeyDown` as a listener in its timeout; this one replaces it.
    self.handleKeyDown = () => undefined;
    self.detach = () => {
      detached = true;
      queue.length = 0;
      doc.removeEventListener("keydown", onKeyDown);
      detach();
    };
  }
}
