// localStorage that never throws (P0-22): private windows, full quotas and disabled
// storage read as empty and drop writes.
export function safeGetItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function safeSetItem(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // storage unavailable: the value lives for this page only
  }
}
