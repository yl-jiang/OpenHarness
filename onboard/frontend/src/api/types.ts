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

export type TodoStatus = 'pending' | 'in_progress' | 'done' | 'cancelled';
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
  messages: { role: string; content: string; timestamp: string }[];
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

// ── Project management types ──────────────────────────────────────

export type ProjectStatus = "active" | "completed" | "archived";
export type ProjectRiskStatus = "normal" | "attention" | "at_risk";
export type ProjectCompletionSource = "milestones" | "todos" | "none";
export type ProjectLinkStatus = "active" | "pending" | "rejected";
export type ProjectLinkSource = "user" | "ai_high_confidence" | "ai_candidate" | "migration";
export type ProjectEntityType = "record" | "todo" | "decision" | "highlight" | "experiment";

export interface Project {
  id: string;
  title: string;
  description: string;
  status: ProjectStatus;
  priority: TodoPriority;
  start_date: string;
  target_date: string;
  completed_at: string;
  archived_at: string;
  archive_reason: string;
  tags: string;
  created_at: string;
  updated_at: string;
  stakeholders?: string;
  success_criteria?: string;
  completion_pct: number | null;
  completion_source: ProjectCompletionSource;
  milestone_count: number;
  completed_milestone_count: number;
  linked_record_count: number;
  linked_todo_count: number;
  completed_linked_todo_count: number;
  activity_7d: number;
  activity_30d: number;
  last_activity_at: string;
  risk_status: ProjectRiskStatus;
  open_blocker_count: number;
}

