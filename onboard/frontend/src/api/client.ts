import type {
  AppName,
  AppStats,
  Decision,
  Entry,
  GatewayStatus,
  Highlight,
  LogRecord,
  PaginatedResponse,
  Report,
  SearchResult,
  Todo,
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
  reports: (app: AppName, params: Record<string, QueryValue> = {}) =>
    request<Report[]>(`/api/${app}/reports${query(params)}`),
  report: (app: AppName, id: string) => request<Report>(`/api/${app}/reports/${id}`),
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
};
