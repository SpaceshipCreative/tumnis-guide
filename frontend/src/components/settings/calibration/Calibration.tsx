// Settings > Calibration (P3-08, FR-11.5): one card per decision point with its threshold,
// a recheck badge when the pinned model changed, and, once 100 of its outcomes are known,
// how often its answers were right (until then, how many more are needed). What each
// threshold would have done opens on request, and the threshold is changed here, by a
// person, with a reason. Nothing is suggested: the page shows the evidence and the human
// decides (design decision 8).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type SyntheticEvent } from "react";

import { decisionsGetCalibrationQueryKey } from "../../../api/@tanstack/react-query.gen";
import type {
  CalibrationPoint,
  Evaluation,
  Metrics,
  SweepRow,
  Threshold,
  ThresholdOut,
} from "../../../api/types.gen";
import { ApiError, apiWrite, useWrite } from "../../../lib/fetch";
import { Card } from "../../common/Card";
import {
  badge,
  BUTTON_QUIET,
  TABLE,
  TABLE_BODY,
  TABLE_CELL,
  TABLE_HEAD,
  TABLE_HEADER_CELL,
} from "../../common/ui";
import { calibrationQuery } from "../queries";
import {
  BUTTON,
  ERROR,
  HEADING,
  HINT,
  INPUT,
  LABEL,
  SECONDARY,
  SECTION,
} from "../styles";

const POINT_LABELS: Record<string, string> = {
  quick_add_label: "Quick-add label",
  project_match: "Project match",
  actionability: "Actionability",
  duplicate: "Duplicate",
  approval_need: "Approval need",
  blocking_impact: "Blocking impact",
  focus_on_task: "On task during focus",
  nudge_warranted: "Nudge warranted",
  estimate_plausibility: "Estimate plausibility",
};

const PROVIDER_LABELS: Record<string, string> = {
  jev: "Jev",
  vllm: "Local fallback",
  fake: "Test provider",
};

function pointLabel(point: string): string {
  return POINT_LABELS[point] ?? point;
}

function percent(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "–"
    : `${String(Math.round(value * 100))}%`;
}

/** The threshold in words: one bar, or the yes and no bands of a yes-or-no point. */
function describe(threshold: Threshold): string {
  if (
    threshold.min_confidence !== null &&
    threshold.min_confidence !== undefined
  ) {
    return `confidence ${String(threshold.min_confidence)} or more`;
  }
  const bands: string[] = [];
  if (threshold.t_yes !== null && threshold.t_yes !== undefined) {
    bands.push(`yes at ${String(threshold.t_yes)} or more`);
  }
  if (threshold.t_no !== null && threshold.t_no !== undefined) {
    bands.push(`no at ${String(threshold.t_no)} or less`);
  }
  return bands.length > 0 ? bands.join(", ") : "never applied on its own";
}

export function CalibrationSection() {
  const loaded = useQuery(calibrationQuery());
  return (
    <section aria-labelledby="calibration-title" className={SECTION}>
      <h2 id="calibration-title" className={HEADING}>
        Calibration
      </h2>
      <p className={HINT}>
        How often each kind of decision was right, once{" "}
        {String(loaded.data?.min_labeled ?? 100)} of its outcomes are known.
        Below its threshold an answer goes to your review queue. You set the
        thresholds here; Tumnis never changes them.
      </p>
      {loaded.data ? (
        <>
          <p className={HINT}>Model: {loaded.data.model_version}</p>
          {loaded.data.points.map((point) => (
            <PointCard key={point.decision_point} point={point} />
          ))}
        </>
      ) : loaded.isError ? (
        <p role="alert" className={ERROR}>
          Calibration could not be loaded.
        </p>
      ) : (
        <p className={HINT}>Loading…</p>
      )}
    </section>
  );
}

