// Result links (P0-18) and citations (P2-17, FR-15.4): an agent cites a knowledge
// document as `tumnis://doc/<id>#page=<n>`. The browser cannot open that scheme, so the
// link goes to the document's file, `/v1/files/<id>#page=<n>`; a PDF is served inline
// there and its viewer opens at that page (RFC 8118; Scott decision 47). Web links stay
// as they are; anything else (an agent's text) never becomes a link.
import { apiUrl } from "../../lib/fetch";

const DOC =
  /^tumnis:\/\/doc\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:#page=([1-9][0-9]{0,5}))?$/;

/** Where a result link may point in the browser; null: not a link. */
export function linkHref(url: string): string | null {
  const doc = DOC.exec(url);
  if (doc) {
    const [, id = "", page] = doc;
    return `${apiUrl(`/files/${id}`)}${page ? `#page=${page}` : ""}`;
  }
  return /^https?:\/\//.test(url) ? url : null;
}

export function Citation({
  url,
  label,
  className,
}: {
  url: string;
  label: string | null | undefined;
  className?: string;
}) {
  const href = linkHref(url);
  if (href === null) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
    >
      {label ?? url}
    </a>
  );
}
