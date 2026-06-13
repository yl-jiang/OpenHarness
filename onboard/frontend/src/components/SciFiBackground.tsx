import { useEffect, useRef } from "react";

/**
 * Subtle aurora background: 3 large soft blobs that drift imperceptibly,
 * plus a gentle mouse-following light. No particles, no lines — just
 * ambient atmosphere that never competes with content.
 */
export function SciFiBackground({ accent }: { accent: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef({ x: -9999, y: -9999 });
  const smoothRef = useRef({ x: -9999, y: -9999 });
  const rafRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const parseHex = (hex: string) => {
      const h = hex.replace("#", "");
      return [
        parseInt(h.substring(0, 2), 16),
        parseInt(h.substring(2, 4), 16),
        parseInt(h.substring(4, 6), 16),
      ];
    };
    const [cr, cg, cb] = parseHex(accent);

    let W = 0;
    let H = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    function resize() {
      W = window.innerWidth;
      H = window.innerHeight;
      canvas!.width = W * dpr;
      canvas!.height = H * dpr;
      canvas!.style.width = `${W}px`;
      canvas!.style.height = `${H}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener("resize", resize);

    /* ── Mouse ── */
    function onMouse(e: MouseEvent) {
      mouseRef.current.x = e.clientX;
      mouseRef.current.y = e.clientY;
    }
    function onLeave() {
      mouseRef.current.x = -9999;
      mouseRef.current.y = -9999;
    }
    window.addEventListener("mousemove", onMouse);
    window.addEventListener("mouseleave", onLeave);

    /* ── Aurora blobs ── */
    const blobs = [
      { x: 0.25, y: 0.3, speedX: 0.00013, speedY: 0.00009, phaseX: 0, phaseY: 1.2, radius: 0.35, alpha: 0.035 },
      { x: 0.7, y: 0.6, speedX: 0.00009, speedY: 0.00015, phaseX: 2.1, phaseY: 0.5, radius: 0.3, alpha: 0.03 },
      { x: 0.5, y: 0.8, speedX: 0.00011, speedY: 0.00007, phaseX: 4.0, phaseY: 3.3, radius: 0.28, alpha: 0.025 },
    ];

    let t = 0;

    function draw() {
      t++;
      ctx!.clearRect(0, 0, W, H);

      /* ── Draw aurora blobs ── */
      for (const b of blobs) {
        const bx = (b.x + Math.sin(t * b.speedX + b.phaseX) * 0.15) * W;
        const by = (b.y + Math.cos(t * b.speedY + b.phaseY) * 0.12) * H;
        const br = b.radius * Math.max(W, H);

        const grad = ctx!.createRadialGradient(bx, by, 0, bx, by, br);
        grad.addColorStop(0, `rgba(${cr},${cg},${cb},${b.alpha})`);
        grad.addColorStop(0.5, `rgba(${cr},${cg},${cb},${b.alpha * 0.4})`);
        grad.addColorStop(1, "rgba(0,0,0,0)");
        ctx!.fillStyle = grad;
        ctx!.fillRect(0, 0, W, H);
      }

      /* ── Smooth mouse follow ── */
      const sm = smoothRef.current;
      const mm = mouseRef.current;
      if (mm.x > -9000) {
        sm.x += (mm.x - sm.x) * 0.03;
        sm.y += (mm.y - sm.y) * 0.03;
      } else {
        sm.x += (-9999 - sm.x) * 0.02;
        sm.y += (-9999 - sm.y) * 0.02;
      }

      if (sm.x > -9000) {
        const mr = Math.max(W, H) * 0.25;
        const mg = ctx!.createRadialGradient(sm.x, sm.y, 0, sm.x, sm.y, mr);
        mg.addColorStop(0, `rgba(${cr},${cg},${cb},0.045)`);
        mg.addColorStop(0.4, `rgba(${cr},${cg},${cb},0.015)`);
        mg.addColorStop(1, "rgba(0,0,0,0)");
        ctx!.fillStyle = mg;
        ctx!.fillRect(sm.x - mr, sm.y - mr, mr * 2, mr * 2);
      }

      rafRef.current = requestAnimationFrame(draw);
    }
    rafRef.current = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMouse);
      window.removeEventListener("mouseleave", onLeave);
    };
  }, [accent]);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none"
      style={{ zIndex: 0 }}
      aria-hidden
    />
  );
}
