import { useCallback, useEffect, useMemo, useState } from "react";
import type { AppName, Project, Milestone, ProjectTemplate } from "../api/types";
import { api } from "../api/client";
import { ProjectRowCard } from "../components/ProjectRowCard";
import { ProjectBoardCard } from "../components/ProjectBoardCard";
import { ProjectHealthPill } from "../components/ProjectHealthPill";
import { SegmentedControl } from "../components/SegmentedControl";
import { EmptyState } from "../components/EmptyState";
import { ProjectCalendar } from "../components/ProjectCalendar";
import { Link } from "react-router-dom";

type ViewMode = "board" | "list" | "calendar";

// Module-level scan state — survives component unmount/remount
let _scanPromise: Promise<{ created: number }> | null = null;
let _scanResult: string | null = null;
let _scanAppName: AppName | null = null;

function daysSince(iso: string): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

/** Classify a project into a board column. */
function boardColumn(p: Project): string {
  if (p.status === "completed") return "done";
  if (p.status === "archived") return "done";
  if (p.risk_status === "at_risk") return "at_risk";
  if (p.risk_status === "attention") return "attention";
  const d = daysSince(p.last_activity_at);
  if (d !== null && d > 21) return "dormant";
  return "now";
}

const BOARD_COLUMNS = [
  { key: "now", label: "Now", desc: "Active & progressing" },
  { key: "attention", label: "Attention", desc: "Near deadline or slowing" },
  { key: "at_risk", label: "At Risk", desc: "Overdue or blocked" },
  { key: "dormant", label: "Dormant", desc: "No recent activity" },
  { key: "done", label: "Done", desc: "Completed / Archived" },
];

