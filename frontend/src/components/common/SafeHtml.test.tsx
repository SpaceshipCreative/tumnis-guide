// SafeHtml renders the server-sanitized string as markup (P0-16, SEC-4).
import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { SafeHtml } from "./SafeHtml";

test("[P0-16][SEC-4] SafeHtml renders sanitized markup in the chosen element", () => {
  const { container } = render(
    <SafeHtml
      as="article"
      className="note"
      html={
        '<p>Read <a href="https://example.com/" rel="noopener noreferrer nofollow">the docs</a></p>'
      }
    />,
  );
  const article = container.querySelector("article.note");
  expect(article).not.toBeNull();
  expect(screen.getByRole("link", { name: "the docs" })).toHaveAttribute(
    "href",
    "https://example.com/",
  );
});
