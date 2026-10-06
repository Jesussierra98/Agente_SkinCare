import type { ReactNode } from 'react';

/** Marco móvil vertical de la Vista_Caja: encabezado + (opcional) enlace "Salir". */
export function CajaFrame({
  onExit,
  tall = false,
  children,
}: {
  onExit?: () => void;
  /** El diseño baja el encabezado unos px en las pantallas con "Salir". */
  tall?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={`mx-auto flex min-h-screen w-full max-w-[430px] flex-col px-6 pb-9 ${
        tall ? 'pt-[45px]' : 'pt-8'
      }`}
    >
      <header className="flex items-baseline justify-between">
        <div className="text-[14px] tracking-[0.2em]">
          <span className="font-bold text-ink">GRUPO ULTRA</span>
          <span className="text-muted"> · CAJA</span>
        </div>
        {onExit ? (
          <button
            type="button"
            onClick={onExit}
            className="text-[14px] text-brand underline underline-offset-2"
          >
            Salir
          </button>
        ) : null}
      </header>
      {children}
    </div>
  );
}