export function Projects({ appName }: { appName: AppName }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<ViewMode>("board");
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newDate, setNewDate] = useState("");
  const [newPriority, setNewPriority] = useState("medium");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [error, setError] = useState("");
  const [scanning, setScanning] = useState(() => _scanAppName === appName && _scanPromise !== null);
  const [scanResult, setScanResult] = useState<string | null>(() => _scanAppName === appName ? _scanResult : null);
  const [templates, setTemplates] = useState<ProjectTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [milestonesByProject, setMilestonesByProject] = useState<Record<string, Milestone[]>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.projects(appName, { status: "all" });
      setProjects(data);
      // Load milestones for active projects (for calendar view)
      const active = data.filter((p) => p.status === "active");
      const msResults = await Promise.all(
        active.map(async (p) => ({ id: p.id, ms: await api.milestones(appName, p.id) }))
      );
      const msMap: Record<string, Milestone[]> = {};
      for (const { id, ms } of msResults) msMap[id] = ms;
      setMilestonesByProject(msMap);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [appName]);

  useEffect(() => { load(); }, [load]);

  // Re-attach to in-flight scan on mount
  useEffect(() => {
    if (_scanPromise && _scanAppName === appName) {
      setScanning(true);
      _scanPromise
        .then((res) => {
          const msg = res.created > 0
            ? `Found ${res.created} project candidate${res.created > 1 ? "s" : ""}. Check your Inbox.`
            : "No new project candidates found.";
          _scanResult = msg;
          setScanResult(msg);
        })
        .catch(() => {
          _scanResult = "Scan failed. Try again later.";
          setScanResult(_scanResult);
        })
        .finally(() => {
          _scanPromise = null;
          setScanning(false);
        });
    }
  }, [appName]);

  // Load templates when create form opens
  useEffect(() => {
    if (showCreate && templates.length === 0) {
      api.projectTemplates(appName).then(setTemplates).catch(() => {});
    }
  }, [showCreate, appName, templates.length]);

  const activeTemplate = templates.find((t) => t.id === selectedTemplate);

  const handleSelectTemplate = (tplId: string) => {
    setSelectedTemplate(tplId);
    const tpl = templates.find((t) => t.id === tplId);
    if (tpl) {
      setNewDesc(tpl.description);
      setNewPriority(tpl.priority);
    } else {
      setNewDesc("");
      setNewPriority("medium");
    }
  };

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    try {
      await api.createProject(appName, {
        title: newTitle.trim(),
        description: newDesc,
        target_date: newDate,
        priority: newPriority,
        template: selectedTemplate,
      });
      setShowCreate(false);
      setNewTitle(""); setNewDesc(""); setNewDate(""); setNewPriority("medium"); setSelectedTemplate("");
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleScan = async () => {
    setScanning(true);
    setScanResult(null);
    _scanAppName = appName;
    _scanResult = null;
    _scanPromise = api.scanProjects(appName);
    try {
      const res = await _scanPromise;
      const msg = res.created > 0
        ? `Found ${res.created} project candidate${res.created > 1 ? "s" : ""}. Check your Inbox.`
        : "No new project candidates found.";
      _scanResult = msg;
      setScanResult(msg);
    } catch {
      _scanResult = "Scan failed. Try again later.";
      setScanResult(_scanResult);
    } finally {
      _scanPromise = null;
      setScanning(false);
    }
  };

  // Filtered projects
  const filtered = useMemo(() => {
    let result = projects;
    if (statusFilter !== "all") {
      result = result.filter((p) => p.status === statusFilter);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.description.toLowerCase().includes(q) ||
          p.tags.toLowerCase().includes(q)
      );
    }
    return result;
  }, [projects, statusFilter, search]);

  // Needs Attention: at_risk + attention active projects
  const needsAttention = useMemo(
    () => filtered.filter((p) => p.status === "active" && (p.risk_status === "at_risk" || p.risk_status === "attention")),
    [filtered]
  );

  // Board columns
  const boardGroups = useMemo(() => {
    const groups: Record<string, Project[]> = {};
    for (const col of BOARD_COLUMNS) groups[col.key] = [];
    for (const p of filtered) {
      const col = boardColumn(p);
      (groups[col] ??= []).push(p);
    }
    return groups;
  }, [filtered]);

  return (
    <div className="space-y-5">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-[22px] font-semibold text-text m-0">Projects</h1>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Search projects..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="px-3 py-1.5 rounded-md bg-surface-2 border border-border text-[12px] text-text placeholder-text-muted w-44"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-2 py-1.5 rounded-md bg-surface-2 border border-border text-[12px] text-text"
          >
            <option value="all">All status</option>
            <option value="active">Active</option>
            <option value="completed">Completed</option>
            <option value="archived">Archived</option>
          </select>
          <SegmentedControl
            options={[{ label: "Board", value: "board" }, { label: "List", value: "list" }, { label: "Calendar", value: "calendar" }]}
            value={view}
            onChange={(v) => setView(v as ViewMode)}
          />
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="px-3 py-1.5 rounded-md bg-accent-solo/15 text-accent-solo text-[12px] hover:bg-accent-solo/25 cursor-pointer border-0 font-medium"
          >
            + New
          </button>
          <button
            onClick={handleScan}
            disabled={scanning}
            className="px-3 py-1.5 rounded-md bg-surface-2 border border-border text-[12px] text-text-secondary hover:bg-surface-3 cursor-pointer disabled:opacity-50"
          >
            {scanning ? "Scanning..." : "Scan Records"}
          </button>
          <Link
            to="/projects/inbox"
            className="px-3 py-1.5 rounded-md bg-surface-2 border border-border text-[12px] text-text-secondary hover:bg-surface-3 no-underline"
          >
            Inbox
          </Link>
        </div>
      </div>

      {error && <div className="text-[12px] text-danger">{error}</div>}
      {scanResult && (
        <div className="text-[12px] text-info">{scanResult}</div>
      )}

      {/* Create form (inline, lightweight) */}
      {showCreate && (
        <div className="border-b border-border pb-4 space-y-2">
          {templates.length > 0 && (
            <select
              value={selectedTemplate}
              onChange={(e) => handleSelectTemplate(e.target.value)}
              className="w-full px-3 py-2 rounded-md bg-surface-2 border border-border text-[13px] text-text"
            >
              <option value="">No template</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </select>
          )}
          <input
            type="text"
            placeholder="Project title"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            className="w-full px-3 py-2 rounded-md bg-surface-2 border border-border text-[13px] text-text placeholder-text-muted"
            autoFocus
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            className="w-full px-3 py-2 rounded-md bg-surface-2 border border-border text-[13px] text-text placeholder-text-muted"
          />
          {activeTemplate && activeTemplate.milestones.length > 0 && (
            <div className="text-[11px] text-text-muted">
              Milestones: {activeTemplate.milestones.join(" → ")}
            </div>
          )}
          <div className="flex gap-3 items-center">
            <input
              type="date"
              value={newDate}
              onChange={(e) => setNewDate(e.target.value)}
              className="px-3 py-2 rounded-md bg-surface-2 border border-border text-[13px] text-text"
            />
            <select
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)}
              className="px-3 py-2 rounded-md bg-surface-2 border border-border text-[13px] text-text"
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <button onClick={handleCreate} className="px-4 py-1.5 rounded-md bg-accent-solo/15 text-accent-solo text-[12px] cursor-pointer border-0 font-medium">Create</button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-1.5 rounded-md bg-surface-3 text-text-secondary text-[12px] cursor-pointer border-0">Cancel</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="h-32 rounded-md bg-surface-2 animate-pulse" />
      ) : projects.length === 0 ? (
        <EmptyState
          title="No projects yet"
          description="Create your first project to begin tracking milestones and linking records."
        />
      ) : (
        <>
          {/* Needs Attention */}
          {needsAttention.length > 0 && (
            <section>
              <h2 className="text-[13px] font-medium text-warning mb-2">
                Needs Attention ({needsAttention.length})
              </h2>
              <div className="border-t border-border">
                {needsAttention.map((p) => (
                  <ProjectRowCard key={p.id} project={p} />
                ))}
              </div>
            </section>
          )}

          {view === "board" ? (
            /* Board view: column-based by operational status */
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4">
              {BOARD_COLUMNS.map((col) => {
                const items = boardGroups[col.key] ?? [];
                if (items.length === 0 && col.key === "done") return null;
                return (
                  <div key={col.key}>
                    <div className="flex items-baseline gap-2 mb-2">
                      <h3 className="text-[12px] font-medium text-text-secondary m-0">{col.label}</h3>
                      <span className="text-[11px] text-text-muted tabular-nums">{items.length}</span>
                    </div>
                    <div className="text-[10px] text-text-muted mb-2">{col.desc}</div>
                    <div>
                      {items.map((p) => (
                        <ProjectBoardCard key={p.id} project={p} />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : view === "list" ? (
            /* List view: dense table */
            <div className="border-t border-border">
              <div className="grid grid-cols-[1fr_80px_80px_70px_90px_70px] gap-2 px-3 py-2 text-[11px] font-medium text-text-muted border-b border-border">
                <span>Project</span>
                <span>Status</span>
                <span>Progress</span>
                <span>Milestones</span>
                <span>Target</span>
                <span>Risk</span>
              </div>
              {filtered.map((p) => (
                <Link
                  key={p.id}
                  to={`/projects/${p.id}`}
                  className="grid grid-cols-[1fr_80px_80px_70px_90px_70px] gap-2 px-3 py-2.5 text-[12px] border-b border-border-subtle last:border-0 hover:bg-surface-2 no-underline transition-colors items-center"
                >
                  <span className="text-text font-medium truncate">{p.title}</span>
                  <span className="text-text-muted capitalize">{p.status}</span>
                  <span className="text-text-muted tabular-nums">
                    {p.completion_pct !== null ? `${p.completion_pct}%` : "—"}
                  </span>
                  <span className="text-text-muted tabular-nums">
                    {p.completed_milestone_count}/{p.milestone_count}
                  </span>
                  <span className="text-text-muted">{p.target_date || "—"}</span>
                  <ProjectHealthPill status={p.risk_status} />
                </Link>
              ))}
            </div>
          ) : (
            /* Calendar view */
            <div className="border-t border-border pt-3">
              <ProjectCalendar projects={filtered} milestonesByProject={milestonesByProject} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
