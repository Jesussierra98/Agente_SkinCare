import type { ReactNode } from 'react';

export function KioskHeader({
  brand,
  brandSub,
  height = 102,
  children,
}: {
  brand: string;
  brandSub: string;
  /** Altura del encabezado en px: cada pantalla del diseño tiene la suya. */
  height?: number;
  children?: ReactNode;
}) {
  return (
    <header
      style={{ height }}
      className="flex shrink-0 items-center justify-between border-b border-line px-14"
    >
      <div className="text-[15px] tracking-[0.2em]">
        <span className="font-bold text-ink">{brand}</span>
        <span className="text-muted"> · {brandSub}</span>
      </div>
      {children ? <div className="flex items-center gap-6">{children}</div> : null}
    </header>
  );
}

export function StatusDot({ label, active = true }: { label: string; active?: boolean }) {
  return (
    <span className="flex items-center gap-2.5 text-[15px] text-ink" role="status" aria-live="polite">
      <span
        className={`inline-block h-2.5 w-2.5 rounded-full ${active ? 'bg-brand' : 'bg-faint'}`}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}
