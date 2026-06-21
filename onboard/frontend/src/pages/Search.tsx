import { Fragment, ReactNode, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { SearchBar } from '../components/SearchBar';
import { useApi } from '../hooks/useApi';

function parseQuery(raw: string): { text: string; tags: string[] } {
  const tokens = raw.split(/\s+/).filter(Boolean);
  const tags: string[] = [];
  const text: string[] = [];
  for (const t of tokens) {
    if (t.startsWith('#') && t.length > 1) tags.push(t.slice(1));
    else text.push(t);
  }
  return { text: text.join(' '), tags };
}

function composeQuery(text: string, tags: string[]): string {
  const parts = [...tags.map((t) => `#${t}`)];
  if (text.trim()) parts.push(text.trim());
  return parts.join(' ').trim();
}

function splitRecordTags(raw: string): string[] {
  return raw.split(',').map((t) => t.trim()).filter(Boolean);
}

function escapeRegex(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function HighlightText({ text, tokens }: { text: string; tokens: string[] }): ReactNode {
  const cleaned = tokens.map((t) => t.trim()).filter(Boolean);
  if (cleaned.length === 0 || !text) return <>{text}</>;
  const pattern = new RegExp(`(${cleaned.map(escapeRegex).join('|')})`, 'ig');
  const matches = [...text.matchAll(pattern)];
  if (matches.length === 0) return <>{text}</>;

  const nodes: ReactNode[] = [];
  let cursor = 0;
  matches.forEach((match, i) => {
    const start = match.index ?? 0;
    const matchedText = match[0];
    if (start > cursor) {
      nodes.push(<Fragment key={`t-${i}`}>{text.slice(cursor, start)}</Fragment>);
    }
    nodes.push(
      <mark
        key={`m-${i}`}
        style={{
          background: 'rgba(251, 191, 36, 0.35)',
          color: 'inherit',
          boxShadow: '0 0 0 1px rgba(251, 191, 36, 0.55)',
          borderRadius: 2,
          padding: '0 2px',
          fontWeight: 500,
        }}
      >
        {matchedText}
      </mark>,
    );
    cursor = start + matchedText.length;
  });
  if (cursor < text.length) {
    nodes.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>);
  }
  return <>{nodes}</>;
}

export function Search({ appName }: { appName: AppName }) {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const { text, tags } = parseQuery(q);
  const hasActiveFilters = tags.length > 0 || text.trim().length > 0;
  const [draft, setDraft] = useState(q);

  // Keep the draft in sync when the URL query changes externally (e.g. back/forward,
  // trending-pill click, lucky pick). SearchBar resets its own input via useEffect
  // on `initialValue`, so we just mirror the URL here.
  useEffect(() => { setDraft(q); }, [q]);

  const { data, error, loading } = useApi(
    () =>
      api.search(appName, {
        q: text || null,
        tags: tags.length ? tags.join(',') : null,
        limit: 50,
      }),
    [appName, text, tags.join(',')],
    { enabled: hasActiveFilters },
  );

  const { data: stats, loading: statsLoading } = useApi(
    () => api.stats(appName),
    [appName],
    { enabled: !hasActiveFilters },
  );

  const trendingTags = (stats?.top_tags ?? []).slice(0, 10);
  const trendingPool = trendingTags.flatMap((t) => Array(Math.max(1, t.count)).fill(t.tag));

  function removeTag(tag: string) {
    const next = composeQuery(text, tags.filter((t) => t !== tag));
    setParams(next ? { q: next } : {});
  }

  function removeText() {
    const next = composeQuery('', tags);
    setParams(next ? { q: next } : {});
  }

  const resultCount = data?.records.length ?? 0;
  const isEmpty = resultCount === 0 && hasActiveFilters && !loading;

  function submitLucky() {
    if (trendingPool.length === 0) return;
    const pick = trendingPool[Math.floor(Math.random() * trendingPool.length)];
    setParams({ q: `#${pick}` });
  }

  const heroHeader = (
    <header className="flex flex-col items-center text-center gap-7 animate-[fade-in_0.35s_ease-out_both] w-full px-4">
      <h1 className="font-serif italic text-[72px] sm:text-[92px] leading-none m-0 tracking-[0.04em] select-none">
        <span className="text-accent-solo">S</span>
        <span className="text-text">earch</span>
      </h1>
      <SearchBar
        initialValue={q}
        variant="hero"
        placeholder="Search records or #tag"
        onChange={(value) => setDraft(value)}
        onSearch={(value) => setParams(value ? { q: value } : {})}
      />
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => {
            const next = draft.trim();
            setParams(next ? { q: next } : {});
          }}
          disabled={!draft.trim()}
          className="px-5 py-2.5 rounded-md bg-surface-2 border border-border/60 text-[13px] text-text-secondary hover:border-border hover:shadow-[0_1px_2px_rgba(0,0,0,0.1)] hover:bg-surface-3 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Search
        </button>
        <button
          type="button"
          onClick={submitLucky}
          disabled={statsLoading || trendingPool.length === 0}
          className="px-5 py-2.5 rounded-md bg-surface-2 border border-border/60 text-[13px] text-text-secondary hover:border-border hover:shadow-[0_1px_2px_rgba(0,0,0,0.1)] hover:bg-surface-3 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
        >
          I'm Feeling Lucky
        </button>
      </div>
      {trendingTags.length > 0 && (
        <div className="flex flex-col items-center gap-2 w-full max-w-[46rem]">
          <span className="text-[11px] uppercase tracking-widest text-text-muted">Trending</span>
          <div className="flex flex-wrap items-center justify-center gap-1.5">
            {trendingTags.map((t) => (
              <button
                key={t.tag}
                type="button"
                onClick={() => setParams({ q: `#${t.tag}` })}
                className="inline-flex items-center gap-1 pl-2 pr-2.5 py-1 rounded-full bg-surface-2 border border-border/60 text-[12px] text-text-secondary hover:border-accent-solo/40 hover:text-accent-solo hover:bg-surface-3 transition-all"
              >
                <span className="font-mono text-[10px] text-accent-solo/70">#</span>
                <span>{t.tag}</span>
                <span className="text-[10px] text-text-muted">{t.count}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <p className="text-[12px] text-text-muted max-w-md leading-relaxed">
        Mix free text and <span className="font-mono text-text-secondary">#tags</span> — for example{' '}
        <button
          type="button"
          onClick={() => setParams({ q: 'meeting #work #decision' })}
          className="font-mono text-accent-solo hover:underline bg-transparent border-0 p-0 cursor-pointer"
        >
          meeting #work #decision
        </button>
        .
      </p>
    </header>
  );

  const resultsHeader = (
    <div className="space-y-5">
      <div className="flex items-center gap-4">
        <Link
          to="/search"
          className="font-serif text-2xl text-text m-0 hover:text-accent-solo transition-colors no-underline"
          aria-label="Clear search"
        >
          Search
        </Link>
        <SearchBar initialValue={q} onSearch={(value) => setParams(value ? { q: value } : {})} />
        {resultCount > 0 && (
          <span className="text-[11px] text-text-muted">
            {resultCount} {resultCount === 1 ? 'result' : 'results'}
          </span>
        )}
      </div>

      {hasActiveFilters && (
        <div className="flex items-center flex-wrap gap-1.5">
          <span className="text-[11px] text-text-muted mr-1">Filters:</span>
          {tags.map((tag) => (
            <button
              key={tag}
              onClick={() => removeTag(tag)}
              className="inline-flex items-center gap-0.5 pl-1.5 pr-1 py-0.5 rounded-full bg-accent-solo-dim text-accent-solo border border-accent-solo/30 text-[11px] hover:bg-accent-solo/20 transition-colors"
            >
              <span className="font-mono text-[10px] text-accent-solo/70">#</span>
              <span>{tag}</span>
              <span className="ml-0.5 text-accent-solo/70 leading-none">×</span>
            </button>
          ))}
          {text && (
            <button
              onClick={removeText}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-3 text-text-secondary border border-border text-[11px] hover:bg-surface-2 transition-colors"
            >
              <span>text: "{text}"</span>
              <span className="text-text-muted leading-none">×</span>
            </button>
          )}
        </div>
      )}

      {loading ? <div className="h-40 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" /> : null}
      {error ? <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error}</div> : null}
      {isEmpty ? (
        <EmptyState
          icon={
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
          }
          title={
            tags.length > 0
              ? `No records matched ${tags.length > 1 ? 'tags' : 'tag'} ${tags.map((t) => `#${t}`).join(' ')}`
              : `No results for "${text}"`
          }
          description="Try different keywords, remove a tag, or check your spelling."
        />
      ) : null}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-3">
        {[...(data?.records ?? [])].sort((a, b) => (b.date || b.created_at).localeCompare(a.date || a.created_at)).map((record, index) => {
          const recordTags = splitRecordTags(record.tags);
          const matchedTagSet = new Set(tags.map((t) => t.toLowerCase()));
          return (
            <article
              key={record.id}
              className="flex flex-col h-[160px] p-4 border border-border rounded-lg bg-surface-1 hover:bg-surface-2 hover:border-text-muted/30 transition-all animate-[fade-in_0.3s_ease-out_both]"
              style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}
            >
              <div className="flex-1 min-h-0 overflow-hidden">
                <h3 className="text-sm font-medium text-text m-0 mb-1 line-clamp-1">
                  <HighlightText text={record.summary} tokens={text ? text.split(/\s+/) : []} />
                </h3>
                <p className="text-[13px] text-text-secondary m-0 line-clamp-3">
                  <HighlightText text={record.corrected_content} tokens={text ? text.split(/\s+/) : []} />
                </p>
              </div>
              <div className="shrink-0 mt-2 flex items-center justify-between gap-2">
                <div className="flex flex-wrap gap-1 min-w-0">
                  {recordTags.slice(0, 4).map((tag) => {
                    const isMatch = matchedTagSet.has(tag.toLowerCase());
                    return (
                      <span
                        key={tag}
                        className={`text-[10px] px-1.5 py-0.5 rounded ${
                          isMatch
                            ? 'bg-accent-solo/25 text-accent-solo border border-accent-solo/40'
                            : 'bg-surface-3 text-text-muted'
                        }`}
                      >
                        {tag}
                      </span>
                    );
                  })}
                </div>
                <Link className="text-[12px] text-accent-solo hover:underline no-underline shrink-0" to={`/records/${record.id}`}>
                  View →
                </Link>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );

  if (!hasActiveFilters) {
    return (
      <div className="flex flex-col min-h-[calc(100vh-8rem)]">
        <div className="flex-1 flex flex-col items-center justify-center">
          {heroHeader}
        </div>
        <footer className="border-t border-border/50 bg-surface-1/40 text-[11px] text-text-muted">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-2 px-6 py-3">
            <span>OpenHarness · Search across your records</span>
            <div className="flex items-center gap-4">
              <span>Tip: press <kbd className="font-mono text-text-secondary">⏎</kbd> to search</span>
              <span>Combine <span className="font-mono text-text-secondary">#tag</span> with keywords</span>
            </div>
          </div>
        </footer>
      </div>
    );
  }

  return resultsHeader;
}
