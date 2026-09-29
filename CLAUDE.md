# CLAUDE.md

Read `AGENTS.md` first. It holds the binding rules (TDD, module boundaries, where things go, markers, time, commands, never). This file adds only Claude Code specifics.

- Run `make check` before every commit, and commit only when it passes.
- Branches: spec PR on `wp/<WP>-spec` (title `[<WP>] spec: <title>`), implementation on `wp/<WP>-impl` (`-impl-2` and so on for large WPs; title `[<WP>] impl: <title>`).
- Commits: conventional commits scoped by module, for example `test(tasks): …`, `feat(tasks): …`, `refactor(core): …`.
- To find a WP: look it up in `docs/plan/work-packages.yaml` (ID, needs, traces), then read its section in `docs/IMPLEMENTATION-PLAN-DETAILED.md` (files, interfaces, spec tests, TDD sequence, done checklist). Start a WP only when its `needs` are merged.
- Never add the `spec-change` label, and never edit an assertion in an existing test to make it pass. If a spec test looks wrong, stop and ask Scott.
