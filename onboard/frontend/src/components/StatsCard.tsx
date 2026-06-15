import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

interface StatsCardProps {
  linkTo?: string;
  label: string;
  value: number | string;
  hint?: string;
  icon?: string;
  accent?: string;
}

function useCountUp(target: number, duration = 700): number {
  const [current, setCurrent] = useState(0);
  const frameRef = useRef(0);
  const startTime = useRef<number | null>(null);

  useEffect(() => {
    if (target === 0) {
      setCurrent(0);
      return;
    }
    startTime.current = null;
    function animate(ts: number) {
      if (!startTime.current) startTime.current = ts;
      const progress = Math.min((ts - startTime.current!) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setCurrent(Math.round(eased * target));
      if (progress < 1) frameRef.current = requestAnimationFrame(animate);
    }
    frameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frameRef.current);
  }, [target, duration]);

  return current;
}

export function StatsCard({ label, value, hint, icon, accent, linkTo }: StatsCardProps) {
  const isNumber = typeof value === "number";
  const displayed = useCountUp(isNumber ? (value as number) : 0);
  const colorStyle = accent ? { color: accent } : undefined;

  const inner = (
    <article className={`relative p-4 rounded-lg border border-border bg-surface-1 hover:bg-surface-2 transition-colors group overflow-hidden${linkTo ? " cursor-pointer" : ""}`}>
      {icon && (
        <span
          className="absolute top-3 right-3 text-[15px] opacity-30 group-hover:opacity-50 transition-opacity select-none"
          aria-hidden
        >
          {icon}
        </span>
      )}
      <div
        className="font-mono text-[28px] font-semibold tracking-tight leading-none animate-[count-up_0.4s_ease-out_both]"
        style={colorStyle}
      >
        {isNumber ? displayed.toLocaleString() : value}
      </div>
      <div className="mt-1.5 text-[11px] uppercase tracking-wider text-text-muted font-medium">
        {label}
      </div>
      {hint ? (
        <div className="mt-0.5 text-[11px] text-text-muted">{hint}</div>
      ) : null}
    </article>
  );

  if (linkTo) {
    return <Link to={linkTo} className="no-underline block">{inner}</Link>;
  }
  return inner;
}
