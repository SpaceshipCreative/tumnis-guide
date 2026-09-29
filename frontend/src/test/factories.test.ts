// Generated factories (P0-11): test data built from the zod schemas openapi-ts
// writes to src/api, so a factory follows the API contract with no hand edits.
import { expect, test } from "vitest";

interface Schema {
  parse: (value: unknown) => unknown;
}
interface ZodModule {
  zProblem: Schema;
}
type Factory = (overrides?: Record<string, unknown>) => Record<string, unknown>;
interface FactoriesModule {
  makeProblem: Factory;
}

// Loaded at run time, so this file compiles before the generated modules exist.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

test.fails(
  "[P0-11][FR-14.7] a generated factory's output parses with its zod schema",
  async () => {
    const { zProblem } = await load<ZodModule>("../api/zod.gen");
    const { makeProblem } = await load<FactoriesModule>("./factories");

    const problem = makeProblem({ code: "not_found", status: 404 });

    expect(zProblem.parse(problem)).toEqual(problem);
    expect(problem).toMatchObject({
      code: "not_found",
      status: 404,
      schema_version: 1,
    });
  },
);
