import { useMemo, useState } from "react";
import type { Project, Milestone } from "../api/types";

interface CalendarEvent {
  date: string; // YYYY-MM-DD
  label: string;
  color: string;
}

function monthDays(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function fmtDate(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export function ProjectCalendar({
  projects,
  milestonesByProject,
}: {
  projects: Project[];
  milestonesByProject: Record<string, Milestone[]>;
}) {
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth());

  // Collect all events
  const events = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();

    // Project target dates
    for (const p of projects) {
      if (p.status !== "active" || !p.target_date) continue;
      const key = p.target_date;
      const ev: CalendarEvent = { date: key, label: p.title, color: "var(--color-accent-solo)" };
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(ev);
    }

    // Milestone target dates
    for (const [pid, milestones] of Object.entries(milestonesByProject)) {
      const proj = projects.find((p) => p.id === pid);
      for (const m of milestones) {
        if (m.status === "completed" || !m.target_date) continue;
        const key = m.target_date;
        const ev: CalendarEvent = {
          date: key,
          label: `${proj?.title || "Project"}: ${m.title}`,
          color: "var(--color-info)",
        };
        if (!map.has(key)) map.set(key, []);
        map.get(key)!.push(ev);
      }
    }

    return map;
  }, [projects, milestonesByProject]);

  const days = monthDays(year, month);
  const firstDayOfWeek = (new Date(year, month, 1).getDay() + 6) % 7; // Mon=0
  const todayStr = fmtDate(today.getFullYear(), today.getMonth(), today.getDate());

  const prevMonth = () => {
    if (month === 0) { setYear(year - 1); setMonth(11); }
    else setMonth(month - 1);
  };
  const nextMonth = () => {
    if (month === 11) { setYear(year + 1); setMonth(0); }
    else setMonth(month + 1);
  };

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDayOfWeek; i++) cells.push(null);
  for (let d = 1; d <= days; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <div>
      {/* Navigation */}
      <div className="flex items-center justify-between mb-3">
        <button onClick={prevMonth} className="px-2 py-1 rounded bg-surface-3 text-text-secondary text-[12px] cursor-pointer border-0 hover:bg-surface-hover">&lsaquo;</button>
        <span className="text-[13px] font-medium text-text">{MONTH_NAMES[month]} {year}</span>
        <button onClick={nextMonth} className="px-2 py-1 rounded bg-surface-3 text-text-secondary text-[12px] cursor-pointer border-0 hover:bg-surface-hover">&rsaquo;</button>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-7 gap-px">
        {WEEKDAYS.map((wd) => (
          <div key={wd} className="text-center text-[10px] text-text-muted py-1 font-medium">{wd}</div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={`e${i}`} />;
          const dateStr = fmtDate(year, month, day);
          const dayEvents = events.get(dateStr) || [];
          const isToday = dateStr === todayStr;

          return (
            <div
              key={i}
              className={`relative min-h-[48px] p-1 rounded-sm text-[11px] ${
                isToday ? "bg-accent-solo/10" : "bg-surface-1"
              }`}
            >
              <span className={`text-[11px] tabular-nums ${isToday ? "text-accent-solo font-semibold" : "text-text-muted"}`}>
                {day}
              </span>
              {dayEvents.length > 0 && (
                <div className="mt-0.5 space-y-0.5">
                  {dayEvents.slice(0, 2).map((ev, j) => (
                    <div
                      key={j}
                      className="text-[9px] truncate px-1 py-px rounded-sm"
                      style={{ backgroundColor: `color-mix(in srgb, ${ev.color} 15%, transparent)`, color: ev.color }}
                      title={ev.label}
                    >
                      {ev.label}
                    </div>
                  ))}
                  {dayEvents.length > 2 && (
                    <div className="text-[9px] text-text-muted">+{dayEvents.length - 2} more</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
