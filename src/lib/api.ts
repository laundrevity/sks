import { readSSE, type SSEMessage } from "./sse";
import type { DeltaPayload } from "./types";

const guessApiBase = () => {
  if (typeof window !== "undefined") {
    const u = new URL(window.location.href);
    return `${u.protocol}//${u.hostname}:8000`;
  }
  return "http://localhost:8000";
};

const API_BASE = import.meta.env.VITE_API_BASE || guessApiBase();

export type StreamHandler = (msg: SSEMessage<DeltaPayload>) => void;

export interface ConversationSummary {
  id: string;
  title?: string | null;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface Conversation {
  id: string;
  title?: string | null;
  created_at: number;
  updated_at: number;
  settings: unknown;
  messages: Array<{
    id: string;
    idx: number;
    role: string | null;
    payload: unknown;
  }>;
}

export async function createConversation(title?: string): Promise<string> {
  const resp = await fetch(`${API_BASE}/v1/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: title ? JSON.stringify({ title }) : "{}",
  });
  if (!resp.ok) {
    throw new Error(`createConversation failed: ${resp.status}`);
  }
  const data = (await resp.json()) as { id: string };
  return data.id;
}

export async function getConversation(id: string): Promise<Conversation | null> {
  const resp = await fetch(`${API_BASE}/v1/conversations/${id}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`getConversation failed: ${resp.status}`);
  return (await resp.json()) as Conversation;
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const resp = await fetch(`${API_BASE}/v1/conversations`);
  if (!resp.ok) throw new Error(`listConversations failed: ${resp.status}`);
  const data = (await resp.json()) as { conversations: ConversationSummary[] };
  return data.conversations;
}

export async function streamChatInConversation(
  convId: string,
  prompt: string,
  signal?: AbortSignal,
  onMessage?: StreamHandler
): Promise<void> {
  const resp = await fetch(`${API_BASE}/v1/conversations/${convId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
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

