// A menu button (DS-01, UX 7): the WAI-ARIA menu button pattern. Enter, Space or
// ArrowDown opens the menu on its first item and ArrowUp on its last; the arrows move and
// wrap, Home and End jump, Escape closes it and gives focus back to the button, Tab closes
// it and moves on, and a click outside closes it. Choosing an item runs it and closes.
import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { Icon } from "./icons";

export interface MenuItem {
  readonly label: string;
  readonly onSelect: () => void;
  /** A choice in a radio group: shows a check and `aria-checked`. */
  readonly checked?: boolean;
}

export interface MenuSection {
  /** Names a group of choices (a radio group); plain sections have none. */
  readonly label?: string;
  readonly items: readonly MenuItem[];
}

const ITEM =
  "flex min-h-11 w-full items-center gap-2 rounded-md px-3 text-left text-sm text-text hover:bg-surface-muted focus-visible:bg-surface-muted md:min-h-9";

function items(menu: HTMLElement | null): HTMLElement[] {
  return Array.from(
    menu?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [],
  );
}

export function MenuButton({
  label,
  button,
  sections,
  header,
  buttonClassName = "",
  onOpenChange,
}: {
  /** The button's and the menu's accessible name. */
  label: string;
  /** What the button shows (an icon or an avatar). */
  button: ReactNode;
  sections: readonly MenuSection[];
  /** Read-only content above the items, e.g. who is signed in. */
  header?: ReactNode;
  buttonClassName?: string;
  /** Told when the menu opens or closes (e.g. to load what it shows). */
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState<"first" | "last">("first");
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open) return;
    const list = items(menuRef.current);
    (start === "first" ? list[0] : list.at(-1))?.focus();
  }, [open, start]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target)) return;
      if (buttonRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open]);

  const show = (from: "first" | "last") => {
    setStart(from);
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };

  const onButtonKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      show(event.key === "ArrowDown" ? "first" : "last");
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      close();
    }
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const list = items(menuRef.current);
    const at = list.findIndex((item) => item === document.activeElement);
    const focus = (index: number) => {
      event.preventDefault();
      list[(index + list.length) % list.length]?.focus();
    };
    switch (event.key) {
      case "ArrowDown":
        focus(at + 1);
        break;
      case "ArrowUp":
        focus(at - 1);
        break;
      case "Home":
        focus(0);
        break;
      case "End":
        focus(list.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        close();
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => {
          if (open) setOpen(false);
          else show("first");
        }}
        onKeyDown={onButtonKeyDown}
        className={buttonClassName}
      >
        {button}
      </button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 z-50 mt-2 w-60 rounded-lg border border-border bg-surface p-1.5 shadow-lg"
        >
          {header}
          {sections.map((section, index) => {
            const body = section.items.map((item) => (
              <button
                key={item.label}
                type="button"
                role={item.checked === undefined ? "menuitem" : "menuitemradio"}
                aria-checked={item.checked}
                tabIndex={-1}
                onClick={() => {
                  item.onSelect();
                  close();
                }}
                className={ITEM}
              >
                {item.checked !== undefined && (
                  <span className="size-4">
                    {item.checked && (
                      <Icon name="check" className="size-4 text-accent" />
                    )}
                  </span>
                )}
                {item.label}
              </button>
            ));
            return (
              <div key={section.label ?? `section-${String(index)}`}>
                {index > 0 && (
                  <div role="separator" className="my-1.5 h-px bg-border" />
                )}
                {section.label === undefined ? (
                  body
                ) : (
                  <div role="group" aria-label={section.label}>
                    <p
                      aria-hidden="true"
                      className="px-3 pt-1 pb-1 text-xs font-semibold text-muted uppercase"
                    >
                      {section.label}
                    </p>
                    {body}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
