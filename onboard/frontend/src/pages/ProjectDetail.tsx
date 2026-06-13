import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import type { AppName, Project, Milestone, ProjectLink, ProjectAnalysis, TimelineEvent, ProjectAlias, GitCommit } from "../api/types";
import { api } from "../api/client";
import { ProjectStatusBadge } from "../components/ProjectStatusBadge";
import { ProjectCompletionBar } from "../components/ProjectCompletionBar";
import { ProjectHealthPill } from "../components/ProjectHealthPill";
import { RightInspector } from "../components/RightInspector";
import { SegmentedControl } from "../components/SegmentedControl";
import { MilestoneList } from "../components/MilestoneList";
import { ProjectTimeline } from "../components/ProjectTimeline";

type EntityTab = "all" | "record" | "todo" | "decision" | "highlight" | "experiment";

export function ProjectDetail({ appName }: { appName: AppName }) {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [links, setLinks] = useState<ProjectLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [entityTab, setEntityTab] = useState<EntityTab>("all");
  const [analysis, setAnalysis] = useState<ProjectAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [review, setReview] = useState<{ id: string; content: string } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [aliases, setAliases] = useState<ProjectAlias[]>([]);
  const [newAlias, setNewAlias] = useState("");
  const [gitRepo, setGitRepo] = useState("");
  const [gitCommits, setGitCommits] = useState<GitCommit[]>([]);
  const [gitLoading, setGitLoading] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const [p, m, l, t, a] = await Promise.all([
        api.project(appName, id),
        api.milestones(appName, id),
        api.projectLinks(appName, id),
        api.projectTimeline(appName, id).catch(() => [] as TimelineEvent[]),
        api.projectAliases(appName, id).catch(() => [] as ProjectAlias[]),
      ]);
      setProject(p);
      setMilestones(m);
      setLinks(l);
      setTimeline(t);
      setAliases(a);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [appName, id]);

  useEffect(() => { load(); }, [load]);

  const handleAction = async (action: string) => {
    if (!project || !id) return;
    try {
      switch (action) {
        case "complete":
          await api.completeProject(appName, id);
          break;
        case "archive":
          await api.archiveProject(appName, id);
          break;
        case "reactivate":
          await api.reactivateProject(appName, id);
          break;
        case "delete":
          if (!confirm("Delete this project? Linked records will NOT be deleted.")) return;
          await api.deleteProject(appName, id);
          window.location.href = "/projects";
          return;
      }
      load();
    } catch (e) {
      setError(String(e));
    }
  };

  const handleAnalyze = async () => {
    if (!id) return;
    setAnalyzing(true);
    try {
      const result = await api.analyzeProjectState(appName, id);
      setAnalysis(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const handleReview = async () => {
    if (!id) return;
    setReviewing(true);
    try {
      const result = await api.reviewProject(appName, id);
      setReview(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setReviewing(false);
    }
  };

  // Filter links by entity tab
  const filteredLinks = entityTab === "all" ? links : links.filter((l) => l.entity_type === entityTab);

  // Entity tab options (only show tabs that have links)
  const entityTabs = [
    { label: `All (${links.length})`, value: "all" },
    ...(["record", "todo", "decision", "highlight", "experiment"] as const)
      .filter((t) => links.some((l) => l.entity_type === t))
      .map((t) => ({
        label: `${t.charAt(0).toUpperCase() + t.slice(1)}s (${links.filter((l) => l.entity_type === t).length})`,
        value: t,
      })),
  ];

  // Drag-sort state
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);

  const handleDragStart = (e: React.DragEvent, idx: number) => {
    setDragIdx(idx);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(idx));
  };

  const handleDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverIdx(idx);
  };

  const handleDrop = async (e: React.DragEvent, dropIdx: number) => {
    e.preventDefault();
    const fromIdx = dragIdx;
    setDragIdx(null);
    setDragOverIdx(null);
    if (fromIdx === null || fromIdx === dropIdx) return;

    const reordered = [...links];
    const [moved] = reordered.splice(fromIdx, 1);
    reordered.splice(dropIdx, 0, moved);
    setLinks(reordered);

    try {
      await api.reorderProjectLinks(appName, project!.id, reordered.map((l) => l.id));
    } catch {
      load(); // revert on failure
    }
  };

  if (loading) return <div className="h-40 rounded-md bg-surface-2 animate-pulse" />;
  if (!project) return <div className="text-[13px] text-text-muted">Project not found</div>;

  return (
    <div className="flex gap-0 min-h-0">
      {/* Main column */}
      <div className="flex-1 min-w-0 space-y-5">
        {/* Sticky header */}
        <div className="sticky top-14 z-[5] bg-bg/90 backdrop-blur-sm pb-3 -mx-1 px-1">
          <Link to="/projects" className="text-[11px] text-text-muted hover:text-text no-underline">
            &larr; Projects
          </Link>
          <div className="flex items-start justify-between mt-2">
            <div>
              <div className="flex items-center gap-2.5 flex-wrap">
                <h1 className="text-[20px] font-semibold text-text m-0">{project.title}</h1>
                <ProjectStatusBadge status={project.status} />
                <ProjectHealthPill status={project.risk_status} />
              </div>
              {project.description && (
                <p className="mt-1 text-[12px] text-text-secondary m-0">{project.description}</p>
              )}
              {project.target_date && (
                <span className="text-[11px] text-text-muted mt-1 inline-block">
                  Target: {project.target_date}
                </span>
              )}
            </div>
            <div className="flex gap-1.5 shrink-0">
              {project.status === "active" && (
                <>
                  <button onClick={() => handleAction("complete")} className="px-2.5 py-1 rounded-md bg-success/15 text-success text-[11px] cursor-pointer border-0 font-medium">Complete</button>
                  <button onClick={() => handleAction("archive")} className="px-2.5 py-1 rounded-md bg-surface-3 text-text-secondary text-[11px] cursor-pointer border-0">Archive</button>
                </>
              )}
              {(project.status === "completed" || project.status === "archived") && (
                <button onClick={() => handleAction("reactivate")} className="px-2.5 py-1 rounded-md bg-info/15 text-info text-[11px] cursor-pointer border-0 font-medium">Reactivate</button>
              )}
              <button onClick={handleReview} disabled={reviewing} className="px-2.5 py-1 rounded-md bg-surface-3 text-text-secondary text-[11px] cursor-pointer border-0 disabled:opacity-50">
                {reviewing ? "Generating..." : "Review"}
              </button>
              <button onClick={() => handleAction("delete")} className="px-2.5 py-1 rounded-md bg-danger/10 text-danger text-[11px] cursor-pointer border-0">Delete</button>
            </div>
          </div>
        </div>

        {error && <div className="text-[12px] text-danger">{error}</div>}

        {/* Progress */}
        <div className="flex items-center gap-4 py-3 border-t border-b border-border">
          <div className="flex-1">
            <ProjectCompletionBar pct={project.completion_pct} />
          </div>
          <span className="text-[12px] text-text-muted tabular-nums">
            {project.completion_pct !== null ? `${project.completion_pct}%` : "Not quantified"}
          </span>
          <span className="text-[11px] text-text-muted">
            {project.completion_source !== "none" ? `via ${project.completion_source}` : ""}
          </span>
          <div className="flex gap-4 text-[11px] text-text-muted">
            <span>{project.completed_milestone_count}/{project.milestone_count} ms</span>
            <span>{project.linked_record_count} rec</span>
            <span>{project.completed_linked_todo_count}/{project.linked_todo_count} todo</span>
          </div>
        </div>

        {/* Milestones */}
        <section>
          <h2 className="text-[13px] font-medium text-text-secondary mb-2">Milestones</h2>
          <MilestoneList app={appName} projectId={project.id} milestones={milestones} onChange={load} />
        </section>

        {/* Timeline */}
        <section>
          <h2 className="text-[13px] font-medium text-text-secondary mb-2">Timeline</h2>
          <ProjectTimeline events={timeline} />
        </section>

        {/* Project Review */}
        {review && (
          <section className="border-t border-border pt-4">
            <h2 className="text-[13px] font-medium text-text-secondary mb-2">Project Review</h2>
            <div className="text-[12px] text-text leading-relaxed whitespace-pre-wrap bg-surface-2 rounded-md p-3 border border-border-subtle">
              {review.content}
            </div>
          </section>
        )}

        {/* Linked Entities with tabs */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[13px] font-medium text-text-secondary m-0">Linked Entities</h2>
          </div>
          {links.length > 0 && entityTabs.length > 1 && (
            <div className="mb-3">
              <SegmentedControl
                options={entityTabs}
                value={entityTab}
                onChange={(v) => setEntityTab(v as EntityTab)}
              />
            </div>
          )}
          {filteredLinks.length === 0 ? (
            <p className="text-[11px] text-text-muted">No entities linked yet.</p>
          ) : (
            <div className="border-t border-border">
              {filteredLinks.map((l, idx) => (
                <div
                  key={l.id}
                  draggable={entityTab === "all"}
                  onDragStart={(e) => handleDragStart(e, idx)}
                  onDragOver={(e) => handleDragOver(e, idx)}
                  onDrop={(e) => handleDrop(e, idx)}
                  onDragEnd={() => { setDragIdx(null); setDragOverIdx(null); }}
                  className={`flex items-center justify-between px-2 py-2 border-b border-border-subtle last:border-0 hover:bg-surface-2 transition-colors ${
                    dragOverIdx === idx ? "border-t-2 border-t-accent-solo" : ""
                  } ${dragIdx === idx ? "opacity-40" : ""} ${
                    entityTab === "all" ? "cursor-grab active:cursor-grabbing" : ""
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    {entityTab === "all" && (
                      <span className="text-text-muted text-[10px] shrink-0 select-none">⠿</span>
                    )}
                    <span className="px-1.5 py-0.5 rounded bg-surface-3 text-[10px] text-text-muted shrink-0 uppercase tracking-wide">
                      {l.entity_type}
                    </span>
                    <span className="text-[12px] text-text truncate" title={l.entity_title || l.entity_id}>
                      {l.entity_title || <span className="font-mono text-text-muted">{l.entity_id.slice(0, 8)}</span>}
                    </span>
                    {l.status !== "active" && (
                      <span className="text-[10px] text-text-muted capitalize shrink-0">({l.status})</span>
                    )}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {l.status === "pending" && (
                      <>
                        <button onClick={() => { api.acceptProjectLink(appName, l.id).then(load); }} className="text-[11px] text-success cursor-pointer bg-transparent border-0 hover:underline">Accept</button>
                        <button onClick={() => { api.rejectProjectLink(appName, l.id).then(load); }} className="text-[11px] text-danger cursor-pointer bg-transparent border-0 hover:underline">Reject</button>
                      </>
                    )}
                    <button onClick={() => { api.deleteProjectLink(appName, l.id).then(load); }} className="text-[11px] text-text-muted hover:text-danger cursor-pointer bg-transparent border-0">Unlink</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Right Inspector */}
      <RightInspector title="Project Info">
        {/* AI Brief & Next Action */}
        {analysis && (
          <div className="space-y-2.5 pb-3 border-b border-border">
            {analysis.summary && (
              <div>
                <div className="text-[11px] text-text-muted mb-1">AI Summary</div>
                <p className="text-[12px] text-text m-0 leading-relaxed">{analysis.summary}</p>
              </div>
            )}
            {analysis.next_action && (
              <div>
                <div className="text-[11px] text-text-muted mb-1">Next Action</div>
                <div className="text-[12px] text-text font-medium">{analysis.next_action}</div>
              </div>
            )}
          </div>
        )}

        {/* Analyze button */}
        <div className="pb-3 border-b border-border">
          <button
            onClick={handleAnalyze}
            disabled={analyzing}
            className="w-full px-2.5 py-1.5 rounded-md bg-surface-3 text-text-secondary text-[11px] cursor-pointer border-0 hover:bg-surface-hover transition-colors disabled:opacity-50"
          >
            {analyzing ? "Analyzing..." : analysis ? "Refresh Analysis" : "Analyze State"}
          </button>
        </div>

        {/* Health Signals */}
        {analysis && analysis.signals.length > 0 && (
          <div className="pb-3 border-b border-border">
            <div className="text-[11px] text-text-muted mb-2">Health Signals</div>
            <div className="space-y-1.5">
              {analysis.signals.map((sig, i) => (
                <div key={i} className="flex items-start gap-1.5 text-[11px]">
                  <span className={`shrink-0 mt-0.5 w-1.5 h-1.5 rounded-full ${
                    sig.severity === "critical" ? "bg-danger" :
                    sig.severity === "warning" ? "bg-warning" : "bg-info"
                  }`} />
                  <span className="text-text-secondary">{sig.summary}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Stats */}
        <div className="space-y-3 text-[12px]">
          <div className="flex justify-between">
            <span className="text-text-muted">Activity (7d)</span>
            <span className="tabular-nums">{project.activity_7d}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">Activity (30d)</span>
            <span className="tabular-nums">{project.activity_30d}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">Priority</span>
            <span className="capitalize">{project.priority}</span>
          </div>
          {project.tags && (
            <div className="flex justify-between">
              <span className="text-text-muted">Tags</span>
              <span className="text-right">{project.tags}</span>
            </div>
          )}
          {project.last_activity_at && (
            <div className="flex justify-between">
              <span className="text-text-muted">Last activity</span>
              <span>{new Date(project.last_activity_at).toLocaleDateString()}</span>
            </div>
          )}
        </div>

        {/* Aliases */}
        <div className="border-t border-border pt-3">
          <div className="text-[11px] text-text-muted mb-1.5">Aliases</div>
          {aliases.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-1.5">
              {aliases.map((a) => (
                <span key={a.id} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-surface-3 text-[11px] text-text-secondary">
                  {a.alias}
                  <button
                    onClick={async () => {
                      await api.deleteProjectAlias(appName, a.id);
                      setAliases((prev) => prev.filter((x) => x.id !== a.id));
                    }}
                    className="text-text-muted hover:text-danger cursor-pointer bg-transparent border-0 p-0 text-[10px] leading-none"
                  >&times;</button>
                </span>
              ))}
            </div>
          )}
          <div className="flex gap-1">
            <input
              type="text"
              placeholder="Add alias..."
              value={newAlias}
              onChange={(e) => setNewAlias(e.target.value)}
              onKeyDown={async (e) => {
                if (e.key === "Enter" && newAlias.trim() && id) {
                  const created = await api.createProjectAlias(appName, id, newAlias.trim());
                  setAliases((prev) => [...prev, created]);
                  setNewAlias("");
                }
              }}
              className="flex-1 min-w-0 px-2 py-1 rounded bg-surface-2 border border-border text-[11px] text-text placeholder-text-muted"
            />
          </div>
        </div>

        {/* Git Context */}
        <div className="border-t border-border pt-3">
          <div className="text-[11px] text-text-muted mb-1.5">Git Activity</div>
          <div className="flex gap-1 mb-1.5">
            <input
              type="text"
              placeholder="/path/to/repo"
              value={gitRepo}
              onChange={(e) => setGitRepo(e.target.value)}
              className="flex-1 min-w-0 px-2 py-1 rounded bg-surface-2 border border-border text-[11px] text-text placeholder-text-muted"
            />
            <button
              onClick={async () => {
                if (!gitRepo.trim() || !id) return;
                setGitLoading(true);
                try {
                  const commits = await api.gitContext(appName, id, gitRepo.trim());
                  setGitCommits(commits);
                } catch { setGitCommits([]); }
                setGitLoading(false);
              }}
              disabled={gitLoading}
              className="px-2 py-1 rounded bg-surface-3 text-text-secondary text-[10px] cursor-pointer border-0 disabled:opacity-50"
            >
              {gitLoading ? "..." : "Sync"}
            </button>
          </div>
          {gitCommits.length > 0 && (
            <div className="space-y-1 max-h-40 overflow-y-auto">
              {gitCommits.map((c, i) => (
                <div key={i} className="text-[11px] border-b border-border-subtle pb-1 last:border-0">
                  <div className="text-text truncate" title={c.subject}>{c.subject}</div>
                  <div className="text-text-muted text-[10px]">{c.hash} · {c.author} · {c.date}</div>
                </div>
              ))}
            </div>
          )}
          {gitCommits.length === 0 && gitRepo && !gitLoading && (
            <div className="text-[10px] text-text-muted">No matching commits found.</div>
          )}
        </div>

        {/* Wolo-specific fields */}
        {project.stakeholders && (
          <div className="border-t border-border pt-3">
            <div className="text-[11px] text-text-muted mb-1">Stakeholders</div>
            <div className="text-[12px] text-text">{project.stakeholders}</div>
          </div>
        )}
        {project.success_criteria && (
          <div className="border-t border-border pt-3">
            <div className="text-[11px] text-text-muted mb-1">Success Criteria</div>
            <div className="text-[12px] text-text">{project.success_criteria}</div>
          </div>
        )}

        {/* Open blockers (wolo) */}
        {project.open_blocker_count > 0 && (
          <div className="border-t border-border pt-3">
            <div className="text-[11px] text-text-muted mb-1">Open Blockers</div>
            <div className="text-lg font-medium text-danger tabular-nums">{project.open_blocker_count}</div>
          </div>
        )}

        {/* Dates */}
        <div className="border-t border-border pt-3 space-y-2 text-[12px]">
          {project.start_date && (
            <div className="flex justify-between">
              <span className="text-text-muted">Started</span>
              <span>{project.start_date}</span>
            </div>
          )}
          {project.completed_at && (
            <div className="flex justify-between">
              <span className="text-text-muted">Completed</span>
              <span>{new Date(project.completed_at).toLocaleDateString()}</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-text-muted">Created</span>
            <span>{new Date(project.created_at).toLocaleDateString()}</span>
          </div>
        </div>
      </RightInspector>
    </div>
  );
}
