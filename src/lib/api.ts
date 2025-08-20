import { readSSE, type SSEMessage } from "./sse";
import type { DeltaPayload } from "./types";

const guessApiBase = () => {
  // default dev: frontend 5173 -> backend 8000
  if (typeof window !== "undefined") {
    const u = new URL(window.location.href);
    return `${u.protocol}//${u.hostname}:8000`;
  }
  return "http://localhost:8000";
};

const API_BASE = import.meta.env.VITE_API_BASE || guessApiBase();

export type StreamHandler = (msg: SSEMessage<DeltaPayload>) => void;

export async function streamChat(
  prompt: string,
  session = "default",
  signal?: AbortSignal,
  onMessage?: StreamHandler
): Promise<void> {
  const resp = await fetch(`${API_BASE}/v1/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, session }),
    signal,
  });

  if (!resp.ok || !resp.body) {
    const errText = await resp.text().catch(() => String(resp.status));
    throw new Error(`stream failed: ${resp.status} ${errText}`);
  }

  for await (const msg of readSSE(resp.body)) {
    onMessage?.(msg as SSEMessage<DeltaPayload>);
  }
}

