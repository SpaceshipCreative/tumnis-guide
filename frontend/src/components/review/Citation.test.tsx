// Citations (P2-17, FR-15.4): a `tumnis://doc/<id>#page=<n>` result link opens the
// document's file at that page; a web link stays as it is; anything else is no link.
import { screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { renderWithProviders } from "../../test/render";
import { Citation, linkHref } from "./Citation";

const DOC = "0b9e6c1e-2f4a-4c3b-9d1e-5a6f7b8c9d0e";

test("[P2-17][FR-15.4] a citation links to the document's file at its page", () => {
  renderWithProviders(
    <Citation url={`tumnis://doc/${DOC}#page=4`} label="Brand guide, page 4" />,
  );
  const link = screen.getByRole("link", { name: "Brand guide, page 4" });
  expect(link.getAttribute("href")).toBe(
    `${window.location.origin}/v1/files/${DOC}#page=4`,
  );
});

test("[P2-17][FR-15.4] only web links and citations become links", () => {
  expect(linkHref(`tumnis://doc/${DOC}`)).toBe(
    `${window.location.origin}/v1/files/${DOC}`,
  );
  expect(linkHref("https://example.com/a")).toBe("https://example.com/a");
  for (const bad of [
    "javascript:alert(1)",
    "tumnis://doc/not-a-uuid",
    `tumnis://doc/${DOC}#page=0`,
    `tumnis://task/${DOC}`,
    "data:text/html,x",
  ]) {
    expect(linkHref(bad), bad).toBeNull();
  }
});
