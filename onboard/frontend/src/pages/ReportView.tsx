import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

interface TocItem {
  id: string;
  level: number;
  text: string;
}

function formatCreatedAt(raw: string): string {
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60) || 'section';
}

function extractHeadings(content: string): TocItem[] {
  const lines = content.split('\n');
  const items: TocItem[] = [];
  const seen = new Map<string, number>();
  let inFence = false;
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (/^```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;
    const level = match[1].length;
    const text = match[2].replace(/`/g, '').replace(/\*\*/g, '').trim();
    if (!text) continue;
    const base = slugify(text);
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    const id = count === 0 ? base : `${base}-${count}`;
    items.push({ id, level, text });
  }
  return items;
}

export function ReportView({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.report(appName, id), [appName, id]);
  const [activeId, setActiveId] = useState<string>('');

  const headings = useMemo(() => (data?.content ? extractHeadings(data.content) : []), [data?.content]);

  useEffect(() => {
    if (!data?.content) return;
    const els = headings
      .map((h) => document.getElementById(h.id))
      .filter((el): el is HTMLElement => el !== null);
    if (els.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      { rootMargin: '-80px 0px -70% 0px', threshold: 0 },
    );
    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [data?.content, headings]);

  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text">{error ?? 'Report not found.'}</div>;
  }

  const periodLabel =
    data.period_start && data.period_end
      ? data.period_start === data.period_end
        ? data.period_start
        : `${data.period_start} → ${data.period_end}`
      : null;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_280px] gap-6 items-start">
      <article className="min-w-0 border border-border rounded-lg bg-surface-1 p-6 md:p-8">
        <div className="flex items-center justify-between gap-4 mb-5 pb-4 border-b border-border">
          <h2 className="font-serif text-xl text-text m-0 capitalize">{data.report_type} Report</h2>
          <span className="text-[11px] font-mono text-text-muted whitespace-nowrap">{formatCreatedAt(data.created_at)}</span>
        </div>
        {data.content ? (
          <MarkdownView content={data.content} headingIds={headings.map((h) => h.id)} />
        ) : (
          <div className="border border-warning/30 rounded-md bg-warning/5 p-5 space-y-2">
            <p className="text-sm text-text m-0 font-medium">Report generated but no content was produced.</p>
            <p className="text-[13px] text-text-secondary m-0 leading-relaxed">
              Possible causes: AI provider not configured (check Settings → Gateway), no records in the selected period, or the LLM returned an empty response.
            </p>
          </div>
        )}
      </article>

      <aside className="hidden lg:flex lg:flex-col gap-4 sticky top-20">
        <div className="border border-border rounded-lg bg-surface-1 p-4 space-y-3">
          <h3 className="text-[11px] font-mono uppercase tracking-wider text-text-muted m-0">Report Info</h3>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-[12px]">
            <dt className="text-text-muted">Type</dt>
            <dd className="text-text m-0 capitalize">{data.report_type}</dd>
            {periodLabel ? (
              <>
                <dt className="text-text-muted">Period</dt>
                <dd className="text-text m-0 font-mono">{periodLabel}</dd>
              </>
            ) : null}
            <dt className="text-text-muted">Generated</dt>
            <dd className="text-text m-0 font-mono">{formatCreatedAt(data.created_at)}</dd>
            <dt className="text-text-muted">App</dt>
            <dd className="text-text m-0 font-mono uppercase">{appName}</dd>
          </dl>
        </div>

        {headings.length > 0 ? (
          <nav className="border border-border rounded-lg bg-surface-1 p-4">
            <h3 className="text-[11px] font-mono uppercase tracking-wider text-text-muted m-0 mb-3">Contents</h3>
            <ul className="list-none m-0 p-0 space-y-1.5 max-h-[60vh] overflow-y-auto pr-1">
              {headings.map((h) => (
                <li
                  key={h.id}
                  style={{ paddingLeft: `${(h.level - 1) * 10}px` }}
                >
                  <a
                    href={`#${h.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      const el = document.getElementById(h.id);
                      if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        setActiveId(h.id);
                      }
                    }}
                    className={`block text-[12px] leading-snug no-underline truncate transition-colors ${
                      activeId === h.id
                        ? 'text-accent-solo'
                        : 'text-text-secondary hover:text-text'
                    }`}
                    title={h.text}
                  >
                    {h.text}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        ) : null}
      </aside>
    </div>
  );
}
