// MSW handlers for the storage locations (P1-14): a default server path that is online and
// a share whose marker file is missing. Writes are recorded; a created location comes back
// online, a tested one comes back online, a new default moves the flag.
import { http, HttpResponse, type RequestHandler } from "msw";

import type { Recorder } from "./settings";

export interface LocationRow {
  id: string;
  name: string;
  kind: "server_path" | "s3" | "sftp";
  root: string;
  endpoint: string | null;
  status: "online" | "offline";
  status_reason: string | null;
  is_default: boolean;
  capabilities: Record<string, boolean>;
  version: number;
}

export const DISK_ID = "01890000-0000-7000-8000-0000000000f1";
export const SHARE_ID = "01890000-0000-7000-8000-0000000000f2";

export function locationRow(overrides: Partial<LocationRow> = {}): LocationRow {
  return {
    id: DISK_ID,
    name: "Projects disk",
    kind: "server_path",
    root: "/data/projects",
    endpoint: null,
    status: "online",
    status_reason: null,
    is_default: true,
    capabilities: { network_fs: false },
    version: 1,
    ...overrides,
  };
}

export function storageHandlers(recorder: Recorder): RequestHandler[] {
  let rows: LocationRow[] = [
    locationRow(),
    locationRow({
      id: SHARE_ID,
      name: "NAS share",
      root: "/mnt/nas/tumnis",
      status: "offline",
      status_reason: "marker_missing",
      is_default: false,
      capabilities: { network_fs: true },
      version: 3,
    }),
  ];
  return [
    http.get("*/v1/knowledge/locations", () => HttpResponse.json(rows)),
    http.post("*/v1/knowledge/locations", async ({ request }) => {
      const sent = await recorder.record(request);
      const body = sent.body as {
        name: string;
        kind: LocationRow["kind"];
        root: string;
      };
      const created = locationRow({
        id: `01890000-0000-7000-8000-00000000010${String(rows.length)}`,
        name: body.name,
        kind: body.kind,
        root: body.root,
        is_default: false,
      });
      rows = [...rows, created];
      return HttpResponse.json(created, { status: 201 });
    }),
    http.post(
      "*/v1/knowledge/locations/:id/test",
      async ({ request, params }) => {
        await recorder.record(request);
        rows = rows.map((row) =>
          row.id === params.id
            ? {
                ...row,
                status: "online",
                status_reason: null,
                version: row.version + 1,
              }
            : row,
        );
        return HttpResponse.json(rows.find((row) => row.id === params.id));
      },
    ),
    http.post(
      "*/v1/knowledge/locations/:id/default",
      async ({ request, params }) => {
        await recorder.record(request);
        rows = rows.map((row) => ({
          ...row,
          is_default: row.id === params.id,
          version: row.id === params.id ? row.version + 1 : row.version,
        }));
        return HttpResponse.json(rows.find((row) => row.id === params.id));
      },
    ),
  ];
}
