import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Todo } from "../api/types";
import type { AppName, Project, ProjectBrief as ProjectBriefData } from "../api/types";
import {
  ActivityHeatmap,
  EmotionPieChart,
  ModelCallUsageChart,
  ModelTokenUsageChart,
  TagBarChart,
  formatTokenAmount,
  tokenPalette,
} from "../components/Charts";
import { ProjectRowCard } from "../components/ProjectRowCard";
import { SciFiBackground } from "../components/SciFiBackground";
import { StatsCard } from "../components/StatsCard";
import { LIVE_REFRESH_INTERVAL_MS, useApi } from "../hooks/useApi";

/* ─── Date / greeting helpers ─────────────────────────────────── */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const SOLAR_TERMS: [number, number, string][] = [
  [1, 5, "Minor Cold"], [1, 20, "Major Cold"],
  [2, 4, "Start of Spring"], [2, 19, "Rain Water"],
  [3, 5, "Awakening of Insects"], [3, 20, "Spring Equinox"],
  [4, 4, "Clear and Bright"], [4, 20, "Grain Rain"],
  [5, 5, "Start of Summer"], [5, 21, "Lesser Fullness"],
  [6, 5, "Grain in Ear"], [6, 21, "Summer Solstice"],
  [7, 7, "Lesser Heat"], [7, 22, "Greater Heat"],
  [8, 7, "Start of Autumn"], [8, 23, "End of Heat"],
  [9, 7, "White Dew"], [9, 23, "Autumnal Equinox"],
  [10, 8, "Cold Dew"], [10, 23, "Frost's Descent"],
  [11, 7, "Start of Winter"], [11, 22, "Minor Snow"],
  [12, 7, "Major Snow"], [12, 22, "Winter Solstice"],
];

const FIXED_OCCASIONS: Record<string, string> = {
  "1-1": "New Year's Day",
  "2-14": "Valentine's Day",
  "3-8": "Women's Day",
  "4-1": "April Fools'",
  "5-1": "Labour Day",
  "5-4": "Youth Day",
  "6-1": "Children's Day",
  "9-10": "Teachers' Day",
  "10-1": "National Day",
  "12-25": "Christmas",
  "12-31": "New Year's Eve",
};
for (const [m, d, name] of SOLAR_TERMS) FIXED_OCCASIONS[`${m}-${d}`] = name;

function greeting(): string {
  const h = new Date().getHours();
  if (h < 6) return "Still up";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/* ─── Clock (compact, for hero header) ───────────────────────── */

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const key = `${now.getMonth() + 1}-${now.getDate()}`;
  const occasion = FIXED_OCCASIONS[key] ?? null;
  const time = now.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const dateStr = `${MONTHS[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`;

  return (
    <div className="text-right">
      <div className="font-mono text-[13px] text-text tabular-nums tracking-wider">{time}</div>
      <div className="text-[11px] text-text-muted mt-0.5">
        {dateStr}
        {occasion && <span className="normal-case ml-1 text-accent-solo">{occasion}</span>}
      </div>
    </div>
  );
}

/* ─── QuickLinks ──────────────────────────────────────────────── */

const QUICK_LINKS = [
  { to: "/entries", icon: "&#x229E;", label: "New Entry" },
  { to: "/projects", icon: "&#x25A6;", label: "Projects" },
  { to: "/reports", icon: "&#x25A4;", label: "Reports" },
  { to: "/chat", icon: "&#x2299;", label: "Chat" },
] as const;

/* ─── ProjectBrief (main column) ──────────────────────────────── */

function ProjectBrief({ app }: { app: AppName }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [brief, setBrief] = useState<ProjectBriefData | null>(null);

  useEffect(() => {
    api.projects(app, { status: "active", limit: 8 }).then(setProjects).catch(() => {});
    api.projectBrief(app).then(setBrief).catch(() => {});
  }, [app]);

  const hasRisk = brief && brief.at_risk.length > 0;
  const hasAttention = brief && brief.attention.length > 0;

  if (projects.length === 0 && !hasRisk && !hasAttention) {
    return (
      <div className="flex items-center justify-between py-8 px-4 rounded-lg border border-border bg-surface-1">
        <span className="text-[13px] text-text-muted">No active projects yet.</span>
        <Link
          to="/projects"
          className="text-[12px] text-accent-solo hover:underline no-underline font-medium"
        >
          Create one &rarr;
        </Link>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      {/* Needs Attention banner */}
      {(hasRisk || hasAttention) && (
        <div className="mb-3 border border-danger/20 rounded-md px-3 py-2 bg-danger/5">
          <div className="text-[11px] font-medium uppercase tracking-wide text-danger mb-1">
            Needs Attention
          </div>
          <div className="space-y-0.5">
            {[...(brief?.at_risk ?? []), ...(brief?.attention ?? [])]
              .slice(0, 3)
              .map((p) => (
                <Link
                  key={p.id}
                  to={`/projects/${p.id}`}
                  className="block text-[12px] text-text-secondary hover:text-text no-underline truncate"
                >
                  {p.risk_status === "at_risk" ? "\u26A0 " : "\u25CF "}
                  {p.title}
                </Link>
              ))}
          </div>
        </div>
      )}

      {/* Project rows */}
      <div className="rounded-lg border border-border bg-surface-1 overflow-hidden">
        {projects.map((p) => (
          <ProjectRowCard key={p.id} project={p} />
        ))}
      </div>
    </div>
  );
}

/* ─── Section wrapper ─────────────────────────────────────────── */

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-medium text-text-secondary m-0">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  );
}

function SectionLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="text-[11px] text-text-muted hover:text-text no-underline">
      {children}
    </Link>
  );
}



/* ─── Pending Todos Panel ─────────────────────────────────────── */

function PendingTodosPanel({ app }: { app: AppName }) {
  const { data, error, loading } = useApi(
    () => api.todos(app, { status: "pending" }).then((todos) =>
      todos.filter((t: Todo) => t.status === "pending" || t.status === "in_progress")
    ),
    [app],
    { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS },
  );
  const [expanded, setExpanded] = useState(false);

  if (!data || data.length === 0) return null;

  const visible = expanded ? data : data.slice(0, 3);

  return (
    <div className="rounded-lg border border-warning/20 bg-warning/5 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2.5 text-left hover:bg-warning/10 transition-colors"
      >
        <span className="text-[12px] font-medium text-warning">
          {data.length} pending todo{data.length !== 1 ? "s" : ""}
        </span>
        <span className="text-[11px] text-text-muted">
          {expanded ? "Collapse" : "Show all"}
        </span>
      </button>
      <div className="divide-y divide-border/50">
        {visible.map((todo: Todo) => (
          <div key={todo.id} className="flex items-center gap-3 px-4 py-2">
            <span className={`shrink-0 w-1.5 h-1.5 rounded-full ${todo.status === "in_progress" ? "bg-accent-solo" : "bg-text-muted"}`} />
            <span className="text-[12px] text-text truncate flex-1">{todo.title}</span>
            {todo.project && (
              <span className="text-[10px] text-text-muted shrink-0">{todo.project}</span>
            )}
            {todo.priority === "high" && (
              <span className="text-[10px] text-danger shrink-0">high</span>
            )}
          </div>
        ))}
      </div>
      {data.length > 3 && !expanded && (
        <div className="px-4 py-1.5 text-[11px] text-text-muted">
          +{data.length - 3} more
        </div>
      )}
    </div>
  );
}

/* ─── Dashboard ───────────────────────────────────────────────── */

