// A `question` review item (P2-05, FR-5.7): a typed answer, or one tap on a choice, goes
// back to the waiting run as `{answer}`. Every fixture is synthetic.
import { screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { makeReviewItem } from "../../test/msw/review";
import { renderWithProviders } from "../../test/render";
import { QuestionItem } from "./QuestionItem";

const RUN_ID = "01950000-0000-7000-8000-000000000941";

type Decide = (action: "answer", payload: { answer: string }) => void;

function questionItem(choices: string[] = []) {
  return makeReviewItem({
    kind: "question",
    target_type: "task",
    target_id: RUN_ID,
    target_title: "Fix footer link",
    actions: ["answer", "snooze"],
    primary_action: "answer",
    payload: {
      question_id: "01950000-0000-7000-8000-000000000942",
      run_id: RUN_ID,
      prompt: "Which footer color?",
      choices,
    },
  });
}

test("[P2-05][FR-5.7] a typed answer is sent as {answer}; blank sends nothing", async () => {
  const onDecide = vi.fn<Decide>();
  const { user } = renderWithProviders(
    <QuestionItem item={questionItem()} onDecide={onDecide} />,
  );
  const card = screen.getByRole("article", { name: /Fix footer link/ });
  expect(within(card).getByText("Which footer color?")).toBeInTheDocument();

  const send = within(card).getByRole("button", { name: "Answer" });
  expect(send).toBeDisabled();
  const field = within(card).getByRole("textbox", { name: "Answer" });
  await user.type(field, "  ");
  expect(send).toBeDisabled();
  await user.type(field, "Navy ");
  await user.click(send);
  expect(onDecide).toHaveBeenCalledTimes(1);
  expect(onDecide).toHaveBeenCalledWith("answer", { answer: "Navy" });
});

test("[P2-05][FR-5.7] a question with choices is answered with one tap", async () => {
  const onDecide = vi.fn<Decide>();
  const { user } = renderWithProviders(
    <QuestionItem item={questionItem(["Navy", "Teal"])} onDecide={onDecide} />,
  );
  const card = screen.getByRole("article", { name: /Fix footer link/ });
  expect(within(card).queryByRole("textbox")).toBeNull();

  // Enter on the card goes to the first choice and stops there.
  card.focus();
  await user.keyboard("{Enter}");
  const teal = within(card).getByRole("button", { name: "Teal" });
  expect(within(card).getByRole("button", { name: "Navy" })).toHaveFocus();
  expect(onDecide).not.toHaveBeenCalled();

  await user.click(teal);
  expect(onDecide).toHaveBeenCalledWith("answer", { answer: "Teal" });
});
