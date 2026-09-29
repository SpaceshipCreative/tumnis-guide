// Settings > API keys (P0-26, FR-9.3): P0-14's key screen, asking before a revoke.
import { ApiKeys } from "./ApiKeys";

export function KeysSection() {
  return <ApiKeys confirmRevoke />;
}
