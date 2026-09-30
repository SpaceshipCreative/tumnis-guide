// The shared primitives (DS-01, ADR-0012): every component colours itself through the
// design tokens, so light, dark and the palette change in one place; and a card is a
// labelled region with a header row, the pattern the dashboard, project and review pages
// share.
import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { Card } from "./Card";

// Tailwind's named hues with a shade (`text-emerald-700`, `border-red-600/40`), and white
// or black text. Gray and black overlays behind a dialog stay allowed.
const RAW_COLOUR =
  /\b(?:[a-z-]+:)*[a-z]+-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|zinc|neutral|stone)-\d{2,3}\b|\btext-(?:white|black)\b/g;

const sources = import.meta.glob<string>(
  [
    "../../**/*.{ts,tsx}",
    "!../../**/*.test.{ts,tsx}",
    "!../../api/**",
    "!../../test/**",
  ],
  { query: "?raw", import: "default", eager: true },
);

test.fails(
  "[DS-01][UX 11] T-DS-01-13 components colour only through the design tokens",
  () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50);
    const raw = Object.entries(sources).flatMap(([path, text]) =>
      [...text.matchAll(RAW_COLOUR)].map((match) => `${path}: ${match[0]}`),
    );
    expect(raw).toEqual([]);
  },
);

test.fails(
  "[DS-01][UX 11] T-DS-01-14 a card is a labelled region with a header row",
  () => {
    render(
      <Card
        title="Today"
        headingLevel={2}
        action={<button type="button">Plan</button>}
      >
        <p>Three tasks</p>
      </Card>,
    );
    const card = screen.getByRole("region", { name: "Today" });
    expect(
      within(card).getByRole("heading", { level: 2, name: "Today" }),
    ).toBeInTheDocument();
    expect(
      within(card).getByRole("button", { name: "Plan" }),
    ).toBeInTheDocument();
    expect(within(card).getByText("Three tasks")).toBeInTheDocument();
  },
);
