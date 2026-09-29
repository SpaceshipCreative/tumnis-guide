import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { server } from "../../test/msw/server";
import {
  DEAD_LETTERS,
  deadLetterHandlers,
  Recorder,
} from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";
import { DeadLettersSection } from "./DeadLettersSection";

describe("DeadLettersSection", () => {
  test.fails(
    "[P0-26][REL-3] T-P0-26-07 retry and discard call the right endpoints; discard asks first",
    async () => {
      const recorder = new Recorder();
      server.use(...deadLetterHandlers(recorder));
      const { user } = renderWithProviders(<DeadLettersSection />);

      const [first, second] = DEAD_LETTERS;
      if (!first || !second) throw new Error("three dead letters");
      const items = await screen.findAllByRole("listitem");
      expect(items).toHaveLength(3);
      const [firstItem, secondItem] = items;
      if (!firstItem || !secondItem) throw new Error("three items");
      expect(within(firstItem).getByText(first.subscriber)).toBeInTheDocument();
      expect(within(firstItem).getByText(first.error)).toBeInTheDocument();

      await user.click(
        within(firstItem).getByRole("button", { name: "Retry" }),
      );
      await waitFor(() => {
        expect(recorder.writes()).toEqual([
          `POST /v1/dead-letters/${first.id}/retry`,
        ]);
      });
      expect(recorder.sent[0]?.body).toEqual({ version: first.version });

      // Discard asks first; cancelling sends nothing.
      await user.click(
        within(secondItem).getByRole("button", { name: "Discard" }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Discard this dead letter?",
      });
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
      expect(recorder.writes()).toHaveLength(1);

      await user.click(
        within(secondItem).getByRole("button", { name: "Discard" }),
      );
      const again = await screen.findByRole("dialog", {
        name: "Discard this dead letter?",
      });
      await user.click(
        within(again).getByRole("button", { name: "Discard dead letter" }),
      );
      await waitFor(() => {
        expect(recorder.writes()).toEqual([
          `POST /v1/dead-letters/${first.id}/retry`,
          `POST /v1/dead-letters/${second.id}/discard`,
        ]);
      });
      expect(recorder.sent[1]?.body).toEqual({ version: second.version });
      for (const sent of recorder.sent) {
        expect(sent.idempotencyKey).toBeTruthy();
      }
    },
  );
});
