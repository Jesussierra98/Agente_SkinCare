import type { KioskStrings } from '../kiosk/i18n';

interface Props {
  t: KioskStrings;
  paso: 1 | 2 | 3 | 4;
}

/** Tarjeta que ocupa el lugar de un paso sin producto asignado (Req. 18.3). Mismo encabezado que `ProductCard`. */
export function MissingStepCard({ t, paso }: Props) {
  return (
    <li className="relative flex flex-1 flex-col bg-white">
      <div className="flex items-baseline gap-2 border-b border-line px-4 py-4">
        <span className="font-display text-[30px] leading-none text-ink" aria-hidden="true">
          {paso}
        </span>
        <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink">
          <span className="sr-only">{paso}. </span>
          {t.stepNames[paso - 1]}
        </h2>
      </div>
      <div className="flex flex-1 items-center justify-center bg-placeholder/40 px-4 py-6">
        <p className="text-center text-[15px] leading-[21px] text-muted">{t.noProductForStep}</p>
      </div>
    </li>
  );
}
