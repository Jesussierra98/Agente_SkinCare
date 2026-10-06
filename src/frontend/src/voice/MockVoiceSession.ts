import {
  SAMPLE_CODE,
  SAMPLE_READINGS,
  SAMPLE_REC_ID,
  SAMPLE_ROUTINE,
} from '../data/sampleRoutine';
import type { Language } from '../kiosk/i18n';
import type { VoiceEvent, VoiceListener, VoiceSession } from './VoiceSession';

export type MockScenario = 'normal' | 'handoff' | 'lost';

interface Line {
  role: 'cliente' | 'asesor';
  text: string;
  /** Duración aproximada en ms de "hablar" la línea. */
  ms: number;
}

const SCRIPTS: Record<Language, { normal: Line[]; handoff: Line[] }> = {
  es: {
    normal: [
      {
        role: 'asesor',
        text: 'Hola, soy el asesor del catálogo de skincare. ¿Qué te gustaría mejorar o cuidar de tu piel?',
        ms: 3800,
      },
      { role: 'cliente', text: 'Siento la piel seca y busco algo para hidratarla, de día.', ms: 3200 },
      {
        role: 'asesor',
        text: 'Entendido. ¿Usas ya algún limpiador o protector solar, o armamos la rutina completa?',
        ms: 3800,
      },
      { role: 'cliente', text: 'La rutina completa, por favor.', ms: 2200 },
      {
        role: 'asesor',
        text: 'Perfecto. Con lo que me cuentas, armé una rutina de cuatro pasos con productos de la tienda. Ya la puedes ver en pantalla.',
        ms: 4200,
      },
    ],
    handoff: [
      { role: 'asesor', text: 'Hola, ¿qué te gustaría cuidar de tu piel?', ms: 2500 },
      { role: 'cliente', text: 'Estoy embarazada y me salió una reacción alérgica.', ms: 3000 },
      {
        role: 'asesor',
        text: 'Por tu seguridad, un asesor de la tienda te atenderá en breve. Acude al mostrador de asesoría.',
        ms: 4000,
      },
    ],
  },
  en: {
    normal: [
      {
        role: 'asesor',
        text: 'Hi, I am the skincare catalog advisor. What would you like to improve or take care of in your skin?',
        ms: 3800,
      },
      { role: 'cliente', text: 'My skin feels dry and I want something to moisturize it, for daytime.', ms: 3200 },
      {
        role: 'asesor',
        text: 'Got it. Do you already use a cleanser or sunscreen, or shall we build the full routine?',
        ms: 3800,
      },
      { role: 'cliente', text: 'The full routine, please.', ms: 2200 },
      {
        role: 'asesor',
        text: 'Great. Based on what you told me, I put together a four-step routine with store products. You can see it on screen.',
        ms: 4200,
      },
    ],
    handoff: [
      { role: 'asesor', text: 'Hi, what would you like to take care of in your skin?', ms: 2500 },
      { role: 'cliente', text: 'I am pregnant and I had an allergic reaction.', ms: 3000 },
      {
        role: 'asesor',
        text: 'For your safety, a store advisor will help you shortly. Please go to the advice counter.',
        ms: 4000,
      },
    ],
  },
};

/** Lee `?scenario=` y `?lang=` de la URL para elegir la demo. */
export function scenarioFromUrl(search: string): { scenario: MockScenario; language: Language } {
  const q = new URLSearchParams(search);
  const s = q.get('scenario');
  const scenario: MockScenario = s === 'handoff' || s === 'lost' ? s : 'normal';
  const language: Language = q.get('lang') === 'en' ? 'en' : 'es';
  return { scenario, language };
}

/**
 * Sesión simulada: reproduce un guion local sin micrófono ni red.
 * Sirve para revisar el diseño y los estados de la pantalla.
 */
export class MockVoiceSession implements VoiceSession {
  private listeners = new Set<VoiceListener>();
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private pulse: ReturnType<typeof setInterval> | null = null;
  private closed = false;
  private routineShown = false;

  constructor(
    private readonly scenario: MockScenario = 'normal',
    private readonly language: Language = 'es',
  ) {}

  subscribe(listener: VoiceListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(event: VoiceEvent) {
    if (this.closed) return;
    this.listeners.forEach((l) => l(event));
  }

  private later(ms: number, fn: () => void) {
    const t = setTimeout(() => {
      this.timers.delete(t);
      fn();
    }, ms);
    this.timers.add(t);
  }

  async start(): Promise<void> {
    this.closed = false;
    this.routineShown = false;
    await new Promise((r) => setTimeout(r, 600));
    this.emit({ type: 'ready', language: this.language });

    const lines = SCRIPTS[this.language][this.scenario === 'handoff' ? 'handoff' : 'normal'];
    let at = 400;
    lines.forEach((line, i) => {
      this.later(at, () => this.speak(line));
      at += line.ms + 700;
      if (i === lines.length - 1) {
        if (this.scenario === 'handoff') {
          this.later(at, () =>
            this.emit({ type: 'handoff', motivo: 'condicion_sensible', estado: 'pendiente' }),
          );
        } else if (this.scenario === 'normal') {
          this.later(at, () => this.demoShowRoutine());
        }
      }
    });

    if (this.scenario === 'lost') {
      this.later(7000, () => {
        this.stopPulse();
        this.emit({ type: 'connection_lost', reason: 'renewal_failed' });
      });
    }
  }

  private speak(line: Line) {
    const words = line.text.split(' ');
    const step = Math.max(80, Math.floor(line.ms / words.length));
    // La voz del cliente se "escribe" palabra por palabra; la del asesor se muestra completa.
    if (line.role === 'cliente') {
      words.forEach((_, i) => {
        this.later(i * step, () =>
          this.emit({
            type: 'transcript',
            role: 'cliente',
            text: words.slice(0, i + 1).join(' ') + (i + 1 < words.length ? '…' : ''),
            final: i + 1 === words.length,
          }),
        );
      });
    } else {
      this.emit({ type: 'transcript', role: 'asesor', text: line.text, final: true });
    }
    this.startPulse(line.ms);
  }

  private startPulse(ms: number) {
    this.stopPulse();
    this.pulse = setInterval(() => this.emit({ type: 'activity' }), 100);
    this.later(ms, () => this.stopPulse());
  }

  private stopPulse() {
    if (this.pulse) clearInterval(this.pulse);
    this.pulse = null;
  }

  demoShowRoutine(): void {
    if (this.routineShown) return;
    this.routineShown = true;
    this.emit({ type: 'language', language: this.language });
    this.emit({ type: 'routine', items: SAMPLE_ROUTINE });
    this.emit({
      type: 'saved',
      recId: SAMPLE_REC_ID,
      code: SAMPLE_CODE,
      qrUrl: `https://tienda.example/caja?rec=${SAMPLE_REC_ID}`,
    });
    this.emit({ type: 'readings', ...SAMPLE_READINGS });
  }

  sendText(text: string): void {
    const t = text.trim();
    if (!t) return;
    this.emit({ type: 'transcript', role: 'cliente', text: t, final: true });
  }

  setMuted(_muted: boolean): void {
    // El mock no usa micrófono; el estado de silencio solo lo refleja la interfaz.
  }

  hangup(): void {
    this.stopPulse();
    this.timers.forEach(clearTimeout);
    this.timers.clear();
    this.closed = true;
  }
}
