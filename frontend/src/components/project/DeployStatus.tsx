// Coolify deploy status (P2-14, FR-12.2): the project card's chip per linked application
// (last deployment: status, time, commit) with the preview URLs of open pull requests.
// Spec interface only; the chip lands with the implementation.

export interface DeployPreview {
  readonly pull_request_id: number;
  readonly url: string;
  readonly status: string;
  readonly commit?: string | null | undefined;
  readonly finished_at?: string | null | undefined;
}

export interface LastDeploy {
  readonly status: string;
  readonly commit?: string | null | undefined;
  readonly created_at: string;
  readonly finished_at?: string | null | undefined;
}

export interface AppDeployStatus {
  readonly app_uuid: string;
  readonly name?: string | null | undefined;
  readonly last?: LastDeploy | null | undefined;
  readonly previews: readonly DeployPreview[];
  readonly checked_at?: string | null | undefined;
  readonly error?: string | null | undefined;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- the spec stub renders nothing
export function DeployStatus(_props: {
  apps: readonly AppDeployStatus[];
  timeZone: string;
}) {
  return null;
}
