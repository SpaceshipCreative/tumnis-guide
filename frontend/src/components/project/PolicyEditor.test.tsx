// The policy editor (P2-05, FR-5.6): each action class of the project's approval policy is
// a switch, on when the class needs approval (gated) and off when it is allowed. Save sends
// one PUT /v1/projects/{id}/policy with both lists and the version it read; a 409 shows the
// policy as it is now, and the next save sends the current version (REL-2). Every fixture
// is synthetic.
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";

import { makeProject } from "../../test/factories";
import { server } from "../../test/msw/server";
import { Recorder } from "../../test/msw/settings";
import { renderWithProviders } from "../../test/render";

interface Policy {
  project_id: string;
  gated: string[];
  allowed: string[];
  tool_allowlist: string[];
  max_concurrent_runs: number;
  max_run_minutes: number;
  max_tasks_per_run: number;
  version: number;
}

interface PolicyEditorModule {
  PolicyEditor: (props: {
    project: ReturnType<typeof makeProject>;
  }) => React.JSX.Element;
}

// Loaded at run time, so this file compiles before PolicyEditor.tsx exists.
async function load<T>(path: string): Promise<T> {
  return (await import(/* @vite-ignore */ path)) as T;
}

function policy(projectId: string, overrides: Partial<Policy> = {}): Policy {
  return {
    project_id: projectId,
    gated: ["merge_main", "send_email"],
    allowed: ["push_feature_branch", "open_pull_request"],
    tool_allowlist: [],
    max_concurrent_runs: 2,
    max_run_minutes: 60,
    max_tasks_per_run: 20,
    version: 4,
    ...overrides,
  };
}

function sorted(value: unknown): string[] {
  return [...(value as string[])].sort();
}

test("[P2-05][FR-5.6] toggles gated and allowed and saves with version", async () => {
  const { PolicyEditor } = await load<PolicyEditorModule>("./PolicyEditor");
  const project = makeProject({ name: "Acme site" });
  const stored = policy(project.id);
  const current = policy(project.id, {
    gated: ["merge_main", "send_email", "open_pull_request"],
    allowed: ["push_feature_branch"],
    version: 7,
  });
  const recorder = new Recorder();
  let puts = 0;
  server.use(
    http.get("*/v1/projects/:projectId/policy", () =>
      HttpResponse.json(stored),
    ),
    http.put("*/v1/projects/:projectId/policy", async ({ request }) => {
      const sent = await recorder.record(request);
      puts += 1;
      if (puts === 1) {
        return HttpResponse.json(
          {
            type: "about:blank",
            title: "Stale version",
            status: 409,
            code: "stale_version",
            current,
          },
          { status: 409 },
        );
      }
      const body = sent.body as Policy;
      return HttpResponse.json({
        ...current,
        gated: body.gated,
        allowed: body.allowed,
        version: 8,
      });
    }),
  );
  const { user } = renderWithProviders(<PolicyEditor project={project} />);

  // A gated class is on, an allowed one off.
  const push = await screen.findByRole("switch", {
    name: /push feature branch/i,
  });
  expect(push).toHaveAttribute("aria-checked", "false");
  expect(screen.getByRole("switch", { name: /merge main/i })).toHaveAttribute(
    "aria-checked",
    "true",
  );

  // Moving a class to gated, then saving, sends both lists and the version read.
  await user.click(push);
  expect(push).toHaveAttribute("aria-checked", "true");
  await user.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(1);
  });
  const [first] = recorder.sent;
  expect(first?.method).toBe("PUT");
  expect(first?.path).toBe(`/v1/projects/${project.id}/policy`);
  expect(first?.idempotencyKey).toBeTruthy();
  const sent = first?.body as Policy;
  expect(sent.version).toBe(4);
  expect(sorted(sent.gated)).toEqual([
    "merge_main",
    "push_feature_branch",
    "send_email",
  ]);
  expect(sorted(sent.allowed)).toEqual(["open_pull_request"]);

  // 409: the editor shows the policy as it is now, with a notice.
  expect(await screen.findByRole("status")).toHaveTextContent(
    /changed elsewhere/i,
  );
  expect(
    screen.getByRole("switch", { name: /open pull request/i }),
  ).toHaveAttribute("aria-checked", "true");
  expect(
    screen.getByRole("switch", { name: /push feature branch/i }),
  ).toHaveAttribute("aria-checked", "false");

  // The next save sends the current version.
  await user.click(screen.getByRole("switch", { name: /open pull request/i }));
  await user.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() => {
    expect(recorder.sent).toHaveLength(2);
  });
  const second = recorder.sent[1]?.body as Policy;
  expect(second.version).toBe(7);
  expect(sorted(second.gated)).toEqual(["merge_main", "send_email"]);
  expect(sorted(second.allowed)).toEqual([
    "open_pull_request",
    "push_feature_branch",
  ]);
});
