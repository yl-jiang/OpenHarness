import { useParams } from 'react-router-dom';

import { api } from '../api/client';
import type { AppName } from '../api/types';
import { Breadcrumb } from '../components/Breadcrumb';
import { MarkdownView } from '../components/MarkdownView';
import { useApi } from '../hooks/useApi';

export function RecordDetail({ appName }: { appName: AppName }) {
  const { id = '' } = useParams();
  const { data, error, loading } = useApi(() => api.record(appName, id), [appName, id]);
  if (loading) {
    return <div className="h-60 rounded-lg bg-gradient-to-r from-surface-1 via-surface-2 to-surface-1 bg-[length:200%_auto] animate-[shimmer_1.5s_linear_infinite]" />;
  }
  if (error || !data) {
    return <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-sm text-text" role="alert">{error ?? 'Record not found.'}</div>;
  }

  const contextFields: { label: string; value: string }[] = [];
  if (data.period) contextFields.push({ label: 'Period', value: data.period });
  if (data.season) contextFields.push({ label: 'Season', value: data.season });
  if (data.weekday) contextFields.push({ label: 'Day', value: data.weekday });
  if (data.is_weekend) contextFields.push({ label: 'Weekend', value: 'Yes' });
  if (data.events) contextFields.push({ label: 'Events', value: data.events });

  const people = data.related_people ? data.related_people.split(',').map((s) => s.trim()).filter(Boolean) : [];
  const places = data.related_places ? data.related_places.split(',').map((s) => s.trim()).filter(Boolean) : [];

  return (
    <div className="max-w-3xl space-y-6">
      <Breadcrumb items={[
        { label: 'Records', to: '/records' },
        { label: data.summary || data.date },
      ]} />

      <div className="border border-border rounded-lg bg-surface-1 p-6">
        <div className="flex items-start justify-between mb-5">
          <h2 className="font-serif text-xl text-text m-0">{data.summary || data.date}</h2>
          <span className="text-[11px] px-2 py-0.5 rounded bg-accent-solo-dim text-accent-solo shrink-0 ml-4">{data.emotion}</span>
        </div>
        <div className="grid grid-cols-[100px_1fr] gap-x-4 gap-y-2 text-[13px] mb-6">
          <span className="text-text-muted">Date</span>
          <span className="text-text-secondary">{data.date}</span>
          <span className="text-text-muted">Tags</span>
          <span className="text-text-secondary">{data.tags || '—'}</span>
          <span className="text-text-muted">Source</span>
          <span className="text-text-secondary font-mono text-[12px]">{data.source}</span>
          <span className="text-text-muted">Entry</span>
          <span className="text-text-secondary font-mono text-[12px]">{data.entry_id}</span>
        </div>

        {/* Context section */}
        {contextFields.length > 0 && (
          <div className="mb-5 p-3 rounded-md bg-surface-2 border border-border-subtle">
            <span className="text-[11px] text-text-muted uppercase tracking-wider block mb-2">Context</span>
            <div className="flex flex-wrap gap-3 text-[12px]">
              {contextFields.map((f) => (
                <span key={f.label} className="inline-flex items-center gap-1.5">
                  <span className="text-text-muted">{f.label}:</span>
                  <span className="text-text-secondary">{f.value}</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Emotion reason */}
        {data.emotion_reason && (
          <div className="mb-5 p-3 rounded-md bg-surface-2 border border-border-subtle">
            <span className="text-[11px] text-text-muted uppercase tracking-wider block mb-1">Emotion reason</span>
            <p className="text-[13px] text-text-secondary m-0 leading-relaxed">{data.emotion_reason}</p>
          </div>
        )}

        {/* People & Places */}
        {(people.length > 0 || places.length > 0) && (
          <div className="mb-5 flex flex-wrap gap-4 text-[12px]">
            {people.length > 0 && (
              <div>
                <span className="text-text-muted mr-2">People:</span>
                {people.map((p) => (
                  <span key={p} className="inline-block px-1.5 py-0.5 text-[11px] rounded bg-surface-3 text-text-secondary mr-1 mb-1">{p}</span>
                ))}
              </div>
            )}
            {places.length > 0 && (
              <div>
                <span className="text-text-muted mr-2">Places:</span>
                {places.map((p) => (
                  <span key={p} className="inline-block px-1.5 py-0.5 text-[11px] rounded bg-surface-3 text-text-secondary mr-1 mb-1">{p}</span>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="border-t border-border pt-5">
          <MarkdownView content={data.corrected_content || data.raw_content} />
        </div>
      </div>
    </div>
  );
}
