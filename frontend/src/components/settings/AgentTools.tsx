// Settings > Agents > a profile's tools (P2-10, FR-5.12, SAF-2, SAF-3): the MCP servers the
// profile really has, from its last health check, each matched against its project's
// allowlist, and what its GitHub and Coolify tokens reach. Read-only: the profile is
// changed on the runner, never from here.
import { useQuery } from "@tanstack/react-query";

import type {
  ForeignReach,
  ProfileToolsOut,
  TokenReach,
  ToolServerOut,
} from "../../api/types.gen";
import { profileToolsQuery } from "./queries";
import { ERROR, HINT } from "./styles";

const CHIP =
  "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium";
const GOOD = `${CHIP} bg-accent text-accent-contrast`;
const BAD = `${CHIP} border border-danger text-danger`;
const NEUTRAL = `${CHIP} bg-surface-muted text-muted`;
const ROW = "flex min-w-0 flex-col gap-1 rounded-md border border-border p-2";
const SUBHEADING = "text-sm font-semibold";

const KIND_LABEL: Record<ForeignReach["kind"], string> = {
  github: "GitHub",
  coolify: "Coolify",
};

function allowedChip(allowed: boolean | null): [string, string] {
  if (allowed === true) return ["Allowed", GOOD];
  if (allowed === false) return ["Not allowed", BAD];
  return ["No allowlist", NEUTRAL];
}

function ServerItem({ server }: { server: ToolServerOut }) {
  const [label, chip] = allowedChip(server.allowed);
  const where = [server.transport, server.target].filter(Boolean).join(" · ");
  return (
    <li
      aria-label={server.name}
      className={ROW}
      {...(server.allowed === false ? { "data-drift": "extra" } : {})}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium [overflow-wrap:anywhere]">
          {server.name}
        </span>
        <span className={chip}>{label}</span>
      </div>
      {where && (
        <span className="text-sm text-muted [overflow-wrap:anywhere]">
          {where}
        </span>
      )}
    </li>
  );
}

// Foreign reach with the linking project's name when the check found it; otherwise the
// token's raw list (a check from before project names were recorded).
function foreignOf(
  tools: ProfileToolsOut,
  kind: ForeignReach["kind"],
  reach: TokenReach,
): ForeignReach[] {
  const named = (tools.foreign ?? []).filter((item) => item.kind === kind);
  if (named.length > 0) return named;
  return (reach.foreign_reachable ?? []).map((target) => ({ kind, target }));
}

function TokenItem({
  tools,
  kind,
  reach,
}: {
  tools: ProfileToolsOut;
  kind: ForeignReach["kind"];
  reach: TokenReach;
}) {
  const foreign = foreignOf(tools, kind, reach);
  const own = Object.entries(reach.own_reachable ?? {});
  const [label, chip]: [string, string] = !reach.token_present
    ? ["No token", NEUTRAL]
    : foreign.length > 0
      ? ["Reaches other projects", BAD]
      : own.some(([, ok]) => !ok) || (reach.errors ?? []).length > 0
        ? ["Falls short", BAD]
        : ["Scoped", GOOD];
  return (
    <li aria-label={KIND_LABEL[kind]} className={ROW}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{KIND_LABEL[kind]} token</span>
        <span className={chip}>{label}</span>
      </div>
      {own.map(([target, ok]) => (
        <span key={target} className="text-sm [overflow-wrap:anywhere]">
          {ok ? "Reaches" : "Cannot reach"} its own {target}
        </span>
      ))}
      {foreign.map((item) => (
        <span
          key={item.target}
          className="text-sm text-danger [overflow-wrap:anywhere]"
        >
          Reaches {item.target}
          {item.project_name ? ` (${item.project_name})` : ""}, which belongs to
          another project
        </span>
      ))}
      {(reach.errors ?? []).map((error) => (
        <span
          key={error}
          className="text-sm text-muted [overflow-wrap:anywhere]"
        >
          {error}
        </span>
      ))}
    </li>
  );
}

export function AgentTools({ profileId }: { profileId: string }) {
  const tools = useQuery(profileToolsQuery(profileId));
  if (tools.isPending) return <p className={HINT}>Loading tools…</p>;
  if (tools.isError) {
    return (
      <p role="alert" className={ERROR}>
        The profile's tools could not be loaded.
      </p>
    );
  }
  const data = tools.data;
  if (!data.checked_at) {
    return <p className={HINT}>No health check yet, so no tools to show.</p>;
  }
  const versions = [
    data.hermes_version && `Hermes ${data.hermes_version}`,
    data.profile_version && `profile ${data.profile_version}`,
  ].filter(Boolean);
  const tokens = (
    [
      ["github", data.github],
      ["coolify", data.coolify],
    ] as const
  ).filter((entry): entry is [ForeignReach["kind"], TokenReach] =>
    Boolean(entry[1]),
  );
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {versions.length > 0 && <p className="text-sm">{versions.join(" · ")}</p>}
      <p className={HINT}>
        {data.project_id
          ? `Allowed for this project: ${data.allowlist.join(", ") || "none"}`
          : "The master agent has no project allowlist."}
      </p>

      <p className={SUBHEADING}>MCP servers</p>
      {data.servers.length === 0 ? (
        <p className={HINT}>The profile reported no MCP servers.</p>
      ) : (
        <ul aria-label="MCP servers" className="flex flex-col gap-2">
          {data.servers.map((server) => (
            <ServerItem key={server.name} server={server} />
          ))}
        </ul>
      )}

      {data.missing.length > 0 && (
        <>
          <p className={SUBHEADING}>Allowed but missing</p>
          <ul
            aria-label="Missing servers"
            className="flex flex-wrap gap-2 text-sm"
          >
            {data.missing.map((name) => (
              <li key={name} className={NEUTRAL}>
                {name}
              </li>
            ))}
          </ul>
        </>
      )}

      {tokens.length > 0 && (
        <>
          <p className={SUBHEADING}>Token reach</p>
          <ul aria-label="Token reach" className="flex flex-col gap-2">
            {tokens.map(([kind, reach]) => (
              <TokenItem key={kind} tools={data} kind={kind} reach={reach} />
            ))}
          </ul>
        </>
      )}
      <p className={HINT}>
        Checked {new Date(data.checked_at).toLocaleString()}. Change servers and
        tokens in the profile on its runner.
      </p>
    </div>
  );
}
