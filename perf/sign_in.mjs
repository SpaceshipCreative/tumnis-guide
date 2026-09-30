#!/usr/bin/env node
// Signs in to the compose.test stack as the load set's user and prepares the perf run
// (P0-29): an API key for k6 (tasks read and write), the id of "Load project 01" for
// quick-add, and the session cookie Lighthouse CI sends (`collect.settings.extraHeaders`).
// Run after `POST /v1/test/reset?set=load`. Test data only: the stack runs with fakes,
// and the user, password and TOTP secret come from backend/fixtures/load/load.yaml.
//
//   node perf/sign_in.mjs [--base http://localhost:8080] [--github-env]
//
// Prints TUMNIS_KEY, PROJECT_ID and LHCI_EXTRA_HEADERS as KEY=value lines; with
// --github-env it masks the secrets and appends the lines to $GITHUB_ENV instead.
import { createHmac, randomUUID } from "node:crypto";
import { appendFileSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LOAD_SET = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "backend",
  "fixtures",
  "load",
  "load.yaml",
);
const PROJECT = "Load project 01";
const SESSION = "__Host-tumnis_session";
const CSRF = "__Host-tumnis_csrf";

function loadUser() {
  const text = readFileSync(LOAD_SET, "utf8");
  const field = (pattern) => {
    const value = pattern.exec(text)?.[1];
    if (value === undefined) throw new Error(`load.yaml has no ${pattern.source}`);
    return value;
  };
  return {
    email: field(/email:\s*([^\s,}]+)/),
    password: field(/password:\s*"?([^"\s,}]+)"?/),
    secret: field(/totp_secret:\s*([A-Z2-7]+)/),
  };
}

function base32(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  }
  return Buffer.from((bits.match(/.{8}/g) ?? []).map((byte) => parseInt(byte, 2)));
}

/** RFC 6238 TOTP (SHA-1, 30 s step, 6 digits) at `at`. */
function totp(secret, at) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at.getTime() / 30_000)));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

async function call(base, path, { method = "GET", body, cookies = {}, headers = {} } = {}) {
  const cookie = Object.entries(cookies)
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(cookie ? { Cookie: cookie } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} -> ${String(response.status)} ${await response.text()}`);
  }
  const set = Object.fromEntries(
    response.headers.getSetCookie().map((line) => {
      const [pair] = line.split(";");
      const at = pair.indexOf("=");
      return [pair.slice(0, at), pair.slice(at + 1)];
    }),
  );
  return { json: response.status === 204 ? null : await response.json(), set };
}

async function main() {
  const argv = process.argv.slice(2);
  const at = argv.indexOf("--base");
  const base = (at >= 0 ? argv[at + 1] : "http://localhost:8080").replace(/\/$/, "");
  const githubEnv = argv.includes("--github-env");

  const user = loadUser();
  const login = await call(base, "/v1/auth/login", {
    method: "POST",
    body: { email: user.email, password: user.password },
  });
  const signedIn = await call(base, "/v1/auth/totp", {
    method: "POST",
    body: { preauth: login.json.preauth, code: totp(user.secret, new Date()) },
  });
  const cookies = { [SESSION]: signedIn.set[SESSION], [CSRF]: signedIn.set[CSRF] };
  if (!cookies[SESSION] || !cookies[CSRF]) throw new Error("the sign-in set no session cookie");

  const created = await call(base, "/v1/keys", {
    method: "POST",
    cookies,
    headers: { "X-CSRF-Token": cookies[CSRF], "Idempotency-Key": randomUUID() },
    body: { name: "k6 perf", scopes: ["tasks:read", "tasks:write"] },
  });
  const projects = await call(base, "/v1/projects?limit=50", { cookies });
  const project = projects.json.items.find((p) => p.name === PROJECT);
  if (!project) throw new Error(`no project named ${PROJECT}; reset to the load set first`);

  const values = {
    TUMNIS_KEY: created.json.key,
    PROJECT_ID: project.id,
    LHCI_EXTRA_HEADERS: JSON.stringify({ Cookie: `${SESSION}=${cookies[SESSION]}` }),
  };
  const lines = Object.entries(values).map(([name, value]) => `${name}=${value}`);
  if (githubEnv && process.env.GITHUB_ENV) {
    // Test-stack secrets, but masked all the same.
    console.log(`::add-mask::${values.TUMNIS_KEY}`);
    console.log(`::add-mask::${cookies[SESSION]}`);
    appendFileSync(process.env.GITHUB_ENV, `${lines.join("\n")}\n`);
    console.log(`signed in as ${user.email}; PROJECT_ID=${project.id}`);
  } else {
    console.log(lines.join("\n"));
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
