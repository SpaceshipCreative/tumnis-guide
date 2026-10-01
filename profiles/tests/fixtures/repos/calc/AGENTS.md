# calc

A tiny calculator library. Rules for anyone changing it, people and agents alike:

- Write a failing test first, in `tests/`, then the code that makes it pass.
- Run the whole suite with `make test` before and after every change. Never report a
  change as done while any test fails.
- Keep each function small and pure: numbers in, a number out, no printing.
- Work on a feature branch; never push to `main`.