export interface Milestone {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: "pending" | "completed";
  target_date: string;
  completed_at: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectLink {
  id: string;
  project_id: string;
  entity_type: ProjectEntityType;
  entity_id: string;
  source: ProjectLinkSource;
  confidence: string;
  status: ProjectLinkStatus;
  sort_order: number;
  created_at: string;
  updated_at: string;
  entity_title?: string;
}

export type SuggestionType =
  | "link_entity"
  | "create_project"
  | "complete_milestone"
  | "create_milestone"
  | "update_project"
  | "archive_project"
  | "reactivate_project"
  | "merge_projects"
  | "split_project"
  | "ask_followup";

export type SuggestionStatus = "pending" | "accepted" | "rejected" | "snoozed" | "expired";

export interface ProjectSuggestion {
  id: string;
  suggestion_type: SuggestionType;
  project_id: string;
  title: string;
  rationale: string;
  proposed_payload_json: string;
  evidence_json: string;
  confidence: number;
  status: SuggestionStatus;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectBrief {
  at_risk: Project[];
  attention: Project[];
  pending_suggestion_count: number;
  active_project_count: number;
}

export type SignalType = "progress" | "blocker" | "risk" | "decision" | "milestone_evidence" | "stale" | "momentum" | "scope_change";
export type SignalSeverity = "info" | "warning" | "critical";

export interface ProjectSignal {
  id: string;
  project_id: string;
  signal_type: SignalType;
  summary: string;
  severity: SignalSeverity;
  evidence_entity_type: string;
  evidence_entity_id: string;
  created_at: string;
}

export interface ProjectSnapshot {
  id: string;
  project_id: string;
  snapshot_date: string;
  summary: string;
  health: ProjectRiskStatus;
  completion_pct: number | null;
  activity_7d: number;
  open_blocker_count: number;
  next_action: string;
  created_at: string;
}

export interface ProjectCheckin {
  id: string;
  project_id: string;
  channel: string;
  question: string;
  status: "sent" | "answered" | "dismissed";
  response_record_id: string;
  created_at: string;
  responded_at: string;
}

export interface ProjectAnalysis {
  signals: Omit<ProjectSignal, "id" | "created_at">[];
  next_action: string;
  summary: string;
}

export interface ProjectTemplate {
  id: string;
  label: string;
  description: string;
  priority: string;
  tags: string;
  milestones: string[];
}

export interface TimelineEvent {
  date: string;
  type: string;
  title: string;
  detail: string;
}

export interface ProjectAlias {
  id: string;
  project_id: string;
  alias: string;
  source: string;
  created_at: string;
}

export interface GitCommit {
  hash: string;
  author: string;
  date: string;
  subject: string;
}

// ── Memory management types ───────────────────────────────────────

export type MemoryType = "user" | "feedback" | "project" | "reference";
export type MemoryScope = "private" | "project" | "team";

export interface MemoryItem {
  id: string;
  name: string;
  description: string;
  type: MemoryType;
  scope: MemoryScope;
  category: string;
  importance: number;
  source: string;
  created_at: string;
  updated_at: string;
  disabled: boolean;
  tags: string[];
  content: string;
  file_path: string;
}

// ── Health ─────────────────────────────────────────────────

export type HealthCategory = 'medical' | 'symptom' | 'medication' | 'fitness' | 'sleep' | 'nutrition' | 'mental' | 'vital';
export type HealthSeverity = 'mild' | 'moderate' | 'severe' | '';
export type HealthStatus = 'active' | 'resolved' | 'chronic' | 'recurring';

export interface SoloHealthRecord {
  id: string;
  record_id: string;
  date: string;
  subject: string;
  category: HealthCategory | string;
  item: string;
  description: string;
  body_part: string;
  severity: HealthSeverity;
  status: HealthStatus;
  medication_name: string;
  dosage: string;
  frequency: string;
  duration: string;
  exercise_type: string;
  exercise_duration_min: number;
  exercise_intensity: string;
  sleep_hours: number;
  sleep_quality: string;
  mood: string;
  mood_sentiment: string;
  stress_level: string;
  metrics_json: string;
  tags: string;
  source: string;
  linked_memory_id: string;
  created_at: string;
  updated_at: string;
}

export interface HealthOverview {
  total_records: number;
  by_category: Record<string, number>;
  by_subject: Record<string, number>;
  subject_filter: string | null;
  recent_7d_count: number;
  active_medications: number;
  active_symptoms: number;
  avg_sleep_hours_30d: number;
  fitness_count_7d: number;
}

export interface FitnessDay {
  date: string;
  session_count: number;
  total_minutes: number;
  types: string[];
}

export interface SleepDay {
  date: string;
  hours: number;
  quality: string;
}

export interface HealthTimelineItem {
  date: string;
  category: string;
  icon: string;
  subject: string;
  item: string;
  description: string;
  severity: string;
  status: string;
  id: string;
}

// ── Finance ────────────────────────────────────────────────

export type FinanceTxnType = 'expense' | 'income' | 'transfer' | 'invest_gain' | 'invest_loss';

export interface SoloFinanceTransaction {
  id: string;
  record_id: string;
  date: string;
  type: FinanceTxnType;
  category: string;
  amount: number;
  currency: string;
  account: string;
  counterparty: string;
  description: string;
  tags: string;
  source: string;
  metrics_json: string;
  created_at: string;
  updated_at: string;
}

export interface SoloFinanceBudget {
  id: string;
  period: string;
  category: string;
  amount: number;
  currency: string;
  name: string;
  active: number;
  note: string;
  spent?: number;
  utilization?: number;
}

export interface FinanceOverview {
  month_expense: number;
  month_income: number;
  month_net: number;
  invest_net: number;
  prev_expense: number;
  prev_income: number;
  prev_net: number;
  prev_invest_net: number;
  by_category: { category: string; amount: number }[];
}

export interface FinanceDailyItem {
  date: string;
  amount: number;
}

export interface FinanceInvestTrendItem {
  month: string;
  net: number;
}

export interface FinanceTrendItem {
  month: string;
  income: number;
  expense: number;
  net: number;
}

export interface FinanceBudgetWithUtilization extends SoloFinanceBudget {
  spent: number;
  utilization: number;
}

// ---------------------------------------------------------------------------
// Insight Report types
// ---------------------------------------------------------------------------

export type InsightDomain = 'health' | 'finance';

export interface InsightBlindSpot {
  title: string;
  why: string;
  evidence: string;
  severity: 'info' | 'watch' | 'alert';
}

export interface InsightItem {
  icon?: string;
  title: string;
  analysis: string;
  evidence: string[];
  severity: 'info' | 'watch' | 'alert';
  tags?: string[];
}

export interface InsightPattern {
  name: string;
  strength: 'strong' | 'moderate' | 'weak';
  detail: string;
}

export interface InsightRecommendation {
  action: string;
  rationale: string;
  expected_signal: string;
}

export interface InsightMetric {
  label: string;
  value: number;
  unit: string;
  trend?: number[];
  comparison_value?: number;
  comparison_label?: string;
}

export interface InsightPeriodComparison {
  metric: string;
  current: number;
  previous: number;
  delta_pct: number;
  direction: 'up' | 'down' | 'flat';
  unit?: string;
}

export interface InsightReportJSON {
  headline: string;
  narrative: string;
  period_comparison?: InsightPeriodComparison[];
  blind_spots: InsightBlindSpot[];
  insights: InsightItem[];
  patterns?: InsightPattern[];
  recommendations: InsightRecommendation[];
  metrics?: InsightMetric[];
}

