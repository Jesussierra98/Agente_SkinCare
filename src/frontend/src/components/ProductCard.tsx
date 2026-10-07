import { useEffect, useRef, useState } from 'react';
import type { RoutineItem } from '../data/sampleRoutine';
import type { KioskStrings } from '../kiosk/i18n';
import { productCardViewModel } from '../lib/viewModels';

interface Props {
  t: KioskStrings;
  item: RoutineItem;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
}

export function ProductCard({ t, item, open, onToggle, onClose }: Props) {
  const [imgFailed, setImgFailed] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);

  // Mueve el foco al popover al abrir y lo devuelve al botón al cerrar.
  useEffect(() => {
    if (open) popoverRef.current?.focus();
    else if (wasOpen.current) triggerRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  // Cierra con Escape o con clic fuera.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const onDown = (e: MouseEvent | TouchEvent) => {
      const target = e.target as Node;
      if (popoverRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
    };
  }, [open, onClose]);

  const view = productCardViewModel(item);
  const stepLabel = t.stepNames[item.paso - 1]!;
  const showImage = item.imagenUrl !== '' && !imgFailed;
  const popoverId = `uso-${item.sku}`;

  return (
    <li className="relative flex flex-1 flex-col bg-white">
      <div className="flex items-baseline gap-2 border-b border-line px-4 py-4">
        <span className="font-display text-[30px] leading-none text-ink" aria-hidden="true">
          {item.paso}
        </span>
        <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink">
          <span className="sr-only">{item.paso}. </span>
          {stepLabel}
        </h2>
      </div>

      <div className="flex flex-1 flex-col px-4 pb-4 pt-4">
        <div className="flex h-[170px] shrink-0 items-center justify-center bg-placeholder">
          {showImage ? (
            <img
              src={item.imagenUrl}
              alt={t.imageAlt(item.nombre)}
              className="h-full w-full object-contain"
              onError={() => setImgFailed(true)}
            />
          ) : (
            <span className="px-2 text-center text-[13px] leading-[17px] text-muted">
              {t.imagePlaceholder}
            </span>
          )}
        </div>

        <p className="mt-4 text-[12px] uppercase tracking-[0.14em] text-muted">{view.marca}</p>
        <p className="mt-1 text-[17px] font-semibold leading-[21px] text-ink">{view.nombre}</p>
        <p className="mt-2 text-[14px] leading-[19px] text-muted">{view.razon}</p>

        <div className="mt-auto flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5 pt-4 text-[12px] text-ink">
          <span className="whitespace-nowrap">
            {t.sku} {view.sku}
          </span>
          <span className="whitespace-nowrap font-bold">{view.precio}</span>
        </div>
        <button
          ref={triggerRef}
          type="button"
          aria-expanded={open}
          aria-controls={popoverId}
          onClick={onToggle}
          className="mt-2 h-11 border border-ink bg-white text-[14px] text-ink hover:bg-canvas"
        >
          {t.howToUse}
        </button>
      </div>

      {open ? (
        <div
          id={popoverId}
          ref={popoverRef}
          role="dialog"
          aria-label={`${t.howToUse}: ${item.nombre}`}
          tabIndex={-1}
          className="absolute inset-x-2 bottom-[68px] z-10 border border-ink bg-white p-4 outline-none shadow-[0_8px_24px_rgba(21,33,29,0.18)]"
        >
          <p className="text-[12px] font-semibold uppercase tracking-[0.14em] text-brand">
            {t.howToUse}
          </p>
          <p className="mt-2 text-[15px] leading-[21px] text-ink">{item.modoUso}</p>
          <button
            type="button"
            onClick={onClose}
            className="mt-3 h-9 w-full border border-ink text-[13px] text-ink hover:bg-canvas"
          >
            {t.close}
          </button>
        </div>
      ) : null}
    </li>
  );
}
