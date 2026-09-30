# ruff: noqa: SIM115
"""TEMPORARY (ci-health): summarise a py-spy raw (collapsed, --threads) profile of T-01's
serial run: samples per thread, top self frames, and inclusive time of chosen frames."""

import collections
import sys

TAGS = (
    "gc_collect",
    "collect",
    "_connect",
    "connect",
    "psycopg",
    "sqlalchemy",
    "dbos",
    "pydantic",
    "jsonschema",
    "structlog",
    "opentelemetry",
    "starlette",
    "fastapi",
    "httpx",
    "argon2",
    "json",
    "deepcopy",
    "tumnis",
    "_poll",
    "select",
    "to_thread",
    "logging",
)

per_thread: collections.Counter[str] = collections.Counter()
self_frames: collections.Counter[str] = collections.Counter()
incl: collections.Counter[str] = collections.Counter()
incl_fn: collections.Counter[str] = collections.Counter()
total = 0
for line in open(sys.argv[1]):
    stack, _, n = line.rstrip().rpartition(" ")
    if not n.isdigit():
        continue
    count = int(n)
    frames = stack.split(";")
    thread = frames[0].split(" ")[0] if frames else "?"
    total += count
    per_thread[frames[0][:60]] += count
    self_frames[frames[-1][:110]] += count
    seen: set[str] = set()
    seen_fn: set[str] = set()
    for f in frames[1:]:
        low = f.lower()
        for tag in TAGS:
            if tag in low and tag not in seen:
                seen.add(tag)
                incl[tag] += count
        fn = f.split(" (")[0]
        key = f"{fn} ({f.split('(')[-1].split(':')[0].rsplit('/', 2)[-1] if '(' in f else ''})"
        if key not in seen_fn:
            seen_fn.add(key)
            incl_fn[key] += count
print(f"PROF total samples {total}")
for k, v in per_thread.most_common(15):
    print(f"PROF thread {100 * v / total:5.1f}% {k}")
for k, v in self_frames.most_common(40):
    print(f"PROF self {100 * v / total:5.1f}% {k}")
for k, v in incl.most_common():
    print(f"PROF incl-tag {100 * v / total:5.1f}% {k}")
for k, v in incl_fn.most_common(120):
    print(f"PROF incl-fn {100 * v / total:5.1f}% {k}")
