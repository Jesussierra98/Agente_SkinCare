import type { Reading } from '../data/sampleRoutine';
import type { KioskStrings } from '../kiosk/i18n';
import { readingsViewModel } from '../lib/viewModels';

interface Props {
  t: KioskStrings;
  ingrediente: string;
  articulos: Reading[];
}

/** Lecturas de PubMed: solo se muestran en pantalla, nunca se leen en voz. */
export function ReadingsCard({ t, ingrediente, articulos }: Props) {
  const titles = readingsViewModel(articulos);
  return (
    <section className="flex flex-1 flex-col bg-white px-5 py-5" aria-labelledby="lecturas">
      <h2
        id="lecturas"
        className="text-[12px] uppercase leading-[16px] tracking-[0.16em] text-muted"
      >
        {t.optionalReadings} {ingrediente.toLocaleUpperCase()}
      </h2>
      <ul className="mt-4 space-y-2 text-[14px] leading-[19px] text-ink">
        {titles.map((title, i) => (
          <li key={i}>{title}</li>
        ))}
      </ul>
      <p className="mt-auto pt-6 text-[12px] leading-[16px] text-muted">{t.readingsSource}</p>
    </section>
  );
}
