/* eslint-disable @typescript-eslint/no-unused-vars -- a P0-25 spec stub */
// Sends one queued item (P0-25, FR-3.10, REL-2). Arrives with its implementation.
import type { QueueItem, SendResult } from "../machines/offlineQueue";

export function sendQueued(_item: QueueItem): Promise<SendResult> {
  return Promise.reject(new Error("lib/queueSender.ts arrives with P0-25"));
}
