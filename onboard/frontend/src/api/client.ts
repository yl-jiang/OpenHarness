import type {
  AppName,
  AppStats,
  ChatSession,
  ChatSessionDetail,
  Decision,
  Entry,
  FeedDigest,
  GatewayStatus,
  Highlight,
  LogRecord,
  PaginatedResponse,
  Report,
  SearchResult,
  Todo,
  Project,
  Milestone,
  ProjectLink,
} from './types';

type QueryValue = string | number | boolean | null | undefined;

function query(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (response.status === 401) {
    // Session expired or not authenticated — redirect to gate
    window.location.href = '/_gate';
    throw new Error('Authentication required');
  }
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  stats: (app: AppName) => request<AppStats>(`/api/${app}/stats`),
  entries: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<PaginatedResponse<Entry>>(`/api/${app}/entries${query(params)}`),
  records: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<PaginatedResponse<LogRecord>>(`/api/${app}/records${query(params)}`),
  record: (app: AppName, id: string) => request<LogRecord>(`/api/${app}/records/${id}`),
  todos: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<Todo[]>(`/api/${app}/todos${query(params)}`),
  markTodoDone: (app: AppName, id: string) =>
    request<{ ok: boolean }>(`/api/${app}/todos/${id}/done`, { method: 'PUT' }),
  startTodo: (app: AppName, id: string) =>
    request<{ ok: boolean }>(`/api/${app}/todos/${id}/start`, { method: 'PUT' }),
  revertTodo: (app: AppName, id: string) =>
    request<{ ok: boolean }>(`/api/${app}/todos/${id}/revert`, { method: 'PUT' }),
  reopenTodo: (app: AppName, id: string) =>
    request<{ ok: boolean }>(`/api/${app}/todos/${id}/reopen`, { method: 'PUT' }),
  reports: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<Report[]>(`/api/${app}/reports${query(params)}`),
  report: (app: AppName, id: string) => request<Report>(`/api/${app}/reports/${id}`),
  deleteReport: (app: AppName, id: string) =>
    request<{ deleted: boolean }>(`/api/${app}/reports/${id}`, { method: 'DELETE' }),
  feedDigests: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<FeedDigest[]>(`/api/${app}/feed-digests${query(params)}`),
  feedDigest: (app: AppName, id: string) =>
    request<FeedDigest>(`/api/${app}/feed-digests/${id}`),
  deleteFeedDigest: (app: AppName, id: string) =>
    request<{ deleted: boolean }>(`/api/${app}/feed-digests/${id}`, { method: 'DELETE' }),
  runFeedDigest: (app: AppName, preset?: string) =>
    request<FeedDigest>(`/api/${app}/feed-digests/run`, {
      method: 'POST',
      body: JSON.stringify({ preset: preset ?? null }),
    }),
  runFeedDigestStream: async (
    app: AppName,
    preset: string | undefined,
    onProgress: (message: string) => void,
  ): Promise<FeedDigest> => {
    const response = await fetch(`/api/${app}/feed-digests/run/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset: preset ?? null }),
    });
    if (response.status === 401) {
      window.location.href = '/_gate';
      throw new Error('Authentication required');
    }
    if (!response.ok || !response.body) {
      const message = await response.text();
      throw new Error(message || `Request failed: ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let result: FeedDigest | null = null;
    let streamError: string | null = null;

    const handleEvent = (raw: string) => {
      const line = raw.split('\n').find((l) => l.startsWith('data:'));
      if (!line) return;
      const payload = line.slice(5).trim();
      if (!payload) return;
      const event = JSON.parse(payload) as
        | { type: 'progress'; message: string }
        | { type: 'done'; report: FeedDigest }
        | { type: 'error'; message: string };
      if (event.type === 'progress') onProgress(event.message);
      else if (event.type === 'done') result = event.report;
      else if (event.type === 'error') streamError = event.message;
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        handleEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }
    if (buffer.trim()) handleEvent(buffer);

    if (streamError) throw new Error(streamError);
    if (!result) throw new Error('Feed digest stream ended without a result');
    return result;
  },
  generateReport: (app: AppName, type: string) =>
    request<Report>(`/api/${app}/reports/generate`, {
      method: 'POST',
      body: JSON.stringify({ type }),
    }),
  process: (app: AppName) =>
    request<Record<string, unknown>>(`/api/${app}/process`, {
      method: 'POST',
      body: JSON.stringify({ limit: 20 }),
    }),
  config: (app: AppName) => request<Record<string, unknown>>(`/api/${app}/config`),
  gatewayStatus: (app: AppName) => request<GatewayStatus>(`/api/${app}/gateway/status`),
  gatewayStart: (app: AppName) =>
    request<GatewayStatus>(`/api/${app}/gateway/start`, { method: 'POST' }),
  gatewayStop: (app: AppName) =>
    request<GatewayStatus>(`/api/${app}/gateway/stop`, { method: 'POST' }),
  decisions: (params: Record<string, QueryValue> = {}) =>
    request<Decision[]>(`/api/wolo/decisions${query(params)}`),
  highlights: (params: Record<string, QueryValue> = {}) =>
    request<Highlight[]>(`/api/wolo/highlights${query(params)}`),
  search: (app: AppName, params: Record<string, QueryValue>) =>
    request<SearchResult>(`/api/${app}/search${query(params)}`),

  // Chat sessions
  chatSessions: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<ChatSession[]>(`/api/${app}/chat/sessions${query(params)}`),
  chatSession: (app: AppName, sessionKey: string) =>
    request<ChatSessionDetail>(`/api/${app}/chat/sessions/${sessionKey}`),
  deleteChatSession: (app: AppName, sessionKey: string) =>
    request<{ deleted: boolean }>(`/api/${app}/chat/sessions/${sessionKey}`, { method: 'DELETE' }),
  exportChatMarkdown: (app: AppName, sessionKey: string) =>
    `/api/${app}/chat/sessions/${sessionKey}/export/markdown`,
  exportChatHtml: (app: AppName, sessionKey: string) =>
    `/api/${app}/chat/sessions/${sessionKey}/export/html`,

  // Chat file upload
  uploadChatFile: async (file: File): Promise<{ path: string; disk_path: string }> => {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch('/api/chat/upload', { method: 'POST', body: form });
    if (response.status === 401) {
      window.location.href = '/_gate';
      throw new Error('Authentication required');
    }
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `Upload failed: ${response.status}`);
    }
    return (await response.json()) as { path: string; disk_path: string };
  },

  // ── Projects ──────────────────────────────────────────────────────
  projects: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<Project[]>(`/api/${app}/projects${query(params)}`),
  project: (app: AppName, id: string) =>
    request<Project>(`/api/${app}/projects/${id}`),
  createProject: (app: AppName, data: Record<string, unknown>) =>
    request<Project>(`/api/${app}/projects`, { method: "POST", body: JSON.stringify(data) }),
  updateProject: (app: AppName, id: string, data: Record<string, unknown>) =>
    request<Project>(`/api/${app}/projects/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteProject: (app: AppName, id: string) =>
    request<{ deleted: boolean }>(`/api/${app}/projects/${id}`, { method: "DELETE" }),
  completeProject: (app: AppName, id: string) =>
    request<Project>(`/api/${app}/projects/${id}/complete`, { method: "PUT" }),
  archiveProject: (app: AppName, id: string, reason?: string) =>
    request<Project>(`/api/${app}/projects/${id}/archive`, { method: "PUT", body: JSON.stringify({ reason: reason || "" }) }),
  reactivateProject: (app: AppName, id: string) =>
    request<Project>(`/api/${app}/projects/${id}/reactivate`, { method: "PUT" }),
  milestones: (app: AppName, projectId: string) =>
    request<Milestone[]>(`/api/${app}/projects/${projectId}/milestones`),
  createMilestone: (app: AppName, projectId: string, data: Record<string, unknown>) =>
    request<Milestone>(`/api/${app}/projects/${projectId}/milestones`, { method: "POST", body: JSON.stringify(data) }),
  completeMilestone: (app: AppName, milestoneId: string) =>
    request<{ ok: boolean }>(`/api/${app}/milestones/${milestoneId}/complete`, { method: "PUT" }),
  deleteMilestone: (app: AppName, milestoneId: string) =>
    request<{ deleted: boolean }>(`/api/${app}/milestones/${milestoneId}`, { method: "DELETE" }),
  projectLinks: (app: AppName, projectId: string) =>
    request<ProjectLink[]>(`/api/${app}/projects/${projectId}/links`),
  createProjectLink: (app: AppName, projectId: string, data: Record<string, unknown>) =>
    request<ProjectLink>(`/api/${app}/projects/${projectId}/links`, { method: "POST", body: JSON.stringify(data) }),
  deleteProjectLink: (app: AppName, linkId: string) =>
    request<{ deleted: boolean }>(`/api/${app}/project-links/${linkId}`, { method: "DELETE" }),
  acceptProjectLink: (app: AppName, linkId: string) =>
    request<{ ok: boolean }>(`/api/${app}/project-links/${linkId}/accept`, { method: "PUT" }),
  rejectProjectLink: (app: AppName, linkId: string) =>
    request<{ ok: boolean }>(`/api/${app}/project-links/${linkId}/reject`, { method: "PUT" }),
};
