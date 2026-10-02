// Purge a connection's or a project's content (P3-09): placeholder until the spec test
// turns green.
export interface PurgeDialogProps {
  scope: "connection" | "project";
  targetId: string;
  name: string;
  onDone: () => void;
  onCancel: () => void;
}

export function PurgeDialog(props: PurgeDialogProps) {
  return <div data-scope={props.scope} />;
}
