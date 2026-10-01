// Speech engines for voice mode (P4-03, FR-10.8, FR-11.7). An engine speaks one message and
// settles when it has finished (or failed); aborting `signal` stops it and settles at once.
// The voiceMode machine, not the engine, guarantees one message at a time.

/** One focus message to speak: its id (the focus event's), the exact in-app text, and the
 * server clip to play when the server engine made one. */
export interface VoiceMessage {
  id: string;
  text: string;
  clipUrl?: string;
}

export interface SpeechEngine {
  speak: (message: VoiceMessage, signal?: AbortSignal) => Promise<void>;
}
