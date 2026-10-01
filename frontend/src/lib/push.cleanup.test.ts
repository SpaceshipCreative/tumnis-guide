// The push subscribe flow when the server refuses the subscription (P4-05, FR-8.3): the
// browser subscription made a moment earlier is dropped again, so Settings does not show
// push as on for a device the server never stored. The subscription's keys are obvious
// placeholders, not key material.
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";

import { server } from "../test/msw/server";
import { enablePush } from "./push";

const SUBSCRIPTION = {
  endpoint: "https://fcm.googleapis.com/fcm/send/placeholder-subscription",
  expirationTime: null,
  keys: { p256dh: "placeholder-p256dh", auth: "placeholder-auth" },
};

afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker");
});

test("[P4-05][FR-8.3] a refused registration unsubscribes the browser again", async () => {
  vi.stubGlobal("Notification", {
    permission: "default",
    requestPermission: () => Promise.resolve<NotificationPermission>("granted"),
  });
  vi.stubGlobal("PushManager", {});
  const unsubscribe = vi.fn(() => Promise.resolve(true));
  const subscribe = vi.fn(() =>
    Promise.resolve({
      endpoint: SUBSCRIPTION.endpoint,
      toJSON: () => SUBSCRIPTION,
      unsubscribe,
    }),
  );
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve({ pushManager: { subscribe } }) },
  });
  server.use(
    http.get("*/v1/push/vapid-public-key", () =>
      HttpResponse.json({ public_key: "AQIDBA" }),
    ),
    http.post("*/v1/push/subscriptions", () =>
      HttpResponse.json(
        {
          type: "about:blank",
          title: "Unprocessable",
          status: 422,
          code: "endpoint_not_allowed",
          detail: "The endpoint is not a known browser push service",
        },
        {
          status: 422,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    ),
  );

  await expect(enablePush()).rejects.toThrow();
  expect(subscribe).toHaveBeenCalledOnce();
  expect(unsubscribe).toHaveBeenCalledOnce();
});
