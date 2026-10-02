// Push notifications in Settings > Account (P4-05, FR-8.3). Nothing is asked or sent on
// load: the browser's permission prompt appears only when "Enable push" is pressed (UX 6).
// A blocked permission is explained, with no button that would prompt again; on iPhone
// and iPad the step is adding Tumnis to the Home Screen first (iOS 16.4+).
import { useEffect, useState } from "react";

import { enablePush, pushStatus, type PushStatus } from "../../lib/push";
import { BUTTON, ERROR, HINT } from "./styles";

const EXPLAIN: Record<Exclude<PushStatus, "ready">, string> = {
  unsupported: "This browser cannot show push notifications.",
  install:
    "On iPhone and iPad, push works once Tumnis is on the Home Screen: tap Share, then Add to Home Screen, and open Tumnis from there.",
  denied:
    "Notifications are blocked for this site. Allow them in your browser's site settings, then come back here.",
  on: "Push is on for this device. Waiting items and focus messages arrive here, following your focus level.",
};

export function PushSettings() {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    void pushStatus()
      .catch((): PushStatus => "unsupported")
      .then((found) => {
        if (live) setStatus(found);
      });
    return () => {
      live = false;
    };
  }, []);

  async function enable(): Promise<void> {
    setBusy(true);
    setFailed(false);
    try {
      setStatus(await enablePush());
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2" aria-labelledby="push-title">
      <h3 id="push-title" className="font-medium">
        Push notifications
      </h3>
      {status === "ready" && (
        <>
          <p className={HINT}>
            Get a notification on this device when something waits on you. Your
            browser asks for permission next.
          </p>
          <button
            type="button"
            className={`${BUTTON} self-start`}
            disabled={busy}
            onClick={() => void enable()}
          >
            Enable push
          </button>
        </>
      )}
      {status !== null && status !== "ready" && (
        <p className={status === "on" ? undefined : HINT}>{EXPLAIN[status]}</p>
      )}
      {failed && (
        <p role="alert" className={ERROR}>
          Push could not be turned on. Try again.
        </p>
      )}
    </div>
  );
}
