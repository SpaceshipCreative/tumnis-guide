// The app shell (DS-01, UX 7, UX 11): the laptop sidebar collapses to icons from the
// keyboard, the header's search, review, help and account controls work from the keyboard,
// and on the phone the navigation drawer keeps focus inside until Escape.
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, expect, test } from "vitest";

import type { AccountOut } from "../../api/types.gen";
import { reviewCount } from "../../test/msw/dashboard";
import { server } from "../../test/msw/server";
import { renderRoute } from "../../test/render";
import { SIDEBAR_KEY, uiStore } from "../../stores/uiStore";

const ACCOUNT: AccountOut = {
  email: "owner@example.com",
  second_factor: "totp",
  totp_confirmed_at: "2026-03-01T09:00:00Z",
  user_id: "0192b3c4-0000-7000-8000-000000000001",
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  uiStore.trigger.setTheme({ theme: "system" });
  uiStore.trigger.setSidebarCollapsed({ collapsed: false });
  uiStore.trigger.setNavOpen({ open: false });
  uiStore.trigger.toggleSearch({ open: false });
  server.use(http.get("*/v1/auth/account", () => HttpResponse.json(ACCOUNT)));
});

test("[DS-01][UX 11] T-DS-01-03 the sidebar collapses to icons from the keyboard", async () => {
  const { user } = await renderRoute("/", { viewport: "laptop" });
  const collapse = screen.getByRole("button", { name: "Collapse sidebar" });
  const sidebar = collapse.closest("nav");
  expect(sidebar).not.toBeNull();
  if (!sidebar) return;
  expect(sidebar).toHaveAccessibleName("Primary");

  collapse.focus();
  await user.keyboard("{Enter}");
  const expand = within(sidebar).getByRole("button", {
    name: "Expand sidebar",
  });
  expect(expand).toHaveFocus();
  expect(localStorage.getItem(SIDEBAR_KEY)).toBe("true");
  // Icons only, but every link keeps its name for the keyboard and screen readers.
  for (const name of ["Dashboard", "Projects", "Tasks", "Review", "Settings"]) {
    expect(within(sidebar).getByRole("link", { name })).toBeInTheDocument();
  }

  await user.keyboard(" ");
  expect(
    within(sidebar).getByRole("button", { name: "Collapse sidebar" }),
  ).toBeInTheDocument();
  expect(localStorage.getItem(SIDEBAR_KEY)).toBe("false");
});

test("[DS-01][UX 7] T-DS-01-04 the account menu works from the keyboard", async () => {
  const { user } = await renderRoute("/", { viewport: "laptop" });
  const account = screen.getByRole("button", { name: "Account" });
  expect(account).toHaveAttribute("aria-haspopup", "menu");
  expect(account).toHaveAttribute("aria-expanded", "false");

  // Enter opens the menu on its first item; the arrows move and wrap.
  account.focus();
  await user.keyboard("{Enter}");
  const menu = screen.getByRole("menu", { name: "Account" });
  expect(account).toHaveAttribute("aria-expanded", "true");
  const settings = within(menu).getByRole("menuitem", { name: "Settings" });
  expect(settings).toHaveFocus();
  expect(await within(menu).findByText(ACCOUNT.email)).toBeInTheDocument();
  await user.keyboard("{ArrowUp}");
  expect(
    within(menu).getByRole("menuitem", { name: "Sign out" }),
  ).toHaveFocus();
  await user.keyboard("{ArrowDown}{ArrowDown}");
  const light = within(menu).getByRole("menuitemradio", { name: "Light" });
  expect(light).toHaveFocus();
  expect(
    within(menu).getByRole("menuitemradio", { name: "System" }),
  ).toHaveAttribute("aria-checked", "true");

  // Choosing a theme shows it at once, closes the menu and returns focus.
  await user.keyboard("{ArrowDown}{Enter}");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(account).toHaveFocus();

  // ArrowDown opens it too; Escape closes it and focus goes back to the button.
  await user.keyboard("{ArrowDown}");
  const reopened = screen.getByRole("menu", { name: "Account" });
  expect(
    within(reopened).getByRole("menuitemradio", { name: "Dark" }),
  ).toHaveAttribute("aria-checked", "true");
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(account).toHaveFocus();
});

