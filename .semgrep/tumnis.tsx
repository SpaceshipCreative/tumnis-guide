// Test cases for .semgrep/tumnis.yml (run: semgrep --test .semgrep). Not part of the app.
export function Unsafe({ html }: { html: string }) {
  // ruleid: tumnis-inner-html
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Safe({ html }: { html: string }) {
  // ok: tumnis-inner-html
  return <SafeHtml html={html} />;
}

declare function SafeHtml(props: { html: string }): JSX.Element;
