// Sign-in through the UI (P0-13): password, then the TOTP code, then the dashboard.
// Runs against the compose.test stack reset to the seed set; the code is computed
// from the seed secret at the real time, as the server's clock is real there.
import { expect, seedUser, test, totp } from "./fixtures";

test(
  "T-P0-13-20 signs in with password and TOTP",
  { tag: ["@SEC-1", "@FR-9.2", "@P0-13"] },
  async ({ seededApp, page, context }) => {
    test.fail();
    const user = seedUser();
    expect(seededApp.baseURL).toBeTruthy();

    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);

    await page.getByLabel("Email").fill(user.email);
    await page.getByLabel("Password").fill(user.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    const code = page.getByLabel("Authentication code");
    await expect(code).toBeVisible();
    const early = await context.cookies();
    expect(
      early.find((c) => c.name === "__Host-tumnis_session"),
    ).toBeUndefined();

    await code.fill(totp(user.totpSecret, new Date()));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("main")).toBeVisible();

    const cookies = await context.cookies();
    expect(
      cookies.find((c) => c.name === "__Host-tumnis_session"),
    ).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "Lax",
      path: "/",
    });
    expect(cookies.find((c) => c.name === "__Host-tumnis_csrf")?.httpOnly).toBe(
      false,
    );
  },
);
