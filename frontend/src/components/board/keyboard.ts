// A keyboard sensor that handles every key after the pick-up, one rendered move at a time
// (P0-24, UX 7).
//
// dnd-kit's KeyboardSensor has two races with a fast typist (or a test):
// - it starts listening for keys in a timeout after the pick-up, so a key that comes
//   first is lost;
// - it reads the drop target (`over`) from state React updates some time after an arrow
//   key moves the card, so a Space right after an arrow drops the card where it was
//   before that move.
// This sensor listens from the moment it is created (ignoring the key that picked the
// card up), and holds each key that arrives while the pick-up or a move is still
// rendering, replaying it in order once that has settled: `over` names what the collision
// detection finds under the card now, and nothing changed for a frame.
//
// "Nothing changed" alone is not enough. On a phone an arrow key only scrolls the board
// (the card stays put), and `over` follows through a scroll event, a render, an effect
// and another render; on a busy machine several frames can pass before any of them runs,
// and a Space then dropped the card where it was (the T-P0-24-05 flake, "dropped in
// Backlog"). The droppables' rects follow the scroll at once, so the sensor runs the
// collision detection itself and waits for `over` to agree.
import {
  closestCorners,
  getFirstCollision,
  KeyboardSensor,
  type CollisionDetection,
  type KeyboardSensorOptions,
  type KeyboardSensorProps,
} from "@dnd-kit/core";

/** The longest a held key waits for a move to settle, in frames (about half a second). */
const MAX_SETTLE_FRAMES = 30;

export interface SettledKeyboardSensorOptions extends KeyboardSensorOptions {
  /** The DndContext's collision detection; `closestCorners` when not given. */
  collisionDetection?: CollisionDetection;
}

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
    const options = props.options as SettledKeyboardSensorOptions;
    const detect = options.collisionDetection ?? closestCorners;
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

    // Whether `over` is what the collision detection finds under the card now. The
    // droppables' rects follow their scroll containers as they scroll; `over` follows a
    // few renders later.
    const caughtUp = () => {
      const {
        active,
        collisionRect,
        droppableRects,
        droppableContainers,
        over,
      } = props.context.current;
      if (!active || !collisionRect) return false; // the pick-up has not rendered
      const collisions = detect({
        active,
        collisionRect,
        droppableRects,
        droppableContainers: droppableContainers.getEnabled(),
        pointerCoordinates: null,
      });
      return (
        (getFirstCollision(collisions, "id") ?? null) === (over?.id ?? null)
      );
    };

    const settle = () => {
      settling = true;
      let frames = 0;
      let last = snapshot();
      const tick = () => {
        if (detached) return;
        frames += 1;
        const now = snapshot();
        const stable = now === last;
        last = now;
        if (
          (frames >= 3 && stable && caughtUp()) ||
          frames >= MAX_SETTLE_FRAMES
        ) {
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
    // Keys wait for the pick-up to render too: until it has, there is no card rect for
    // an arrow to move.
    settle();
  }
}
