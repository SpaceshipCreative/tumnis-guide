// Test data factories. `factoryFor(schema)` builds a factory from a zod schema
// generated into src/api by openapi-ts (P0-11), so test data follows the API
// contract: every required field gets a valid sample, defaults apply, and the
// result is parsed by the schema itself. Add `makeX = factoryFor(zX)` for each
// response model a test needs.
//
// makeProject is generated from ProjectOut (P0-17); makeTask stays hand-written
// until the TaskOut schema exists (P0-18), then becomes `factoryFor(zTaskOut)`.
import * as z from "zod";

import { zProblem, zProjectOut } from "../api/zod.gen";

// The backend's FixedClock start (A5): factories are deterministic but for ids.
const SAMPLE_DATETIME = "2026-03-09T12:00:00Z";
const SAMPLE_DATE = "2026-03-09";

function sampleString(format: string | undefined): string {
  switch (format) {
    case "uuid":
    case "guid":
      return crypto.randomUUID();
    case "datetime":
      return SAMPLE_DATETIME;
    case "date":
      return SAMPLE_DATE;
    case "email":
      return "person@example.com";
    case "url":
      return "https://example.com/";
    default:
      return "text";
  }
}

// A valid value for `schema`, or undefined for an optional field (left out).
function sample(schema: z.core.$ZodType): unknown {
  const def = (schema as z.core.$ZodTypes)._zod.def;
  switch (def.type) {
    case "object":
      return Object.fromEntries(
        Object.entries(def.shape).flatMap(([key, field]) => {
          const value = sample(field);
          return value === undefined ? [] : [[key, value]];
        }),
      );
    case "string":
      return sampleString("format" in def ? String(def.format) : undefined);
    case "number":
      return 1;
    case "boolean":
      return false;
    case "literal":
      return def.values[0];
    case "enum":
      return Object.values(def.entries)[0];
    case "array":
      return [];
    case "record":
      return {};
    case "optional":
      return undefined;
    case "nullable":
      return null;
    case "default":
      return def.defaultValue;
    case "union":
      return def.options[0] === undefined ? undefined : sample(def.options[0]);
    case "pipe":
      return sample(def.in);
    case "unknown":
    case "any":
      return null;
    default:
      throw new Error(`factoryFor: no sample for a zod ${def.type} schema`);
  }
}

export type Factory<S extends z.ZodObject> = (
  overrides?: Partial<z.input<S>>,
) => z.output<S>;

export function factoryFor<S extends z.ZodObject>(schema: S): Factory<S> {
  return (overrides = {}) =>
    schema.parse({ ...(sample(schema) as object), ...overrides });
}

export const makeProblem = factoryFor(zProblem);
export const makeProject = factoryFor(zProjectOut);

export type TaskLabel = "human" | "ai" | "hybrid";
export type TaskStatus =
  | "backlog"
  | "today"
  | "in_progress"
  | "waiting_on_human"
  | "in_review"
  | "done";

export interface TaskStub {
  schema_version: 1;
  id: string;
  project_id: string;
  parent_id: string | null;
  title: string;
  label: TaskLabel | null;
  status: TaskStatus;
  version: number;
  estimate_minutes: number | null;
  first_action: string | null;
  layout: "card" | "checklist" | "nested_ai" | null;
  tainted: boolean;
}

export function makeTask(overrides: Partial<TaskStub> = {}): TaskStub {
  return {
    schema_version: 1,
    id: crypto.randomUUID(),
    project_id: crypto.randomUUID(),
    parent_id: null,
    title: "Test task",
    label: "human",
    status: "backlog",
    version: 1,
    estimate_minutes: 30,
    first_action: null,
    layout: null,
    tainted: false,
    ...overrides,
  };
}
