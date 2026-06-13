import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AppName, ProjectSuggestion } from "../api/types";
import { SegmentedControl } from "../components/SegmentedControl";

interface Props {
  appName: AppName;
}

const TYPE_LABELS: Record<string, string> = {
  link_entity: "关联实体",
  create_project: "新建项目",
  complete_milestone: "完成里程碑",
  create_milestone: "新建里程碑",
  update_project: "更新项目",
  archive_project: "归档项目",
  reactivate_project: "重启项目",
  merge_projects: "合并项目",
  split_project: "拆分项目",
  ask_followup: "跟进问题",
};

const STATUS_OPTIONS = [
  { label: "Pending", value: "pending" },
  { label: "All", value: "" },
];

export function ProjectInbox({ appName }: Props) {
  const [suggestions, setSuggestions] = useState<ProjectSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("pending");
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  const fetchSuggestions = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number | boolean | null | undefined> = {};
      if (statusFilter) params.status = statusFilter;
      const data = await api.projectSuggestions(appName, params);
      setSuggestions(data);
    } catch {
      setSuggestions([]);
    } finally {
      setLoading(false);
    }
  }, [appName, statusFilter]);

  useEffect(() => {
    fetchSuggestions();
  }, [fetchSuggestions]);

  const handleAction = async (id: string, action: "accept" | "reject" | "snooze") => {
    setBusy((prev) => ({ ...prev, [id]: true }));
    try {
      if (action === "accept") await api.acceptProjectSuggestion(appName, id);
      else if (action === "reject") await api.rejectProjectSuggestion(appName, id);
      else await api.snoozeProjectSuggestion(appName, id);
      setSuggestions((prev) => prev.filter((s) => s.id !== id));
    } catch {
      // keep suggestion on failure
    } finally {
      setBusy((prev) => ({ ...prev, [id]: false }));
    }
  };

  // Group by suggestion_type
  const grouped = suggestions.reduce<Record<string, ProjectSuggestion[]>>((acc, s) => {
    const key = s.suggestion_type;
    if (!acc[key]) acc[key] = [];
    acc[key].push(s);
    return acc;
  }, {});

  const groupKeys = Object.keys(grouped);

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-text">AI Suggestions</h1>
          <p className="mt-0.5 text-xs text-text-muted">
            {suggestions.length} pending suggestion{suggestions.length !== 1 ? "s" : ""}
          </p>
        </div>
        <SegmentedControl
          options={STATUS_OPTIONS}
          value={statusFilter}
          onChange={setStatusFilter}
        />
      </div>

      {/* Content */}
      {loading ? (
        <div className="py-20 text-center text-sm text-text-muted">Loading...</div>
      ) : groupKeys.length === 0 ? (
        <div className="py-20 text-center text-sm text-text-muted">
          {statusFilter === "pending"
            ? "No pending suggestions. Process some records to generate suggestions."
            : "No suggestions found."}
        </div>
      ) : (
        <div className="space-y-6">
          {groupKeys.map((type) => (
            <section key={type}>
              <div className="mb-2 flex items-center gap-2 border-t border-border pt-4">
                <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
                  {TYPE_LABELS[type] || type}
                </span>
                <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-muted">
                  {grouped[type].length}
                </span>
              </div>

              <div className="space-y-1">
                {grouped[type].map((s) => (
                  <SuggestionRow
                    key={s.id}
                    suggestion={s}
                    isBusy={!!busy[s.id]}
                    onAccept={() => handleAction(s.id, "accept")}
                    onReject={() => handleAction(s.id, "reject")}
                    onSnooze={() => handleAction(s.id, "snooze")}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Inline components ───────────────────────────────────────────── */

function SuggestionRow({
  suggestion: s,
  isBusy,
  onAccept,
  onReject,
  onSnooze,
}: {
  suggestion: ProjectSuggestion;
  isBusy: boolean;
  onAccept: () => void;
  onReject: () => void;
  onSnooze: () => void;
}) {
  const confidencePct = Math.round(s.confidence * 100);
  const confidenceColor =
    confidencePct >= 85
      ? "text-success"
      : confidencePct >= 70
        ? "text-warning"
        : "text-text-muted";

  const projectLink = s.project_id ? (
    <Link
      to={`../projects/${s.project_id}`}
      className="text-xs text-accent-solo hover:underline"
    >
      View project
    </Link>
  ) : null;

  return (
    <div className="flex items-start gap-3 rounded px-3 py-2 transition-colors hover:bg-surface-1">
      {/* Main content */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm text-text">{s.title}</span>
          <span className={`text-[10px] font-medium ${confidenceColor}`}>
            {confidencePct}%
          </span>
        </div>
        {s.rationale && (
          <p className="mt-0.5 truncate text-xs text-text-muted">{s.rationale}</p>
        )}
        <div className="mt-1 flex items-center gap-3">
          {projectLink}
          <span className="text-[10px] text-text-muted">
            {s.created_at ? new Date(s.created_at).toLocaleDateString() : ""}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div className="flex shrink-0 items-center gap-1">
        <button
          onClick={onAccept}
          disabled={isBusy}
          className="rounded border border-border px-2 py-0.5 text-[11px] text-success transition-colors hover:bg-surface-2 disabled:opacity-50"
        >
          Accept
        </button>
        <button
          onClick={onReject}
          disabled={isBusy}
          className="rounded border border-border px-2 py-0.5 text-[11px] text-danger transition-colors hover:bg-surface-2 disabled:opacity-50"
        >
          Reject
        </button>
        <button
          onClick={onSnooze}
          disabled={isBusy}
          className="rounded border border-border px-2 py-0.5 text-[11px] text-text-muted transition-colors hover:bg-surface-2 disabled:opacity-50"
        >
          Snooze
        </button>
      </div>
    </div>
  );
}
