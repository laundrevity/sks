export type DeltaKind =
  | "response.status"
  | "response.usage"
  | "item.started"
  | "item.completed"
  | "text"
  | "reasoning"
  | "function.arguments"
  | "custom.input"
  | "unknown"
  | "error";

export interface DeltaPayload {
  kind: DeltaKind;
  output_index: number | null;
  item_id: string | null;
  content_index: number | null;
  summary_index: number | null;
  text: string | null;
  name: string | null;
  call_id: string | null;
  status: string | null;
  meta: Record<string, unknown>;
}

// ---- UI chat types (centralized so +page.svelte can import) ----
export type Role = "user" | "assistant";

export type TextPart = { type: "text"; text: string };
export type ReasoningPart = { type: "reasoning"; text: string };
export type FuncPart = { type: "function"; name: string; call_id: string; text: string };
export type CustomPart = { type: "custom"; name: string; call_id: string; text: string };

export type Part = TextPart | ReasoningPart | FuncPart | CustomPart;

export type Msg = { id: string; role: Role; parts: Part[] };

