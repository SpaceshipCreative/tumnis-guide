// Project > Folder (P3-14, FR-15.12): where the project's files live. "Use a folder you
// already keep" points the project at an existing folder on a storage location; Tumnis
// then writes only inside its `Tumnis/` subfolder and never renames, moves, overwrites or
// deletes the user's own files. "Move the folder" starts the move job: every file is
// copied and checked before the project switches, and the old copy stays until the user
// removes it (a review item asks).
import { useQuery } from "@tanstack/react-query";
import { type SyntheticEvent, useId, useState } from "react";

import type {
  LocationOut,
  MoveStarted,
  ProjectFolderOut,
} from "../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
import { ConfirmDialog } from "../settings/ConfirmDialog";
import { storageQuery } from "../settings/queries";
import { fieldClass, saveClass } from "./rail/RailSection";

interface Target {
  locationId: string;
  path: string;
}

const EMPTY: Target = { locationId: "", path: "" };

export const WRITE_RULE_HINT =
  "Tumnis writes only inside a Tumnis/ subfolder there. It never renames, moves, overwrites or deletes your own files.";

function problemText(error: Error, fallback: string): string {
  return error instanceof ApiError
    ? (error.problem.detail ?? error.message)
    : fallback;
}

function TargetFields({
  locations,
  target,
  onChange,
}: {
  locations: LocationOut[];
  target: Target;
  onChange: (target: Target) => void;
}) {
  const ids = { location: useId(), path: useId() };
  return (
    <>
      <label htmlFor={ids.location} className="text-sm">
        Location
      </label>
      <select
        id={ids.location}
        className={fieldClass}
        required
        value={target.locationId}
        onChange={(event) => {
          onChange({ ...target, locationId: event.target.value });
        }}
      >
        <option value="" disabled>
          Choose a location
        </option>
        {locations.map((location) => (
          <option key={location.id} value={location.id}>
            {location.name}
          </option>
        ))}
      </select>
      <label htmlFor={ids.path} className="text-sm">
        Folder path on that location
      </label>
      <input
        id={ids.path}
        className={fieldClass}
        required
        maxLength={1024}
        autoComplete="off"
        spellCheck={false}
        placeholder="Clients/Acme"
        value={target.path}
        onChange={(event) => {
          onChange({ ...target, path: event.target.value });
        }}
      />
    </>
  );
}

export function FolderSetup({ projectId }: { projectId: string }) {
  const locations = useQuery(storageQuery());
  const [existing, setExisting] = useState<Target>(EMPTY);
  const [move, setMove] = useState<Target>(EMPTY);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const project = encodeURIComponent(projectId);

  const link = useWrite<Target & { idempotencyKey?: string }, ProjectFolderOut>(
    {
      mutationFn: ({ locationId, path, idempotencyKey }) =>
        apiWrite<ProjectFolderOut>({
          kind: "create",
          method: "POST",
          path: `/knowledge/projects/${project}/existing-folder`,
          body: { location_id: locationId, path: path.trim() },
          idempotencyKey,
        }),
      onSuccess: (folder) => {
        setExisting(EMPTY);
        setMessage(
          `The project now uses ${folder.root_path}. Tumnis writes only in ${folder.root_path}/Tumnis/.`,
        );
      },
      onError: (error) => {
        setMessage(problemText(error, "The folder could not be used."));
      },
    },
  );

  const start = useWrite<Target & { idempotencyKey?: string }, MoveStarted>({
    mutationFn: ({ locationId, path, idempotencyKey }) =>
      apiWrite<MoveStarted>({
        kind: "create",
        method: "POST",
        path: `/knowledge/projects/${project}/folder/move`,
        body: { location_id: locationId, path: path.trim() },
        idempotencyKey,
      }),
    onSuccess: () => {
      setMove(EMPTY);
      setMessage(
        "Moving. Every file is copied and checked first; a review item asks about the old copy when it is done.",
      );
    },
    onError: (error) => {
      setMessage(problemText(error, "The move could not start."));
    },
    onSettled: () => {
      setConfirming(false);
    },
  });

  const rows = locations.data ?? [];
  const useExisting = (event: SyntheticEvent) => {
    event.preventDefault();
    setMessage(null);
    link.mutate(existing);
  };
  const askMove = (event: SyntheticEvent) => {
    event.preventDefault();
    setMessage(null);
    setConfirming(true);
  };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {locations.isError && (
        <p role="alert" className="text-sm text-danger">
          The storage locations could not be loaded.
        </p>
      )}
      <form className="flex flex-col gap-2" onSubmit={useExisting}>
        <h3 className="text-sm font-semibold">Use a folder you already keep</h3>
        <TargetFields
          locations={rows}
          target={existing}
          onChange={setExisting}
        />
        <p className="text-sm text-muted">{WRITE_RULE_HINT}</p>
        <button type="submit" className={saveClass} disabled={link.isPending}>
          Use this folder
        </button>
      </form>
      <form className="flex flex-col gap-2" onSubmit={askMove}>
        <h3 className="text-sm font-semibold">Move the folder</h3>
        <TargetFields locations={rows} target={move} onChange={setMove} />
        <button type="submit" className={saveClass} disabled={start.isPending}>
          Move folder
        </button>
      </form>
      <p role="status" className="text-sm text-muted">
        {message}
      </p>
      {confirming && (
        <ConfirmDialog
          title="Move this project's folder?"
          confirmLabel="Start the move"
          busy={start.isPending}
          onCancel={() => {
            setConfirming(false);
          }}
          onConfirm={() => {
            start.mutate(move);
          }}
        >
          <p className="text-sm">
            Every file is copied to the new folder and its hash checked before
            the project switches. The old copy stays where it is until you
            choose to remove it.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
