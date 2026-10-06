import { formatDateTime, formatPrice } from '../lib/format';
import { CajaFrame } from './CajaFrame';
import type { Recommendation } from './cajaApi';

const STEP_NAMES = ['LIMPIEZA', 'TRATAMIENTO', 'HIDRATACIÓN', 'PROTECCIÓN SOLAR'] as const;

interface Props {
  rec: Recommendation;
  onExit: () => void;
  onMark: () => void;
  onReadAnother: () => void;
  marking: boolean;
  error: string | null;
}

export function ResultScreen({ rec, onExit, onMark, onReadAnother, marking, error }: Props) {
  const pending = rec.estado === 'PENDIENTE';
  const products = [...rec.productos].sort((a, b) => a.paso - b.paso);

  return (
    <CajaFrame onExit={onExit} tall>
      <main className="flex flex-1 flex-col">
        <p className="mt-[22px] text-[15px] text-muted">Recomendación</p>
        <div className="flex items-center justify-between">
          <h1 className="font-display text-[46px] font-normal leading-[52px] text-ink">
            {rec.codigoCorto}
          </h1>
          <span
            className="border border-ink px-3 py-1.5 text-[13px] font-medium tracking-[0.1em] text-ink"
            role="status"
          >
            {rec.estado}
          </span>
        </div>
        <p className="mt-2 text-[15px] text-muted">
          {products.length} productos · generada {formatDateTime(rec.fechaCreacion)}
          {rec.fechaAtendida ? ` · atendida ${formatDateTime(rec.fechaAtendida)}` : ''}
        </p>

        <ul className="mt-4 bg-white">
          {products.map((p, i) => (
            <li
              key={p.sku}
              className={`flex items-center gap-3.5 p-3.5 ${i > 0 ? 'border-t border-line' : ''}`}
            >
              <div
                className="flex h-16 w-16 shrink-0 items-center justify-center bg-placeholder text-[12px] text-muted"
                aria-hidden={p.imagenUrl === ''}
              >
                {p.imagenUrl ? (
                  <img src={p.imagenUrl} alt="" className="h-full w-full object-contain" />
                ) : (
                  'Imagen'
                )}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-[12px] uppercase tracking-[0.14em] text-muted">
                  {STEP_NAMES[p.paso - 1]}
                </p>
                <p className="text-[17px] font-semibold leading-[22px] text-ink">{p.nombre}</p>
                <div className="mt-0.5 flex items-baseline justify-between text-[16px] text-ink">
                  <span className="font-medium">SKU {p.sku}</span>
                  <span>{formatPrice(p.precioCents)}</span>
                </div>
              </div>
            </li>
          ))}
        </ul>

        <div className="mt-auto pt-10">
          {error ? (
            <p role="alert" className="mb-3 text-[15px] text-danger">
              {error}
            </p>
          ) : null}
          <button
            type="button"
            onClick={onMark}
            disabled={!pending || marking}
            className="h-[60px] w-full bg-brand text-[18px] font-semibold text-white hover:bg-[#194035] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {marking ? 'Guardando…' : 'Marcar como atendida'}
          </button>
          <button
            type="button"
            onClick={onReadAnother}
            className="mt-3 h-[54px] w-full border border-ink bg-transparent text-[17px] text-ink hover:bg-white"
          >
            Leer otro QR
          </button>
        </div>
      </main>
    </CajaFrame>
  );
}