export function Dashboard({ appName }: { appName: AppName }) {
  const { data, error, loading } = useApi(
    () => api.stats(appName),
    [appName],
    { refreshIntervalMs: LIVE_REFRESH_INTERVAL_MS },
  );

  const accent = appName === "solo" ? "#d4a574" : "#5eead4";

  const focusCards = useMemo(() => {
    if (!data) return [];
    const items: { label: string; value: number; icon: string; accent?: string; linkTo?: string }[] = [
      { label: "Records", value: data.total_records, icon: "\u25C7" },
      { label: "This Week", value: data.this_week_records, icon: "\u25B3" },
      {
        label: "Pending",
        value: data.pending_todos,
        icon: "\u2610",
        accent: data.pending_todos > 0 ? "#fbbf24" : undefined,
        linkTo: data.pending_todos > 0 ? "/todos" : undefined,
      },
      { label: "Model Calls", value: data.llm_total_calls, icon: "\u2299" },
    ];
    if (appName === "wolo") {
      if (data.open_blockers) {
        items.push({ label: "Blockers", value: data.open_blockers, icon: "\u26A0", accent: "#f87171", linkTo: "/highlights" });
      }
      items.push({ label: "Decisions", value: data.total_decisions ?? 0, icon: "\u29EB" });
    }
    return items;
  }, [data, appName]);

  const modelColorMap = useMemo(() => {
    const models = Array.from(
      new Set((data?.llm_monthly_tokens ?? []).map((t: any) => t.model)),
    ).sort();
    const map: Record<string, string> = {};
    models.forEach((m, i) => {
      map[m] = tokenPalette[i % tokenPalette.length].input;
    });
    return map;
  }, [data]);

  /* ── Loading / error states ── */
  if (loading) {
    return (
      <div className="space-y-5 animate-pulse">
        <div className="h-24 rounded-lg bg-surface-2" />
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-20 rounded-lg bg-surface-2" />
          ))}
        </div>
        <div className="h-64 rounded-lg bg-surface-2" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="border border-danger/30 rounded-lg bg-danger/5 p-5 text-[13px] text-text">
        {error ?? "Failed to load dashboard."}
      </div>
    );
  }

  /* ── Render ── */
  return (
    <>
    <SciFiBackground accent={accent} />
    <div className="relative space-y-6" style={{ zIndex: 1 }}>
      {/* Zone 1: Hero Header */}
      <header className="flex items-start justify-between">
        <div>
          <h2 className="text-[28px] font-serif text-text m-0 leading-tight">
            {greeting()}
          </h2>
          <p className="text-[12px] text-text-muted mt-1 m-0">
            {data.this_week_records} records this week
            {data.pending_todos > 0 && (
              <span className="text-warning"> &middot; {data.pending_todos} pending</span>
            )}
          </p>
        </div>
        <div className="flex items-start gap-4">
          <Clock />
          <div className="text-right">
            <div className="text-[11px] text-text-muted">Model</div>
            <div className="text-[12px] font-medium truncate max-w-[200px]" title={data.current_model} style={{ color: modelColorMap[data.current_model] ?? accent }}>
              {data.current_model || "\u2014"}
            </div>
            {data.vision_model && (
              <div className="text-[11px] truncate max-w-[200px] mt-0.5" title={data.vision_model} style={{ color: modelColorMap[data.vision_model] ?? "#a78bfa" }}>
                {data.vision_model}
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Quick Actions */}
      <div className="flex items-center gap-2 flex-wrap">
        {QUICK_LINKS.map((lnk) => (
          <Link
            key={lnk.to}
            to={lnk.to}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border-subtle bg-surface-1 hover:bg-surface-2 no-underline transition-colors text-[12px]"
          >
            <span className="opacity-50" dangerouslySetInnerHTML={{ __html: lnk.icon }} />
            <span className="text-text-secondary">{lnk.label}</span>
          </Link>
        ))}
      </div>

      {/* Zone 2: Stat Cards */}
      <div
        className="grid grid-cols-2 gap-3"
        style={{ gridTemplateColumns: `repeat(${Math.max(focusCards.length, 2)}, minmax(0, 1fr))` }}
      >
        {focusCards.map((card) => (
          <StatsCard
            key={card.label}
            label={card.label}
            value={card.value}
            icon={card.icon}
            accent={card.accent}
            linkTo={card.linkTo}
          />
        ))}
      </div>

      {/* Zone 2b: Pending Todos */}
      {data.pending_todos > 0 && (
        <PendingTodosPanel app={appName} />
      )}

      {/* Zone 3: Projects */}
      <Section
        title="Projects"
        action={<SectionLink to="/projects">View all</SectionLink>}
      >
        <ProjectBrief app={appName} />
      </Section>

      {/* Zone 4: Insights */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="p-4 rounded-lg border border-border bg-surface-1">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-medium text-text-secondary m-0">Activity</h3>
            <SectionLink to="/records">View all</SectionLink>
          </div>
          <ActivityHeatmap data={data.daily_counts} />
        </div>
        <div className="p-4 rounded-lg border border-border bg-surface-1 flex flex-col min-h-0">
          <h3 className="text-[13px] font-medium text-text-secondary m-0 mb-3 shrink-0">Emotions</h3>
          <EmotionPieChart data={data.emotion_distribution} />
        </div>
        <div className="p-4 rounded-lg border border-border bg-surface-1">
          <h3 className="text-[13px] font-medium text-text-secondary m-0 mb-3">Top Tags</h3>
          <TagBarChart data={data.top_tags} />
        </div>
      </div>

      {/* Zone 5: Usage */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="p-4 rounded-lg border border-border bg-surface-1">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-medium text-text-secondary m-0">Token Usage</h3>
            <div className="flex items-center gap-3 text-[11px] font-mono">
              <span className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-success animate-[pulse-dot_1.5s_ease-in-out_infinite]" />
                live
              </span>
              <span>
                <span className="text-text-muted">in </span>
                <span className="tabular-nums text-text">
                  {formatTokenAmount(data.llm_daily_input_tokens)}
                </span>
              </span>
              <span>
                <span className="text-text-muted">out </span>
                <span className="tabular-nums text-text">
                  {formatTokenAmount(data.llm_daily_output_tokens)}
                </span>
              </span>
            </div>
          </div>
          <ModelTokenUsageChart
            data={data.llm_monthly_tokens}
            startDate={data.llm_monthly_start_date}
            endDate={data.llm_monthly_end_date}
          />
        </div>

        <div className="p-4 rounded-lg border border-border bg-surface-1">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-medium text-text-secondary m-0">Model Usage</h3>
            <div className="flex items-center gap-3 text-[11px] font-mono">
              <span className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-success animate-[pulse-dot_1.5s_ease-in-out_infinite]" />
                live
              </span>
              <span>
                <span className="text-text-muted">calls </span>
                <span className="tabular-nums text-text">
                  {data.llm_daily_total_calls.toLocaleString()}
                </span>
              </span>
            </div>
          </div>
          <ModelCallUsageChart
            data={data.llm_monthly_model_calls}
            startDate={data.llm_monthly_start_date}
            endDate={data.llm_monthly_end_date}
          />
        </div>
      </div>
    </div>
    </>
  );
}
