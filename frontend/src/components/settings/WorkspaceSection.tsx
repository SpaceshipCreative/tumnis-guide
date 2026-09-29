// Settings > Workspace (P0-26, REL-6, FR-3.8): the workspace timezone and the default
// subtask threshold. The form validates with the generated zod schema (the server's
// bounds, through OpenAPI), plus one client-side rule: the zone comes from the list. A
// save sends PUT /v1/settings/workspace with the version it read; a 409 shows the
// current values with a notice (REL-2).
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import type * as z from "zod";

import { settingsGetWorkspaceSettingsQueryKey } from "../../api/@tanstack/react-query.gen";
import type { WorkspaceSettingsOut } from "../../api/types.gen";
import { zWorkspaceSettingsIn, zWorkspaceSettingsOut } from "../../api/zod.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { browserZone, ianaZones } from "../../lib/timezones";
import { workspaceQuery } from "./queries";
import { BUTTON, ERROR, HEADING, HINT, INPUT, LABEL, SECTION } from "./styles";
import { TimezonePicker } from "./TimezonePicker";

function workspaceSchema(zones: readonly string[], saved: string) {
  return zWorkspaceSettingsIn.extend({
    timezone: zWorkspaceSettingsIn.shape.timezone.refine(
      (zone) => zone != null && (zones.includes(zone) || zone === saved),
      "Pick a timezone from the list",
    ),
  });
}
type Values = z.infer<ReturnType<typeof workspaceSchema>>;

export function WorkspaceSection() {
  const loaded = useQuery(workspaceQuery());
  return (
    <section aria-labelledby="workspace-title" className={SECTION}>
      <h2 id="workspace-title" className={HEADING}>
        Workspace
      </h2>
      {loaded.data ? (
        <WorkspaceForm loaded={loaded.data} />
      ) : loaded.isError ? (
        <p role="alert" className={ERROR}>
          The workspace settings could not be loaded.
        </p>
      ) : (
        <p className={HINT}>Loading…</p>
      )}
    </section>
  );
}

function WorkspaceForm({ loaded }: { loaded: WorkspaceSettingsOut }) {
  const queryClient = useQueryClient();
  const ids = {
    zone: useId(),
    threshold: useId(),
    hint: useId(),
    error: useId(),
  };
  const zones = useMemo(() => ianaZones(), []);
  const device = useMemo(() => browserZone(), []);
  const [saved, setSaved] = useState(loaded);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const schema = useMemo(
    () => workspaceSchema(zones, saved.timezone),
    [zones, saved],
  );
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: loaded,
  });
  const { errors } = form.formState;

  function show(current: WorkspaceSettingsOut) {
    queryClient.setQueryData(settingsGetWorkspaceSettingsQueryKey(), current);
    setSaved(current);
    form.reset(current);
  }

  const save = useWrite<
    Values & { version: number; idempotencyKey?: string },
    WorkspaceSettingsOut
  >({
    mutationFn: ({ version, idempotencyKey, ...body }) =>
      apiWrite({
        kind: "update",
        method: "PUT",
        path: "/settings/workspace",
        body,
        version,
        idempotencyKey,
        schema: zWorkspaceSettingsOut,
      }),
    onSuccess: (current) => {
      show(current);
      setNotice("Saved.");
    },
    onError: (error) => {
      if (error instanceof ConflictError) {
        const current = zWorkspaceSettingsOut.safeParse(error.current);
        if (current.success) {
          show(current.data);
          setNotice(
            "These settings changed elsewhere; you are now seeing the current values.",
          );
          return;
        }
      }
      setFailure(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The settings could not be saved.",
      );
    },
  });

  const submit = form.handleSubmit((values) => {
    setNotice(null);
    setFailure(null);
    save.mutate({
      timezone: values.timezone,
      subtask_threshold_min: values.subtask_threshold_min,
      version: saved.version,
    });
  });

  const zone = form.register("timezone");
  const threshold = form.register("subtask_threshold_min", {
    valueAsNumber: true,
  });
  const zoneError = errors.timezone?.message;
  const thresholdError = errors.subtask_threshold_min?.message;

  return (
    <form
      noValidate
      onSubmit={(event) => void submit(event)}
      className="flex flex-col gap-4"
    >
      <TimezonePicker
        id={ids.zone}
        name={zone.name}
        ref={zone.ref}
        value={form.watch("timezone") ?? ""}
        zones={zones}
        device={device}
        invalid={zoneError !== undefined}
        describedBy={zoneError === undefined ? undefined : `${ids.zone}-error`}
        onChange={(event) => void zone.onChange(event)}
        onBlur={(event) => void zone.onBlur(event)}
        onUseDevice={(value) => {
          form.setValue("timezone", value, {
            shouldDirty: true,
            shouldValidate: true,
          });
        }}
      />
      {zoneError !== undefined && (
        <p id={`${ids.zone}-error`} role="alert" className={ERROR}>
          {zoneError}
        </p>
      )}
      <div className="flex flex-col gap-1">
        <label htmlFor={ids.threshold} className={LABEL}>
          Default subtask threshold (minutes)
        </label>
        <input
          id={ids.threshold}
          type="number"
          inputMode="numeric"
          className={INPUT}
          aria-invalid={thresholdError !== undefined}
          aria-describedby={thresholdError === undefined ? ids.hint : ids.error}
          {...threshold}
        />
        {thresholdError === undefined ? (
          <p id={ids.hint} className={HINT}>
            Tasks estimated above this get split into subtasks.
          </p>
        ) : (
          <p id={ids.error} role="alert" className={ERROR}>
            {thresholdError}
          </p>
        )}
      </div>
      {failure !== null && (
        <p role="alert" className={ERROR}>
          {failure}
        </p>
      )}
      <p role="status" className={HINT}>
        {notice}
      </p>
      <div>
        <button type="submit" className={BUTTON} disabled={save.isPending}>
          Save
        </button>
      </div>
    </form>
  );
}
