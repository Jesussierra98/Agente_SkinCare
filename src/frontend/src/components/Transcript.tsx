import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { TranscriptLine } from '../kiosk/kioskState';
import type { KioskStrings } from '../kiosk/i18n';

interface Props {
  t: KioskStrings;
  lines: TranscriptLine[];
  onSend: (text: string) => void;
  /** Mensaje mostrado cuando la voz terminó (la transcripción se conserva). */
  endedNotice?: string | null;
}

export function Transcript({ t, lines, onSend, endedNotice }: Props) {
  const [draft, setDraft] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [lines, endedNotice]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft('');
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-white" aria-labelledby="transcripcion">
      <h2 id="transcripcion" className="eyebrow border-b border-line px-7 py-5">
        {t.transcript}
      </h2>

      <div role="log" aria-live="polite" className="min-h-0 flex-1 overflow-y-auto px-7 py-5">
        <ul className="space-y-6">
          {lines.map((line) => (
            <li key={line.id} className={line.role === 'cliente' ? 'pl-[72px]' : ''}>
              <p
                className={`text-[12px] font-semibold uppercase tracking-[0.14em] ${
                  line.role === 'asesor' ? 'text-brand' : 'text-muted'
                }`}
              >
                {line.role === 'asesor' ? t.advisor : t.you}
              </p>
              <p
                className={`mt-1.5 text-[19px] leading-[28px] ${
                  line.final ? 'text-ink' : 'text-faint'
                }`}
              >
                {line.text}
              </p>
            </li>
          ))}
        </ul>
        {endedNotice ? (
          <p className="mt-6 border-t border-line pt-4 text-[15px] text-muted">{endedNotice}</p>
        ) : null}
        <div ref={endRef} />
      </div>

      <form onSubmit={submit} className="border-t border-line px-7 pb-6 pt-4">
        <label htmlFor="mensaje" className="block text-[14px] text-muted">
          {t.noisy}
        </label>
        <div className="mt-2.5 flex gap-3">
          <input
            id="mensaje"
            type="text"
            value={draft}
            maxLength={500}
            autoComplete="off"
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t.typePlaceholder}
            className="h-[52px] min-w-0 flex-1 border border-field bg-white px-4 text-[17px] text-ink placeholder:text-faint"
          />
          <button
            type="submit"
            className="h-[52px] bg-ink px-7 text-[16px] font-semibold text-white hover:bg-black"
          >
            {t.send}
          </button>
        </div>
      </form>
    </section>
  );
}
