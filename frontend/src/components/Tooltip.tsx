import { useState } from "react";
import { createPortal } from "react-dom";

/** Pointer-following tooltip for chart marks. Content is React nodes, never HTML strings. */
export function Tip({ content, children, className, style }: {
  content: React.ReactNode; children: React.ReactNode; className?: string; style?: React.CSSProperties;
}) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  return (
    <div className={className} style={style}
      onPointerMove={(e) => setPos({ x: e.clientX, y: e.clientY })} onPointerLeave={() => setPos(null)}>
      {children}
      {pos && createPortal(
        <div className="tooltip" style={{ left: Math.min(pos.x + 14, window.innerWidth - 290), top: Math.max(8, pos.y - 58) }}>{content}</div>,
        document.body)}
    </div>
  );
}
