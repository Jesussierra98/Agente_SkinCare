import { useCallback, useState } from 'react';
import { CheckoutCard } from '../components/CheckoutCard';
import { PhoneHangupIcon } from '../components/Icons';
import { KioskHeader, StatusDot } from '../components/KioskHeader';
import { MissingStepCard } from '../components/MissingStepCard';
import { ProductCard } from '../components/ProductCard';
import { ReadingsCard } from '../components/ReadingsCard';
import type { KioskStrings } from './i18n';
import type { KioskState } from './kioskState';
import { useColumns } from './useColumns';

interface Props {
  t: KioskStrings;
  state: KioskState;
  onHangup: () => void;
}

export function RoutineScreen({ t, state, onHangup }: Props) {
  const [openSku, setOpenSku] = useState<string | null>(null);
  const close = useCallback(() => setOpenSku(null), []);
  const lost = state.phase === 'lost';
  const items = state.routine ?? [];
  const columns = useColumns();

  return (
    <div className="flex min-h-screen flex-col">
      <KioskHeader brand={t.brand} brandSub={t.brandSub} height={93}>
        <StatusDot label={lost ? t.voiceEnded : t.advisorListening} active={!lost} />
        <button
          type="button"
          onClick={onHangup}
          className="flex h-[52px] items-center gap-3 bg-danger px-8 text-[17px] font-semibold text-white hover:bg-[#862626]"
        >
          <PhoneHangupIcon width={20} height={20} />
          {t.hangup}
        </button>
      </KioskHeader>

      {/* Con 1024 px o más es el diseño entregado; por debajo, la columna lateral pasa debajo de las tarjetas (ui-reference, fila 22). */}
      <main
        className={`grid flex-1 gap-10 pb-10 pt-8 ${
          columns === 4 ? 'grid-cols-[1fr_300px] px-14' : columns === 2 ? 'grid-cols-1 px-10' : 'grid-cols-1 px-5'
        }`}
      >
        <section className="flex min-w-0 flex-col" aria-labelledby="tu-rutina">
          <div className="flex items-end justify-between">
            <h1 id="tu-rutina" className="font-display text-[52px] font-normal leading-[52px] text-ink">
              {t.yourRoutine}
            </h1>
            <p className="pb-1 text-[16px] text-muted">{t.oneProductPerStep}</p>
          </div>

          <ol
            className="mt-5 grid flex-1 items-stretch gap-3.5"
            style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
          >
            {([1, 2, 3, 4] as const).map((paso) => {
              const item = items.find((i) => i.paso === paso);
              if (!item) return <MissingStepCard key={`falta-${paso}`} t={t} paso={paso} />;
              return (
                <ProductCard
                  key={item.sku}
                  t={t}
                  item={item}
                  open={openSku === item.sku}
                  onToggle={() => setOpenSku((cur) => (cur === item.sku ? null : item.sku))}
                  onClose={close}
                />
              );
            })}
          </ol>

          <p className="mt-5 max-w-[740px] text-[15px] leading-[22px] text-muted">{t.routineNote}</p>
        </section>

        <aside className="flex min-w-0 flex-col gap-3.5">
          {state.saved ? (
            <CheckoutCard t={t} code={state.saved.code} qrUrl={state.saved.qrUrl} />
          ) : null}
          {state.readings && state.readings.articulos.length > 0 ? (
            <ReadingsCard
              t={t}
              ingrediente={state.readings.ingrediente}
              articulos={state.readings.articulos}
            />
          ) : null}
        </aside>
      </main>
    </div>
  );
}
