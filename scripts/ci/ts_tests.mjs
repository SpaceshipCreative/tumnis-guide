#!/usr/bin/env node
// Extract Vitest and Playwright test blocks with the TypeScript compiler API (P0-03).
//
//   node scripts/ci/ts_tests.mjs <file>...  ->  {"<file>": {"<describe> > <title>": block}}
//
// block = {title, line, tags, fails, skip, text}. `text` is the call printed after the
// normalization spec-guard allows: `test.fails(` reads as `test(`, and a bare
// `test.fail();` as the first statement of a body is dropped. TypeScript resolves from
// frontend/node_modules (run `npm ci` in frontend/ first).
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(here, "..", "..", "frontend", "package.json"));
const ts = require("typescript");

const TEST = /^(test|it)(\.(only|skip|fails|fail|fixme|concurrent|sequential))*$/;
const DESCRIBE = /^(test\.)?describe(\.(only|skip|fixme|serial|parallel|concurrent|sequential))*$/;
const printer = ts.createPrinter({ removeComments: true });

function calleeText(call, sf) {
  return call.expression.getText(sf).replace(/\s+/g, "");
}

function titleOf(arg, sf) {
  if (arg && ts.isStringLiteralLike(arg)) return arg.text;
  return arg ? arg.getText(sf) : "<untitled>";
}

function isFunction(node) {
  return node && (ts.isArrowFunction(node) || ts.isFunctionExpression(node));
}

// A bare `test.fail();` (or `test.fail()` with no arguments) statement.
function isBareFail(statement, sf) {
  return (
    ts.isExpressionStatement(statement) &&
    ts.isCallExpression(statement.expression) &&
    calleeText(statement.expression, sf) === "test.fail" &&
    statement.expression.arguments.length === 0
  );
}

function printFunction(fn, sf) {
  const params = fn.parameters.map((p) => printer.printNode(ts.EmitHint.Unspecified, p, sf));
  const isAsync = (fn.modifiers ?? []).some((m) => m.kind === ts.SyntaxKind.AsyncKeyword);
  const head = `${isAsync ? "async " : ""}(${params.join(", ")}) =>`;
  if (!ts.isBlock(fn.body)) {
    const expression = printer.printNode(ts.EmitHint.Expression, fn.body, sf);
    return { text: `${head} ${expression}`, leadingFail: false };
  }
  const statements = [...fn.body.statements];
  const leadingFail = statements.length > 0 && isBareFail(statements[0], sf);
  const kept = leadingFail ? statements.slice(1) : statements;
  const body = kept.map((s) => printer.printNode(ts.EmitHint.Unspecified, s, sf)).join("\n");
  return { text: `${head} {\n${body}\n}`, leadingFail };
}

function tagsOf(call, title, sf) {
  const tags = [...title.matchAll(/(?:^|\s)(@[\w.-]+)/g)].map((m) => m[1]);
  for (const arg of call.arguments) {
    if (!ts.isObjectLiteralExpression(arg)) continue;
    for (const prop of arg.properties) {
      if (!ts.isPropertyAssignment(prop) || prop.name.getText(sf) !== "tag") continue;
      const value = prop.initializer;
      const items = ts.isArrayLiteralExpression(value) ? value.elements : [value];
      for (const item of items) if (ts.isStringLiteralLike(item)) tags.push(item.text);
    }
  }
  return tags;
}

function extract(file) {
  const source = readFileSync(file, "utf8");
  const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, kind);
  const blocks = {};

  function visit(node, describes) {
    if (ts.isCallExpression(node)) {
      const callee = calleeText(node, sf);
      const args = node.arguments;
      const fn = args.length > 0 ? args[args.length - 1] : undefined;
      if (DESCRIBE.test(callee) && isFunction(fn)) {
        const title = titleOf(args[0], sf);
        ts.forEachChild(fn.body, (child) => visit(child, [...describes, title]));
        return;
      }
      if (TEST.test(callee) && args.length >= 2 && isFunction(fn)) {
        const title = titleOf(args[0], sf);
        const printedArgs = [];
        let leadingFail = false;
        for (const arg of args) {
          if (arg === fn) {
            const printed = printFunction(arg, sf);
            printedArgs.push(printed.text);
            leadingFail = printed.leadingFail;
          } else printedArgs.push(printer.printNode(ts.EmitHint.Expression, arg, sf));
        }
        const base = callee.split(".")[0];
        const modifiers = callee.split(".").slice(1);
        let key = [...describes, title].join(" > ");
        for (let n = 2; key in blocks; n++) key = `${[...describes, title].join(" > ")} #${n}`;
        blocks[key] = {
          title,
          line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
          tags: tagsOf(node, title, sf),
          fails: leadingFail || modifiers.includes("fails") || modifiers.includes("fail"),
          skip: modifiers.includes("skip") || modifiers.includes("fixme"),
          text: `${base}(${printedArgs.join(", ")})`,
        };
        return;
      }
    }
    ts.forEachChild(node, (child) => visit(child, describes));
  }

  visit(sf, []);
  return blocks;
}

const out = {};
for (const file of process.argv.slice(2)) out[file] = extract(file);
process.stdout.write(JSON.stringify(out));
