// Settings (P0-22 route, P0-26 sections): an unknown section lands on account; the
// loader starts the section's read so it is under way while the code renders.
import { createFileRoute, redirect } from "@tanstack/react-router";

import { AccountSection } from "../components/settings/AccountSection";
import { AgentsSection } from "../components/settings/AgentsSection";
import { AuditSection } from "../components/settings/AuditSection";
import { CalendarSection } from "../components/settings/CalendarSection";
import { CalibrationSection } from "../components/settings/calibration/Calibration";
import { DeadLettersSection } from "../components/settings/DeadLettersSection";
import { KeysSection } from "../components/settings/KeysSection";
import {
  metricsRange,
  MetricsSection,
} from "../components/settings/MetricsSection";
import {
  accountQuery,
  calendarAccountsQuery,
  calibrationQuery,
  deadLettersQuery,
  keysQuery,
  metricsSummaryQuery,
  profilesQuery,
  runnersQuery,
  sessionsQuery,
  storageQuery,
  unattendedWindowQuery,
  workingHoursQuery,
  workspaceQuery,
} from "../components/settings/queries";
import {
  isSettingsSection,
  SETTINGS_SECTIONS,
  type SettingsSection,
} from "../components/settings/sections";
import { SessionsSection } from "../components/settings/SessionsSection";
import { SettingsLayout } from "../components/settings/SettingsLayout";
import { StorageSection } from "../components/settings/StorageSection";
import { UnattendedSection } from "../components/settings/UnattendedSection";
import { VoiceSection } from "../components/settings/voice/VoiceSection";
import { WorkingHoursSection } from "../components/settings/WorkingHoursSection";
import { WorkspaceSection } from "../components/settings/WorkspaceSection";
import { voiceQuery } from "../lib/speech/voiceSettings";

export const settingsSections = SETTINGS_SECTIONS;

const SCREENS: Record<SettingsSection, () => React.JSX.Element> = {
  account: AccountSection,
  sessions: SessionsSection,
  keys: KeysSection,
  audit: AuditSection,
  "dead-letters": DeadLettersSection,
  agents: AgentsSection,
  workspace: WorkspaceSection,
  "working-hours": WorkingHoursSection,
  unattended: UnattendedSection,
  calendar: () => <CalendarSection />,
  storage: StorageSection,
  calibration: CalibrationSection,
  metrics: MetricsSection,
  voice: VoiceSection,
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
      case "agents":
        started(queryClient.query(runnersQuery()));
        started(queryClient.query(profilesQuery()));
        break;
      case "workspace":
        started(queryClient.query(workspaceQuery()));
        break;
      case "working-hours":
        started(queryClient.query(workingHoursQuery()));
        break;
      case "unattended":
        started(queryClient.query(unattendedWindowQuery()));
        break;
      case "calendar":
        started(queryClient.query(calendarAccountsQuery()));
        break;
      case "storage":
        started(queryClient.query(storageQuery()));
        break;
      case "calibration":
        started(queryClient.query(calibrationQuery()));
        break;
      case "metrics":
        // The two weeks are the workspace's own days, so its zone is read first.
        started(
          queryClient
            .query(workspaceQuery())
            .then((workspace) =>
              queryClient.query(
                metricsSummaryQuery(
                  metricsRange(new Date(), workspace.timezone),
                ),
              ),
            ),
        );
        break;
      case "voice":
        started(queryClient.query(voiceQuery()));
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