test("[DS-01][UX 7] T-DS-01-05 help lists the shortcuts and closes on Escape", async () => {
  const { user } = await renderRoute("/", { viewport: "laptop" });
  const help = screen.getByRole("button", { name: "Help" });
  expect(help).toHaveAttribute("aria-expanded", "false");

  help.focus();
  await user.keyboard("{Enter}");
  expect(help).toHaveAttribute("aria-expanded", "true");
  const panel = screen.getByRole("region", { name: "Keyboard shortcuts" });
  expect(within(panel).getByText("Search")).toBeInTheDocument();
  expect(within(panel).getByText("Quick add")).toBeInTheDocument();

  await user.keyboard("{Escape}");
  expect(
    screen.queryByRole("region", { name: "Keyboard shortcuts" }),
  ).toBeNull();
  expect(help).toHaveFocus();
});

test("[DS-01][FR-1.4] T-DS-01-06 header search opens the palette and review shows the count", async () => {
  server.use(reviewCount(3));
  const { user, router } = await renderRoute("/", { viewport: "laptop" });

  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(uiStore.getSnapshot().context.searchOpen).toBe(true);
  uiStore.trigger.toggleSearch({ open: false });

  const review = await screen.findByRole("link", {
    name: "Review: 3 waiting",
  });
  await user.click(review);
  await waitFor(() => {
    expect(router.state.location.pathname).toBe("/review");
  });
});

test("[DS-01][UX 11] T-DS-01-07 the phone drawer traps focus and closes on Escape", async () => {
  const { user, router } = await renderRoute("/", { viewport: "phone" });
  const open = screen.getByRole("button", { name: "Open menu" });
  expect(open).toHaveAttribute("aria-expanded", "false");

  await user.click(open);
  const drawer = screen.getByRole("dialog", { name: "Menu" });
  expect(drawer).toHaveAttribute("aria-modal", "true");
  expect(open).toHaveAttribute("aria-expanded", "true");
  const close = within(drawer).getByRole("button", { name: "Close menu" });
  expect(close).toHaveFocus();
  // The page behind is inert while the drawer is open.
  expect(document.getElementById("main")?.closest("[inert]")).not.toBeNull();

  // Tab and Shift+Tab wrap inside the drawer.
  const links = within(drawer).getAllByRole("link");
  await user.tab({ shift: true });
  expect(links.at(-1)).toHaveFocus();
  await user.tab();
  expect(close).toHaveFocus();
  for (let i = 0; i < links.length + 2; i += 1) {
    await user.tab();
    expect(drawer).toContainElement(document.activeElement as HTMLElement);
  }

  // Escape closes it and gives focus back to the menu button.
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
  expect(open).toHaveFocus();
  expect(document.getElementById("main")?.closest("[inert]")).toBeNull();

  // A link navigates and closes the drawer.
  await user.click(open);
  await user.click(
    within(screen.getByRole("dialog", { name: "Menu" })).getByRole("link", {
      name: "Projects",
    }),
  );
  await waitFor(() => {
    expect(router.state.location.pathname).toBe("/projects");
  });
  expect(screen.queryByRole("dialog", { name: "Menu" })).toBeNull();
});

test("[DS-01][UX 11] T-DS-01-16 everything behind the phone drawer is inert, toasts and Quick add included", async () => {
  const { user } = await renderRoute("/", { viewport: "phone" });
  await user.click(screen.getByRole("button", { name: "Open menu" }));
  const drawer = screen.getByRole("dialog", { name: "Menu" });
  expect(drawer.closest("[inert]")).toBeNull();

  const quickAdd = screen.getByRole("button", {
    name: "Quick add",
    hidden: true,
  });
  expect(quickAdd.closest("[inert]")).not.toBeNull();
  for (const status of screen.queryAllByRole("status", { hidden: true })) {
    expect(status.closest("[inert]")).not.toBeNull();
  }
});
