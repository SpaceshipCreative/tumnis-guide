// R-38 over every machine in src/machines (P0-25): each guard, action, actor and delay a
// machine's config names is declared in its `setup(...)`, none is written inline, and
// setup declares nothing the config never names. A new machine file is swept
// automatically.
import { expect, test } from "vitest";
import { type AnyStateMachine, StateMachine } from "xstate";

type Kind = "guards" | "actions" | "actors" | "delays";

interface Refs {
  named: Record<Kind, Set<string>>;
  inline: string[];
}

const modules = import.meta.glob<Record<string, unknown>>(
  ["./*.ts", "!./*.test.ts"],
  { eager: true },
);

function machines(): AnyStateMachine[] {
  return Object.values(modules).flatMap((exports) =>
    Object.values(exports).filter(
      (value): value is AnyStateMachine => value instanceof StateMachine,
    ),
  );
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asList = (value: unknown): unknown[] =>
  value === undefined ? [] : Array.isArray(value) ? value : [value];

function reference(refs: Refs, kind: Kind, value: unknown, at: string): void {
  if (typeof value === "string") {
    refs.named[kind].add(value);
  } else if (typeof value === "function") {
    refs.inline.push(`${kind} at ${at}`);
  } else if (isObject(value)) {
    if (typeof value.type === "string") refs.named[kind].add(value.type);
    // Higher-order guards (and, or, not) name their parts.
    for (const part of asList(value.guards)) reference(refs, kind, part, at);
  }
}

function transitions(refs: Refs, value: unknown, at: string): void {
  for (const transition of asList(value)) {
    if (!isObject(transition)) continue;
    if (transition.guard !== undefined) {
      reference(refs, "guards", transition.guard, at);
    }
    for (const action of asList(transition.actions)) {
      reference(refs, "actions", action, at);
    }
  }
}

function walk(refs: Refs, node: Json, at: string): void {
  for (const key of ["entry", "exit"]) {
    for (const action of asList(node[key])) {
      reference(refs, "actions", action, `${at}.${key}`);
    }
  }
  if (isObject(node.on)) {
    for (const [event, value] of Object.entries(node.on)) {
      transitions(refs, value, `${at}.on.${event}`);
    }
  }
  transitions(refs, node.always, `${at}.always`);
  if (isObject(node.after)) {
    for (const [delay, value] of Object.entries(node.after)) {
      if (!/^\d+$/.test(delay)) refs.named.delays.add(delay);
      transitions(refs, value, `${at}.after.${delay}`);
    }
  }
  for (const invoke of asList(node.invoke)) {
    if (!isObject(invoke)) continue;
    reference(refs, "actors", invoke.src, `${at}.invoke`);
    transitions(refs, invoke.onDone, `${at}.invoke.onDone`);
    transitions(refs, invoke.onError, `${at}.invoke.onError`);
  }
  if (isObject(node.states)) {
    for (const [key, child] of Object.entries(node.states)) {
      if (isObject(child)) walk(refs, child, `${at}.${key}`);
    }
  }
}

function declared(machine: AnyStateMachine, kind: Kind): string[] {
  const implementations = machine.implementations as Record<Kind, object>;
  return Object.keys(implementations[kind]).sort();
}

test.fails(
  "[P0-25][FR-3.10] T-P0-25-14 every machine declares the guards, actions, actors and delays it names",
  () => {
    const found = machines();
    expect(found.map((m) => m.id)).toContain("offlineQueue");
    for (const machine of found) {
      const refs: Refs = {
        named: {
          guards: new Set(),
          actions: new Set(),
          actors: new Set(),
          delays: new Set(),
        },
        inline: [],
      };
      walk(refs, machine.config as Json, machine.id);
      expect(refs.inline, `${machine.id}: inline implementations`).toEqual([]);
      for (const kind of ["guards", "actions", "actors", "delays"] as const) {
        expect(
          [...refs.named[kind]].sort(),
          `${machine.id}: ${kind} named in the config and declared in setup`,
        ).toEqual(declared(machine, kind));
      }
    }
  },
);
