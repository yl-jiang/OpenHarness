import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName, FeedDigest, FeedDigestMeta } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { LIVE_REFRESH_INTERVAL_MS, useApi } from '../hooks/useApi';

function formatRelativeDate(raw: string): string {
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  if (diffDays === 0) return `Today ${time}`;
  if (diffDays === 1) return `Yesterday ${time}`;
  if (diffDays < 7) return `${d.toLocaleDateString(undefined, { weekday: 'short' })} ${time}`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function formatPeriodCompact(digest: FeedDigest): string {
  if (!digest.period_start && !digest.period_end) return '';
  const fmt = (s: string) => {
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s;
    return d.toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
  };
  const start = digest.period_start ? fmt(digest.period_start) : '';
  const end = digest.period_end ? fmt(digest.period_end) : '';
  if (!start && !end) return '';
  return `${start} — ${end}`;
}

function sortDigests(items: FeedDigest[]): FeedDigest[] {
  return [...items].sort((a, b) => {
    const left = a.period_start || a.created_at;
    const right = b.period_start || b.created_at;
    return right.localeCompare(left);
  });
}

function DigestMetaPanel({ meta, accentBorder }: { meta: FeedDigestMeta; accentBorder: string }) {
  const stats = meta.source_stats ?? [];
  const totalFetched = stats.reduce((s, r) => s + r.fetched, 0);
  const failedCount = stats.filter((r) => r.failed).length;

  return (
    <div className="border border-border rounded-lg bg-surface-2/50 p-4 space-y-4 text-[12px]">
      <div className="flex items-center gap-4 flex-wrap text-text-secondary">
        <span>
          <span className="text-text-muted mr-1">Selected</span>
          <span className="font-mono font-medium text-text">{meta.selected_count}</span>
        </span>
        <span>
          <span className="text-text-muted mr-1">Fetched</span>
          <span className="font-mono font-medium text-text">{totalFetched}</span>
        </span>
        {failedCount > 0 && (
          <span>
            <span className="text-text-muted mr-1">Failed sources</span>
            <span className="font-mono font-medium text-danger">{failedCount}</span>
          </span>
        )}
        {meta.is_empty && (
          <span className={`px-2 py-0.5 rounded-full border text-[11px] uppercase tracking-wide ${accentBorder}`}>
            empty
          </span>
        )}
      </div>

      {stats.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] border-collapse">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left pb-1.5 pr-4 font-medium text-text-muted">Source</th>
                <th className="text-right pb-1.5 pr-4 font-medium text-text-muted w-16">Fetched</th>
                <th className="text-right pb-1.5 pr-4 font-medium text-text-muted w-16">Selected</th>
                <th className="text-right pb-1.5 font-medium text-text-muted w-14">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {stats.map((row) => {
                const selectRate = row.fetched > 0 ? Math.round((row.selected / row.fetched) * 100) : 0;
                return (
                  <tr key={row.source} className={row.failed ? 'opacity-50' : ''}>
                    <td className="py-1.5 pr-4 font-mono text-text truncate max-w-[200px]">{row.source}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-text-secondary">{row.fetched}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-text-secondary">
                      {row.selected}
                      {row.fetched > 0 && (
                        <span className="ml-1 text-text-muted">({selectRate}%)</span>
                      )}
                    </td>
                    <td className="py-1.5 text-right">
                      {row.failed ? (
                        <span className="text-danger">failed</span>
                      ) : (
                        <span className="text-success">ok</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const PRESETS = [
  { value: 'ai_news', label: 'AI News' },
] as const;

const INITIAL_RUN_MESSAGE = 'Starting...';

type DigestRunState = { running: boolean; runMessage: string };
const _digestRunStates = new Map<AppName, DigestRunState>();
const _digestRunListeners = new Map<AppName, Set<() => void>>();

function getDigestRunState(app: AppName): DigestRunState {
  return _digestRunStates.get(app) ?? { running: false, runMessage: INITIAL_RUN_MESSAGE };
}

function setDigestRunState(app: AppName, update: Partial<DigestRunState>): void {
  const next = { ...getDigestRunState(app), ...update };
  _digestRunStates.set(app, next);
  for (const listener of _digestRunListeners.get(app) ?? []) listener();
}

function useDigestRunState(app: AppName): [DigestRunState, (update: Partial<DigestRunState>) => void] {
  const [state, setState] = useState<DigestRunState>(() => getDigestRunState(app));

  useEffect(() => {
    setState(getDigestRunState(app));
    const listener = () => setState(getDigestRunState(app));
    if (!_digestRunListeners.has(app)) _digestRunListeners.set(app, new Set());
    _digestRunListeners.get(app)!.add(listener);
    return () => { _digestRunListeners.get(app)?.delete(listener); };
  }, [app]);

  const setter = useCallback((update: Partial<DigestRunState>) => setDigestRunState(app, update), [app]);
  return [state, setter];
}

export function FeedDigests({ appName }: { appName: AppName }) {
  const navigate = useNavigate();
  const { id = '' } = useParams();
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [{ running, runMessage }, setRunState] = useDigestRunState(appName);
  const [runPreset, setRunPreset] = useState<string>('ai_news');
  const [showMeta, setShowMeta] = useState(false);
  const accent = appName === 'solo' ? 'text-accent-solo' : 'text-accent-wolo';
  const accentBorder = appName === 'solo' ? 'border-accent-solo/30 bg-accent-solo-dim/30' : 'border-accent-wolo/30 bg-accent-wolo-dim/30';
  const accentBtn = appName === 'solo' ? 'bg-accent-solo/10 border-accent-solo/30 hover:bg-accent-solo/20 text-accent-solo' : 'bg-accent-wolo/10 border-accent-wolo/30 hover:bg-accent-wolo/20 text-accent-wolo';

  const listState = useApi(() => api.feedDigests(appName), [appName], {
    refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS,
  });
  const detailState = useApi(
    () => (id ? api.feedDigest(appName, id) : Promise.resolve<FeedDigest | null>(null)),
    [appName, id],
    { refreshIntervalMs: id ? LIVE_REFRESH_INTERVAL_MS : undefined },
  );

  async function deleteDigest(digestId: string) {
    if (!confirm('Delete this feed digest permanently?')) return;
    setDeleting(digestId);
    setActionError(null);
    try {
      await api.deleteFeedDigest(appName, digestId);
      listState.reload();
      if (id === digestId) {
        navigate('/feeds');
      } else {
        detailState.reload();
      }
    } catch (err) {
      setActionError(`Failed to delete feed digest${err instanceof Error ? `: ${err.message}` : ''}`);
    } finally {
      setDeleting(null);
    }
  }

  function requestNotificationPermission(): void {
    if ('Notification' in window && Notification.permission === 'default') {
      void Notification.requestPermission();
    }
  }

  function notifyDigestReady(digest: FeedDigest): void {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const domain = digest.metadata?.domain ?? 'Feed Digest';
    const selected = digest.metadata?.selected_count ?? 0;
    const n = new Notification(`${domain} digest ready`, {
      body: `${selected} items selected`,
      icon: '/favicon.ico',
      tag: `feed-digest-${digest.id}`,
    });
    n.onclick = () => {
      window.focus();
      navigate(`/feeds/${digest.id}`);
      n.close();
    };
  }

  async function runDigest() {
    setRunState({ running: true, runMessage: INITIAL_RUN_MESSAGE });
    setActionError(null);
    requestNotificationPermission();

    try {
      const digest = await api.runFeedDigestStream(appName, runPreset, (message) => {
        setRunState({ runMessage: message });
      });
      notifyDigestReady(digest);
      await listState.reload();
      navigate(`/feeds/${digest.id}`);
    } catch (err) {
      setActionError(`Failed to fetch digest${err instanceof Error ? `: ${err.message}` : ''}`);
    } finally {
      setRunState({ running: false, runMessage: INITIAL_RUN_MESSAGE });
    }
  }

  if (listState.loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }

  if (listState.error || !listState.data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{listState.error ?? 'Failed to load feed digests.'}</div>;
  }

  const digests = sortDigests(listState.data);
  const selected = id ? detailState.data : null;
  const selectedError = id ? detailState.error : null;
  const selectedLoading = Boolean(id) && detailState.loading;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        <div className="flex items-baseline gap-3">
          <h2 className="font-serif text-2xl text-text m-0">Feed Digests</h2>
          <span className="text-[11px] font-mono text-text-muted">{digests.length} archived</span>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={runPreset}
            onChange={(e) => setRunPreset(e.target.value)}
            disabled={running}
            className="text-[12px] px-2 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary cursor-pointer disabled:opacity-50 focus:outline-none"
          >
            {PRESETS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
          <button
            onClick={runDigest}
            disabled={running}
            className={`flex items-center gap-1.5 text-[12px] font-medium px-3 py-1.5 rounded-md border cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed transition-colors ${accentBtn}`}
          >
            {running ? (
              <>
                <span className="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0" />
                <span className="max-w-[220px] truncate" title={runMessage}>{runMessage}</span>
              </>
            ) : (
              <>Fetch Now</>
            )}
          </button>
        </div>
      </div>

      {actionError && (
        <div className="flex items-center gap-2 border border-danger/30 rounded-md bg-danger/5 px-4 py-2.5 text-[13px] text-text">
          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-danger" />
          {actionError}
          <button
            onClick={() => setActionError(null)}
            className="ml-auto text-text-muted hover:text-text text-[11px] cursor-pointer bg-transparent border-none"
          >
            dismiss
          </button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)] items-start">
        {/* Digest list */}
        <section className="border border-border rounded-lg overflow-hidden bg-surface-1">
          {digests.length === 0 ? (
            <div className="p-8 text-center">
              <div className="text-[13px] text-text-muted">No feed digests yet.</div>
              <div className="mt-1 text-[12px] text-text-muted/60">Run a fetch to generate your first digest.</div>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {digests.map((digest, index) => {
                const active = digest.id === id;
                const meta = digest.metadata;
                const period = formatPeriodCompact(digest);
                return (
                  <div
                    key={digest.id}
                    className={`group relative transition-colors animate-[fade-in_0.3s_ease-out_both] ${active ? 'bg-surface-2' : 'hover:bg-surface-2/60'}`}
                    style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
                  >
                    {active && (
                      <span className={`absolute inset-y-0 left-0 w-0.5 ${appName === 'solo' ? 'bg-accent-solo' : 'bg-accent-wolo'}`} />
                    )}
                    <Link
                      to={`/feeds/${digest.id}`}
                      className="block px-4 py-3.5 no-underline text-inherit"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-[13px] font-medium text-text truncate">
                              {meta?.domain ?? 'Feed Digest'}
                            </span>
                            {meta?.is_empty && (
                              <span className="text-[10px] uppercase tracking-wide text-text-muted border border-border rounded px-1.5 py-0.5">empty</span>
                            )}
                          </div>
                          <div className="mt-1.5 flex items-center gap-2 text-[12px] text-text-muted flex-wrap">
                            <span>{formatRelativeDate(digest.created_at)}</span>
                            {period && (
                              <>
                                <span className="text-border">|</span>
                                <span className="font-mono text-[11px]">{period}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 shrink-0 pt-0.5">
                          <span className={`text-[11px] font-mono px-2 py-0.5 rounded-full border ${accentBorder}`}>
                            {meta?.selected_count ?? 0}
                          </span>
                          {meta?.preset && (
                            <span className={`text-[10px] font-medium ${accent}`}>
                              {meta.preset}
                            </span>
                          )}
                        </div>
                      </div>
                    </Link>
                    <button
                      onClick={() => deleteDigest(digest.id)}
                      disabled={deleting === digest.id}
                      className="absolute top-2.5 right-2.5 text-[11px] text-text-muted/0 group-hover:text-text-muted hover:!text-danger cursor-pointer bg-transparent border-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      title="Delete digest"
                    >
                      {deleting === digest.id ? '...' : 'x'}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Detail panel */}
        <section className="border border-border rounded-lg bg-surface-1 p-6 min-h-[320px]">
          {!id ? (
            <div className="h-full flex flex-col items-center justify-center gap-2 text-center">
              <div className="text-text-muted text-[28px] leading-none select-none opacity-40">&larr;</div>
              <div className="text-[13px] text-text-muted">Select a digest to view its content</div>
            </div>
          ) : selectedLoading ? (
            <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />
          ) : selectedError || !selected ? (
            <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">
              {selectedError ?? 'Feed digest not found.'}
            </div>
          ) : (
            <article className="space-y-6 animate-[fade-in_0.2s_ease-out]">
              {/* Header */}
              <header className="flex items-start justify-between gap-4 pb-5 border-b border-border">
                <div className="space-y-2">
                  <h3 className="font-serif text-2xl text-text m-0">{selected.metadata?.domain ?? 'Feed Digest'}</h3>
                  <div className="flex items-center gap-3 flex-wrap text-[12px] text-text-muted">
                    <span className={`font-medium ${accent}`}>{selected.metadata?.preset ?? 'feed_digest'}</span>
                    <span className="text-border">|</span>
                    <span>{selected.metadata?.date ?? selected.created_at}</span>
                    {formatPeriodCompact(selected) && (
                      <>
                        <span className="text-border">|</span>
                        <span className="font-mono text-[11px]">{formatPeriodCompact(selected)}</span>
                      </>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => setShowMeta((v) => !v)}
                    className={`text-[11px] px-2.5 py-1.5 rounded-md border cursor-pointer transition-colors ${showMeta ? `${accent} ${accentBorder}` : 'text-text-muted border-border bg-transparent hover:text-text-secondary hover:border-text-muted'}`}
                  >
                    {showMeta ? 'Hide metadata' : 'Metadata'}
                  </button>
                  <button
                    onClick={() => deleteDigest(selected.id)}
                    disabled={deleting === selected.id}
                    className="text-[12px] px-3 py-1.5 rounded-md border border-border bg-surface-2 text-text-secondary hover:text-danger hover:border-danger/30 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {deleting === selected.id ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </header>

              {/* Stats row */}
              {selected.metadata && (
                <div className="grid grid-cols-3 gap-3">
                  <div className="rounded-lg bg-surface-2 px-4 py-3">
                    <div className="text-[11px] text-text-muted uppercase tracking-wide">Selected</div>
                    <div className="mt-1 text-xl font-mono text-text tabular-nums">{selected.metadata.selected_count}</div>
                  </div>
                  <div className="rounded-lg bg-surface-2 px-4 py-3">
                    <div className="text-[11px] text-text-muted uppercase tracking-wide">Sources</div>
                    <div className="mt-1 text-xl font-mono text-text tabular-nums">{selected.metadata.source_stats.length}</div>
                  </div>
                  <div className="rounded-lg bg-surface-2 px-4 py-3">
                    <div className="text-[11px] text-text-muted uppercase tracking-wide">Fetched</div>
                    <div className="mt-1 text-xl font-mono text-text tabular-nums">
                      {selected.metadata.source_stats.reduce((s, r) => s + r.fetched, 0)}
                    </div>
                  </div>
                </div>
              )}

              {showMeta && selected.metadata && (
                <DigestMetaPanel meta={selected.metadata} accentBorder={accentBorder} />
              )}

              {selected.metadata?.warnings?.length ? (
                <div className="border border-warning/30 rounded-md bg-warning/5 p-4 space-y-2">
                  <div className="text-[12px] font-medium text-text">Warnings</div>
                  <ul className="m-0 pl-5 text-[13px] text-text-secondary space-y-1">
                    {selected.metadata.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {selected.content ? (
                <MarkdownView content={selected.content} />
              ) : (
                <div className="border border-warning/30 rounded-md bg-warning/5 p-5 text-sm text-text">
                  Feed digest archived without Markdown content.
                </div>
              )}
            </article>
          )}
        </section>
      </div>
    </div>
  );
}
