import { screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import { AUDIT_NEXT, auditHandlers, Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { AuditSection } from "./AuditSection";

describe("AuditSection", () => {
  test("[P0-26][SEC-3] T-P0-26-06 audit list pages by cursor and CSV export links to audit.csv", async () => {
    const recorder = new Recorder();
    server.use(...auditHandlers(recorder));
    const { user } = renderWithProviders(<AuditSection />);

    expect(await screen.findByText("key.created")).toBeInTheDocument();
    expect(screen.getByText("auth.login")).toBeInTheDocument();
    expect(screen.queryByText("setup.completed")).not.toBeInTheDocument();
    expect(recorder.sent).toHaveLength(1);
    expect(
      new URLSearchParams(recorder.sent[0]?.search).get("cursor"),
    ).toBeNull();

    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("setup.completed")).toBeInTheDocument();
    expect(screen.getByText("key.created")).toBeInTheDocument();
    expect(new URLSearchParams(recorder.sent[1]?.search).get("cursor")).toBe(
      AUDIT_NEXT,
    );
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();

    // The export follows the date filters and downloads.
    await user.type(screen.getByLabelText("From"), "2026-03-01");
    await user.type(screen.getByLabelText("To"), "2026-03-09");
    const exportLink = screen.getByRole("link", { name: "Export CSV" });
    await waitFor(() => {
      const href = new URL(
        exportLink.getAttribute("href") ?? "",
        window.location.origin,
      );
      expect(href.pathname).toBe("/v1/audit.csv");
      expect(href.searchParams.get("from")).toBe("2026-03-01T00:00:00Z");
      expect(href.searchParams.get("to")).toBe("2026-03-10T00:00:00Z");
    });
    expect(exportLink).toHaveAttribute("download");
    // The list follows the same filters.
    await waitFor(() => {
      const last = new URLSearchParams(recorder.sent.at(-1)?.search);
      expect(last.get("from")).toBe("2026-03-01T00:00:00Z");
      expect(last.get("to")).toBe("2026-03-10T00:00:00Z");
    });
  });
});
