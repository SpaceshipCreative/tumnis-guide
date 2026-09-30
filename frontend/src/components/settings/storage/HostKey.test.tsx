import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

// Obviously fake fingerprints (the real ones are made by the server at test time).
const SHOWN = `SHA256:${"Q".repeat(43)}`;
const OTHER = `SHA256:${"R".repeat(43)}`;

interface HostKeyModule {
  HostKey: (props: {
    fingerprint: string;
    onConfirm: (fingerprint: string) => void;
  }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before HostKey.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

describe("HostKey", () => {
  test.fails(
    "[P3-14][FR-15.7] T-P3-14-19 fingerprint must be confirmed",
    async () => {
      const { HostKey } = await load<HostKeyModule>("./HostKey");
      const onConfirm = vi.fn();
      const user = userEvent.setup();
      render(<HostKey fingerprint={SHOWN} onConfirm={onConfirm} />);

      // The server's fingerprint is shown, with where to find the server's own.
      expect(screen.getByText(SHOWN)).toBeInTheDocument();
      expect(screen.getByText(/ssh-keygen -lf/)).toBeInTheDocument();
      const field = screen.getByLabelText("Fingerprint from the server");
      const confirm = screen.getByRole("button", { name: "Trust this server" });
      expect(confirm).toBeDisabled();

      // A wrong fingerprint, typed: still disabled, and nothing sent.
      await user.type(field, OTHER);
      expect(confirm).toBeDisabled();
      await user.click(confirm);
      expect(onConfirm).not.toHaveBeenCalled();

      // Almost right (one character short): still disabled.
      await user.clear(field);
      await user.type(field, SHOWN.slice(0, -1));
      expect(confirm).toBeDisabled();

      // The exact fingerprint, pasted with stray whitespace: enabled.
      await user.clear(field);
      await user.click(field);
      await user.paste(`  ${SHOWN}\n`);
      expect(confirm).toBeEnabled();
      await user.click(confirm);
      expect(onConfirm).toHaveBeenCalledTimes(1);
      expect(onConfirm).toHaveBeenCalledWith(SHOWN);
    },
  );
});
