// The first stop for the keyboard (UX 11): hidden until focused, then Enter moves focus
// to the page's main content.
export const MAIN_ID = "main";

export function SkipLink() {
  return (
    <a
      href={`#${MAIN_ID}`}
      className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2 focus:text-text focus:shadow-lg"
      onClick={(event) => {
        event.preventDefault();
        document.getElementById(MAIN_ID)?.focus();
      }}
    >
      Skip to content
    </a>
  );
}
