// Settings (P0-22 route, P0-26 sections): an unknown section lands on account; the
// loader starts the section's read so it is under way while the code renders.
import { createFileRoute, redirect } from "@tanstack/react-router";

import { AccountSection } from "../components/settings/AccountSection";
import { AuditSection } from "../components/settings/AuditSection";
import { DeadLettersSection } from "../components/settings/DeadLettersSection";
import { KeysSection } from "../components/settings/KeysSection";
import {
  accountQuery,
  deadLettersQuery,
  keysQuery,
  sessionsQuery,
  workspaceQuery,
} from "../components/settings/queries";
import {
  isSettingsSection,
  SETTINGS_SECTIONS,
  type SettingsSection,
} from "../components/settings/sections";
import { SessionsSection } from "../components/settings/SessionsSection";
import { SettingsLayout } from "../components/settings/SettingsLayout";
import { WorkspaceSection } from "../components/settings/WorkspaceSection";

export const settingsSections = SETTINGS_SECTIONS;

const SCREENS: Record<SettingsSection, () => React.JSX.Element> = {
  account: AccountSection,
  sessions: SessionsSection,
  keys: KeysSection,
  audit: AuditSection,
  "dead-letters": DeadLettersSection,
  workspace: WorkspaceSection,
};

export const Route = createFileRoute("/settings/$section")({
  beforeLoad: ({ params }) => {
    if (!isSettingsSection(params.section)) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect
      throw redirect({
        to: "/settings/$section",
        params: { section: "account" },
        replace: true,
      });
    }
  },
  loader: ({ context: { queryClient }, params }) => {
    const started = (read: Promise<unknown>) => {
      read.catch(() => undefined); // the section shows its own error
    };
    switch (params.section) {
      case "account":
        started(queryClient.query(accountQuery()));
        break;
      case "sessions":
        started(queryClient.query(sessionsQuery()));
        break;
      case "keys":
        started(queryClient.query(keysQuery()));
        break;
      case "dead-letters":
        started(queryClient.query(deadLettersQuery()));
        break;
      case "workspace":
        started(queryClient.query(workspaceQuery()));
        break;
      default:
        break; // the audit log reads by page
    }
  },
  component: SettingsPage,
});

function SettingsPage() {
  const { section } = Route.useParams();
  const Screen = isSettingsSection(section) ? SCREENS[section] : AccountSection;
  return (
    <SettingsLayout>
      <Screen key={section} />
    </SettingsLayout>
  );
}