function PointCard({ point }: { point: CalibrationPoint }) {
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  return (
    <Card
      title={pointLabel(point.decision_point)}
      action={
        point.needs_recheck ? (
          <span className={badge("warning")}>Recheck: model changed</span>
        ) : undefined
      }
    >
      <p className="text-sm">
        Threshold: {describe(point.threshold)}{" "}
        <span className="text-muted">
          ({point.source === "user" ? "set by you" : "default"})
        </span>
      </p>
      {point.evaluations.length === 0 ? (
        <p className={HINT}>No decisions with a known outcome yet.</p>
      ) : (
        point.evaluations.map((evaluation) => (
          <EvaluationView
            key={`${evaluation.provider}:${evaluation.model_version}`}
            evaluation={evaluation}
          />
        ))
      )}
      {editing ? (
        <ThresholdEditor
          point={point}
          onDone={(message) => {
            setEditing(false);
            setNotice(message);
          }}
        />
      ) : (
        <div>
          <button
            type="button"
            className={SECONDARY}
            onClick={() => {
              setNotice(null);
              setEditing(true);
            }}
          >
            Change threshold
          </button>
        </div>
      )}
      <p role="status" className={HINT}>
        {notice}
      </p>
    </Card>
  );
}

function EvaluationView({ evaluation }: { evaluation: Evaluation }) {
  const [open, setOpen] = useState(false);
  const sweepId = useId();
  const source = `${PROVIDER_LABELS[evaluation.provider] ?? evaluation.provider} (${evaluation.model_version})`;
  const { metrics } = evaluation;
  if (metrics === null) {
    return (
      <p className={HINT}>
        {source}: {String(evaluation.labeled)} known outcomes,{" "}
        {String(evaluation.needed)} more needed before accuracy shows.
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <caption className="sr-only">
            {source} at the current threshold
          </caption>
          <thead className={TABLE_HEAD}>
            <tr>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Answers
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Applied automatically
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Right when applied
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Sent to review
              </th>
              <th scope="col" className={TABLE_HEADER_CELL}>
                Right overall
              </th>
            </tr>
          </thead>
          <tbody className={TABLE_BODY}>
            <MetricsRow label={source} metrics={metrics} />
          </tbody>
        </table>
      </div>
      <p className={HINT}>
        {String(evaluation.labeled)} known outcomes. Right when you decided
        yourself: {percent(evaluation.explicit_metrics?.overall_accuracy)}
        {evaluation.explicit_metrics === null
          ? " (not enough of your own decisions yet)"
          : ""}
        .
      </p>
      <div>
        <button
          type="button"
          className={BUTTON_QUIET}
          aria-expanded={open}
          aria-controls={sweepId}
          onClick={() => {
            setOpen(!open);
          }}
        >
          {open ? "Hide" : "Show"} what each threshold would do
        </button>
      </div>
      {open && <Sweep id={sweepId} rows={evaluation.sweep} />}
    </div>
  );
}

function MetricsRow({ label, metrics }: { label: string; metrics: Metrics }) {
  return (
    <tr>
      <th scope="row" className={`${TABLE_CELL} font-medium`}>
        {label}
      </th>
      <td className={TABLE_CELL}>{percent(metrics.auto_rate)}</td>
      <td className={TABLE_CELL}>{percent(metrics.auto_precision)}</td>
      <td className={TABLE_CELL}>{percent(metrics.review_rate)}</td>
      <td className={TABLE_CELL}>{percent(metrics.overall_accuracy)}</td>
    </tr>
  );
}

function Sweep({ id, rows }: { id: string; rows: SweepRow[] }) {
  return (
    <div id={id} className="overflow-x-auto">
      <table className={TABLE}>
        <caption className="sr-only">What each threshold would do</caption>
        <thead className={TABLE_HEAD}>
          <tr>
            <th scope="col" className={TABLE_HEADER_CELL}>
              Threshold
            </th>
            <th scope="col" className={TABLE_HEADER_CELL}>
              Applied automatically
            </th>
            <th scope="col" className={TABLE_HEADER_CELL}>
              Right when applied
            </th>
            <th scope="col" className={TABLE_HEADER_CELL}>
              Sent to review
            </th>
          </tr>
        </thead>
        <tbody className={TABLE_BODY}>
          {rows.map((row) => (
            <tr key={row.threshold}>
              <th scope="row" className={`${TABLE_CELL} font-medium`}>
                {String(row.threshold)}
              </th>
              <td className={TABLE_CELL}>{percent(row.auto_rate)}</td>
              <td className={TABLE_CELL}>{percent(row.auto_precision)}</td>
              <td className={TABLE_CELL}>{percent(row.review_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Field = "min_confidence" | "t_yes" | "t_no";

const FIELD_LABELS: Record<Field, string> = {
  min_confidence: "Minimum confidence",
  t_yes: "Yes at or above",
  t_no: "No at or below",
};

/** The fields a point's threshold has: one bar, or the yes and no bands (approval need
 * has no yes band: a yes always asks you). */
function fieldsOf(point: CalibrationPoint): Field[] {
  if (point.primitive !== "noul") return ["min_confidence"];
  return point.decision_point === "approval_need"
    ? ["t_no"]
    : ["t_yes", "t_no"];
}

function ThresholdEditor({
  point,
  onDone,
}: {
  point: CalibrationPoint;
  onDone: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const baseId = useId();
  const fields = fieldsOf(point);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map((f) => [f, String(point.threshold[f] ?? "")]),
    ),
  );
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState<string | null>(null);

  const save = useWrite<
    { body: Record<string, unknown>; idempotencyKey?: string },
    ThresholdOut
  >({
    mutationFn: ({ body, idempotencyKey }) =>
      apiWrite({
        kind: "create",
        method: "PUT",
        path: `/decisions/thresholds/${point.decision_point}`,
        body,
        idempotencyKey,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: decisionsGetCalibrationQueryKey(),
      });
      onDone("Saved.");
    },
    onError: (error) => {
      setFailure(
        error instanceof ApiError
          ? (error.problem.detail ?? error.message)
          : "The threshold could not be saved.",
      );
    },
  });

  function submit(event: SyntheticEvent) {
    event.preventDefault();
    setFailure(null);
    const threshold: Record<string, number> = {};
    for (const field of fields) {
      const raw = values[field]?.trim() ?? "";
      if (raw === "") continue;
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0 || value > 1) {
        setFailure(`${FIELD_LABELS[field]} must be between 0 and 1.`);
        return;
      }
      threshold[field] = value;
    }
    if (Object.keys(threshold).length === 0) {
      setFailure("Enter a threshold between 0 and 1.");
      return;
    }
    if (reason.trim() === "") {
      setFailure("Say why you are changing it: a reason is required.");
      return;
    }
    save.mutate({ body: { threshold, reason: reason.trim() } });
  }

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => {
          const id = `${baseId}-${field}`;
          return (
            <div key={field} className={LABEL}>
              <label htmlFor={id}>{FIELD_LABELS[field]}</label>
              <input
                id={id}
                type="number"
                inputMode="decimal"
                min={0}
                max={1}
                step={0.01}
                className={INPUT}
                value={values[field] ?? ""}
                onChange={(event) => {
                  setValues({ ...values, [field]: event.target.value });
                }}
              />
            </div>
          );
        })}
      </div>
      <div className={LABEL}>
        <label htmlFor={`${baseId}-reason`}>Reason</label>
        <textarea
          id={`${baseId}-reason`}
          rows={2}
          maxLength={500}
          className={INPUT}
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
          }}
        />
      </div>
      {failure !== null && (
        <p role="alert" className={ERROR}>
          {failure}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={BUTTON} disabled={save.isPending}>
          Save
        </button>
        <button
          type="button"
          className={SECONDARY}
          onClick={() => {
            onDone(null);
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
