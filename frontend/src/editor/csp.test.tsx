// The note editor under the strict CSP (P1-17, SEC-4). APP-08 (application test): opening
// the Brief logged "Applying inline style violates ... style-src 'self'" because Tiptap
// injects a <style data-tiptap-style> tag, by default, when an editor mounts. The app
// ships those base rules in its own stylesheet instead, so the editor may add no <style>
// element to the page.
import { screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { renderWithProviders } from "../test/render";
import { MarkdownField } from "./Editor";

const injected = () => document.querySelectorAll("style[data-tiptap-style]");

afterEach(() => {
  injected().forEach((tag) => {
    tag.remove();
  });
});

test("[P1-17][SEC-4] APP-08 the rich editor adds no inline <style> to the page", async () => {
  renderWithProviders(
    <MarkdownField
      markdown={"Logo and site refresh\n"}
      label="Brief"
      projectId={null}
      suggest={{
        tasks: () => Promise.resolve([]),
        docs: () => Promise.resolve([]),
      }}
      onChange={() => undefined}
    />,
  );
  expect(await screen.findByRole("textbox", { name: "Brief" })).toBeVisible();
  expect(injected()).toHaveLength(0);
});
