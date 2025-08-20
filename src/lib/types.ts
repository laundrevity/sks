export type DeltaKind =
  | "response.status"
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

