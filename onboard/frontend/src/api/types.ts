export type AppName = 'solo' | 'wolo';
export type JsonObject = { [key: string]: unknown };

export interface StoredAttachment {
  filename: string;
  content_type: string;
  size: number;
  stored_path: string;
}

export interface Entry {
  id: string;
  content: string;
  created_at: string;
  channel: string;
  sender_id: string;
  chat_id: string;
  message_id: string | null;
  metadata: JsonObject | null;
  attachments: StoredAttachment[];
}

export interface LogRecord {
  id: string;
  entry_id: string;
  date: string;
  raw_content: string;
  corrected_content: string;
  summary: string;
  tags: string;
  emotion: string;
  weekday: string;
  events: string;
  period: string;
  season: string;
  is_weekend: boolean;
  content_length: number;
  emotion_reason: string;
  related_people: string;
  related_places: string;
  source: string;
  created_at: string;
  attachments: StoredAttachment[];
}

export type TodoStatus = 'pending' | 'in_progress' | 'done';
export type TodoPriority = 'high' | 'medium' | 'low';

export interface Todo {
  id: string;
  record_id: string;
  title: string;
  category?: string;
  project?: string;
  priority: TodoPriority;
  due_date: string;
  status: TodoStatus;
  source: string;
  created_at: string;
  completed_at: string;
}

export type ReportType = 'weekly' | 'monthly' | 'yearly';

export interface Report {
  id: string;
  report_type: string;
  content: string;
  created_at: string;
  period_start: string;
  period_end: string;
  metadata?: Record<string, unknown> | null;
}

export interface FeedDigestMeta {
  preset: string;
  domain: string;
  date: string;
  is_empty: boolean;
  selected_count: number;
  source_stats: Array<{ source: string; fetched: number; selected: number; failed: boolean }>;
  warnings: string[];
}

export interface FeedDigest {
  id: string;
  report_type: 'feed_digest';
  content: string;
  created_at: string;
  period_start: string;
  period_end: string;
  metadata: FeedDigestMeta | null;
}

export interface Decision {
  id: string;
  record_id: string;
  title: string;
  rationale: string;
  impact: string;
  project: string;
  source: string;
  created_at: string;
}

export type HighlightKind = 'important' | 'blocker' | 'risk' | 'prompt' | 'tool';

export interface Highlight {
  id: string;
  record_id: string;
  kind: HighlightKind;
  title: string;
  content: string;
  project: string;
  tags: string;
  source: string;
  created_at: string;
}

export interface CountPoint {
  date: string;
  count: number;
}

export interface EmotionPoint {
  emotion: string;
  count: number;
}

export interface TagPoint {
  tag: string;
  count: number;
}

export interface ModelUsagePoint {
  model: string;
  count: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ModelTokenDailyPoint {
  date: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface ModelCallDailyPoint {
  date: string;
  model: string;
  count: number;
}

export interface AppStats {
  total_entries: number;
  total_records: number;
  pending_entries: number;
  total_todos: number;
  pending_todos: number;
  this_week_records: number;
  llm_total_calls: number;
  llm_total_input_tokens: number;
  llm_total_output_tokens: number;
  llm_usage_models: ModelUsagePoint[];
  llm_monthly_start_date: string;
  llm_monthly_end_date: string;
  llm_monthly_tokens: ModelTokenDailyPoint[];
  llm_monthly_model_calls: ModelCallDailyPoint[];
  llm_daily_focus_date: string;
  llm_daily_total_calls: number;
  llm_daily_input_tokens: number;
  llm_daily_output_tokens: number;
  llm_daily_usage_models: ModelUsagePoint[];
  vision_total_calls: number;
  vision_model: string;
  current_model: string;
  total_decisions?: number;
  total_highlights?: number;
  open_blockers?: number;
  emotion_distribution: EmotionPoint[];
  daily_counts: CountPoint[];
  top_tags: TagPoint[];
}

export type GatewayStatusCode = 'running' | 'stopped' | 'unknown';

export interface GatewayStatus {
  status: GatewayStatusCode;
  pid: number | null;
  uptime_seconds: number | null;
  port: number | null;
  provider_profile?: string;
  enabled_channels?: string[];
  last_error?: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface SearchResult {
  records: LogRecord[];
  total: number;
  query: string;
}

export interface ChatSession {
  session_key: string;
  session_id: string | null;
  message_count: number;
  updated_at: string;
  preview: string;
}

export interface ChatSessionDetail {
  session_key: string;
  session_id: string | null;
  messages: { role: string; content: string }[];
}

export type WsClientMessage = { type: 'message'; content: string; media?: string[] } | { type: 'cancel' };

export type WsServerMessage =
  | { type: 'delta'; content: string }
  | { type: 'reasoning'; content: string }
  | { type: 'tool_start'; tool: string; args: JsonObject }
  | { type: 'tool_complete'; tool: string; result: string }
  | { type: 'progress'; content: string }
  | { type: 'media'; paths: string[] }
  | { type: 'complete'; content: string }
  | { type: 'session_key'; session_key: string }
  | { type: 'error'; message: string };
