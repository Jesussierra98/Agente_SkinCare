import { QRCodeSVG } from 'qrcode.react';
import type { KioskStrings } from '../kiosk/i18n';

interface Props {
  t: KioskStrings;
  code: string;
  qrUrl: string;
}

/**
 * Tarjeta oscura "PARA LA CAJA". El QR mide 224 px en pantalla, como en el
 * diseño entregado (Req. 18.7 pide ≥256 px: diferencia anotada en ui-reference.md).
 */
export function CheckoutCard({ t, code, qrUrl }: Props) {
  return (
    <section className="bg-ink px-8 pb-7 pt-6 text-center text-white" aria-labelledby="para-caja">
      <h2 id="para-caja" className="text-[13px] font-bold uppercase tracking-[0.2em]">
        {t.forCheckout}
      </h2>
      <div className="mx-auto mt-3.5 w-[224px] bg-white p-3">
        <QRCodeSVG
          value={qrUrl}
          size={200}
          level="M"
          bgColor="#ffffff"
          fgColor="#15211d"
          role="img"
          aria-label={`QR ${code}`}
        />
      </div>
      <p className="mt-4 text-[13px] text-white/85">{t.code}</p>
      <p
        className="mt-1 font-mono text-[28px] font-semibold tracking-[0.12em]"
        aria-label={`${t.code} ${code.split('').join(' ')}`}
      >
        {code}
      </p>
      <p className="mx-auto mt-3 max-w-[240px] text-[16px] font-semibold leading-[23px]">
        {t.takePhoto}
      </p>
    </section>
  );
}
