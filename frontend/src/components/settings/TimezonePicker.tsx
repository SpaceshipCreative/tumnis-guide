// The workspace timezone (P0-26, REL-6): a native select over the IANA zones (the phone's
// own picker), and, when this device's zone differs, one tap to use it. A saved zone the
// list lacks (an alias such as US/Eastern) stays selectable, marked as such.
import type { ChangeEvent, FocusEvent, Ref } from "react";

import { INPUT, LABEL, SECONDARY } from "./styles";

export function TimezonePicker({
  id,
  name,
  value,
  zones,
  device,
  ref,
  describedBy,
  invalid = false,
  onChange,
  onBlur,
  onUseDevice,
}: {
  id: string;
  name: string;
  value: string;
  zones: readonly string[];
  device: string;
  ref?: Ref<HTMLSelectElement>;
  describedBy?: string | undefined;
  invalid?: boolean;
  onChange: (event: ChangeEvent<HTMLSelectElement>) => void;
  onBlur: (event: FocusEvent<HTMLSelectElement>) => void;
  onUseDevice: (zone: string) => void;
}) {
  const listed = zones.includes(value);
  const offerDevice =
    device !== "" && device !== value && zones.includes(device);
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className={LABEL}>
        Timezone
      </label>
      <select
        id={id}
        name={name}
        ref={ref}
        value={value}
        className={INPUT}
        aria-invalid={invalid}
        aria-describedby={describedBy}
        onChange={onChange}
        onBlur={onBlur}
      >
        {!listed && value !== "" && (
          <option value={value}>
            {value} (saved; not in this device's list)
          </option>
        )}
        {zones.map((zone) => (
          <option key={zone} value={zone}>
            {zone}
          </option>
        ))}
      </select>
      {offerDevice && (
        <div>
          <button
            type="button"
            className={SECONDARY}
            onClick={() => {
              onUseDevice(device);
            }}
          >
            Use this device's zone ({device})
          </button>
        </div>
      )}
    </div>
  );
}
