import { Fragment, ReactNode } from 'react';
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
  if (cleaned.length === 0) return <>{text}</>;
  const pattern = new RegExp(`(${cleaned.map(escapeRegex).join('|')})`, 'ig');
  const parts = text.split(pattern);
  return (
    <>
      {parts.map((part, i) => {
        const isMatch = cleaned.some((t) => t.toLowerCase() === part.toLowerCase());
        return isMatch ? (
          <mark key={i} className="bg-accent-solo/25 text-text rounded px-0.5">
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        );
      })}
    </>
  );
}

export function Search({ appName }: { appName: AppName }) {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const { text, tags } = parseQuery(q);
  const { data, error, loading } = useApi(
    () =>
      api.search(appName, {
        q: text || null,
        tags: tags.length ? tags.join(',') : null,
        limit: 50,
      }),
    [appName, text, tags.join(',')],
  );

  function removeTag(tag: string) {
    const next = composeQuery(text, tags.filter((t) => t !== tag));
    setParams(next ? { q: next } : {});
  }

  function removeText() {
    const next = composeQuery('', tags);
    setParams(next ? { q: next } : {});
  }

  const hasActiveFilters = tags.length > 0 || text.length > 0;
  const resultCount = data?.records.length ?? 0;
  const isEmpty = resultCount === 0 && hasActiveFilters;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-4">
        <h2 className="font-serif text-2xl text-text m-0">Search</h2>
        <SearchBar initialValue={q} onSearch={(value) => setParams(value ? { q: value } : {})} />
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
      {isEmpty && !loading ? (
        <EmptyState
          icon={<span>⌕</span>}
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
}
