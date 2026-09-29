// Fractional ranking keys (P0-17, FR-2.1): the same algorithm as backend
// tumnis/core/rank.py (a port of the CC0 rocicorp/fractional-indexing), so an optimistic
// board move (P0-24) computes the key the server would. Both ports pass
// backend/fixtures/rank/vectors.json. Keys compare as plain strings.

export const DIGITS =
  "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const ZERO = "0";
const TOP = "z";
export const SMALLEST_INTEGER = "A" + ZERO.repeat(26);
/** Writers rebalance rather than store a key this long (plan default). */
export const MAX_KEY_LEN = 48;

/** An invalid key, or a >= b (the server answers 422 `invalid_rank`). */
export class RankError extends Error {
  readonly code = "invalid_rank";

  constructor(message: string) {
    super(message);
    this.name = "RankError";
  }
}

function digitAt(key: string, i: number): number {
  return DIGITS.indexOf(key.charAt(i));
}

function intLength(head: string): number {
  if (head >= "a" && head <= "z") {
    return head.charCodeAt(0) - "a".charCodeAt(0) + 2;
  }
  if (head >= "A" && head <= "Z") {
    return "Z".charCodeAt(0) - head.charCodeAt(0) + 2;
  }
  throw new RankError(`invalid rank key head: ${head}`);
}

function intPart(key: string): string {
  if (key === "") {
    throw new RankError("empty rank key");
  }
  const length = intLength(key.charAt(0));
  if (length > key.length) {
    throw new RankError(`invalid rank key: ${key}`);
  }
  return key.slice(0, length);
}

/** Throws RankError unless `key` is a well-formed key. */
export function validate(key: string): void {
  if (key === SMALLEST_INTEGER) {
    throw new RankError(`invalid rank key: ${key}`);
  }
  const integer = intPart(key);
  for (let i = 1; i < key.length; i += 1) {
    if (digitAt(key, i) < 0) {
      throw new RankError(`invalid rank key: ${key}`);
    }
  }
  if (key.length > integer.length && key.endsWith(ZERO)) {
    throw new RankError(`invalid rank key (trailing zero): ${key}`);
  }
}

// A fraction strictly between fractions a and b (b null: no upper bound).
function midpoint(a: string, b: string | null): string {
  if (b !== null) {
    let n = 0;
    while (
      n < b.length &&
      (n < a.length ? a.charAt(n) : ZERO) === b.charAt(n)
    ) {
      n += 1;
    }
    if (n > 0) {
      return b.slice(0, n) + midpoint(a.slice(n), b.slice(n));
    }
  }
  const digitA = a ? digitAt(a, 0) : 0;
  const digitB = b !== null ? digitAt(b, 0) : DIGITS.length;
  if (digitB - digitA > 1) {
    return DIGITS.charAt(Math.round(0.5 * (digitA + digitB)));
  }
  if (b !== null && b.length > 1) {
    return b.slice(0, 1);
  }
  return DIGITS.charAt(digitA) + midpoint(a.slice(1), null);
}

function incr(integer: string): string | null {
  const head = integer.charAt(0);
  const digits = integer.slice(1).split("");
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    const d = DIGITS.indexOf(digits[i] ?? ZERO) + 1;
    if (d < DIGITS.length) {
      digits[i] = DIGITS.charAt(d);
      return head + digits.join("");
    }
    digits[i] = ZERO;
  }
  if (head === "Z") return "a" + ZERO;
  if (head === "z") return null;
  const next = String.fromCharCode(head.charCodeAt(0) + 1);
  if (next > "a") digits.push(ZERO);
  else digits.pop();
  return next + digits.join("");
}

function decr(integer: string): string | null {
  const head = integer.charAt(0);
  const digits = integer.slice(1).split("");
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    const d = DIGITS.indexOf(digits[i] ?? ZERO) - 1;
    if (d >= 0) {
      digits[i] = DIGITS.charAt(d);
      return head + digits.join("");
    }
    digits[i] = TOP;
  }
  if (head === "a") return "Z" + TOP;
  if (head === "A") return null;
  const previous = String.fromCharCode(head.charCodeAt(0) - 1);
  if (previous < "Z") digits.push(TOP);
  else digits.pop();
  return previous + digits.join("");
}

function before(b: string): string {
  const intB = intPart(b);
  if (intB === SMALLEST_INTEGER) {
    return intB + midpoint("", b.slice(intB.length));
  }
  if (intB < b) return intB;
  const below = decr(intB);
  if (below === null || below === SMALLEST_INTEGER) {
    return SMALLEST_INTEGER + midpoint("", null);
  }
  return below;
}

function after(a: string): string {
  const intA = intPart(a);
  const above = incr(intA);
  return above ?? intA + midpoint(a.slice(intA.length), null);
}

/** A key strictly between a and b (null: an open end); a < b is required. */
export function between(a: string | null, b: string | null): string {
  if (a !== null) validate(a);
  if (b !== null) validate(b);
  if (a === null) return b === null ? "a" + ZERO : before(b);
  if (b === null) return after(a);
  if (a >= b) {
    throw new RankError(`${a} is not before ${b}`);
  }
  const intA = intPart(a);
  const intB = intPart(b);
  if (intA === intB) {
    return intA + midpoint(a.slice(intA.length), b.slice(intB.length));
  }
  const above = incr(intA);
  if (above !== null && above < b) return above;
  return intA + midpoint(a.slice(intA.length), null);
}

/** n increasing keys from the start (`a0`, `a1`, ...). */
export function nKeys(n: number): string[] {
  const keys: string[] = [];
  for (let i = 0; i < n; i += 1) {
    keys.push(between(keys.at(-1) ?? null, null));
  }
  return keys;
}
