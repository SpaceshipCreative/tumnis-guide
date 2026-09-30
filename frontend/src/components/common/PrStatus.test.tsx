// The pull request chip (P2-13, FR-12.1): state, combined checks and review, in words and
// in the link's accessible name, so a red build is not told by colour alone.
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { PrStatus, type PullRequest } from "./PrStatus";

const BASE: PullRequest = {
  artifact_id: "01950000-0000-7000-8000-000000000042",
  url: "https://github.com/acme-example/site/pull/42",
  repo: "acme-example/site",
  number: 42,
  title: "Add the booking form",
  state: "open",
  draft: false,
  checks: "green",
  review: "approved",
  checked_at: "2026-03-09T12:00:00Z",
};

test.fails("[P2-13][FR-12.1] shows state checks and review", () => {
  const { rerender } = render(<PrStatus pr={BASE} />);
  const link = screen.getByRole("link", {
    name: "Pull request acme-example/site#42, Add the booking form: open, checks passing, approved",
  });
  expect(link).toHaveAttribute("href", BASE.url);
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.getByText("#42")).toBeInTheDocument();
  expect(screen.getByText("Checks passing")).toBeInTheDocument();
  expect(screen.getByText("Approved")).toBeInTheDocument();
  expect(link.closest("[data-checks]")).toHaveAttribute("data-checks", "green");

  rerender(
    <PrStatus
      pr={{ ...BASE, checks: "red", review: "changes_requested", draft: true }}
    />,
  );
  expect(
    screen.getByRole("link", {
      name: "Pull request acme-example/site#42, Add the booking form: draft, checks failing, changes requested",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("Checks failing")).toBeInTheDocument();
  expect(screen.getByText("Changes requested")).toBeInTheDocument();
  expect(screen.getByText("Draft")).toBeInTheDocument();

  rerender(
    <PrStatus pr={{ ...BASE, checks: "pending", review: "review_required" }} />,
  );
  expect(screen.getByText("Checks running")).toBeInTheDocument();
  expect(screen.getByText("Review required")).toBeInTheDocument();

  rerender(
    <PrStatus
      pr={{ ...BASE, state: "merged", checks: "none", review: "none" }}
    />,
  );
  expect(
    screen.getByRole("link", {
      name: "Pull request acme-example/site#42, Add the booking form: merged, no checks, no review",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("Merged")).toBeInTheDocument();

  rerender(<PrStatus pr={{ ...BASE, state: "closed" }} />);
  expect(screen.getByText("Closed")).toBeInTheDocument();

  rerender(
    <PrStatus
      pr={{
        ...BASE,
        title: null,
        state: null,
        checks: null,
        review: null,
        checked_at: null,
      }}
    />,
  );
  expect(
    screen.getByRole("link", {
      name: "Pull request acme-example/site#42: status not read yet",
    }),
  ).toBeInTheDocument();
  expect(screen.getByText("Checking…")).toBeInTheDocument();
  expect(screen.queryByText("Checks passing")).toBeNull();
});
