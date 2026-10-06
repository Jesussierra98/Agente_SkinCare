import type { Reading, RoutineItem } from '../data/sampleRoutine';
import type { VoiceEvent } from '../voice/VoiceSession';
import type { Language } from './i18n';

export type Phase = 'idle' | 'connecting' | 'active' | 'lost';

export interface TranscriptLine {
  id: number;
  role: 'cliente' | 'asesor';
  text: string;
  final: boolean;
}

export interface KioskState {
  phase: Phase;
  language: Language;
  transcript: TranscriptLine[];
  routine: RoutineItem[] | null;
  saved: { recId: string; code: string; qrUrl: string } | null;
  readings: { ingrediente: string; articulos: Reading[] } | null;
  handoff: 'pendiente' | 'confirmada' | null;
  muted: boolean;
  lastActivityAt: number;
  error: 'mic_denied' | 'connect_failed' | 'timeout' | null;
  nextId: number;
}

export const initialKioskState: KioskState = {
  phase: 'idle',
  language: 'es',
  transcript: [],
  routine: null,
  saved: null,
  readings: null,
  handoff: null,
  muted: false,
  lastActivityAt: 0,
  error: null,
  nextId: 1,
};

export type KioskAction =
  | { type: 'start' }
  | { type: 'reset' }
  | { type: 'setMuted'; muted: boolean }
  | { type: 'voice'; event: VoiceEvent; now: number }
  | { type: 'local-text'; text: string };

export function kioskReducer(state: KioskState, action: KioskAction): KioskState {
  switch (action.type) {
    case 'start':
      return { ...initialKioskState, phase: 'connecting', language: state.language };
    case 'reset':
      return { ...initialKioskState, language: 'es' };
    case 'setMuted':
      return { ...state, muted: action.muted };
    case 'local-text':
      return state;
    case 'voice':
      return applyVoiceEvent(state, action.event, action.now);
  }
}

function applyVoiceEvent(state: KioskState, event: VoiceEvent, now: number): KioskState {
  switch (event.type) {
    case 'ready':
      return { ...state, phase: 'active', language: event.language };
    case 'language':
      return { ...state, language: event.language };
    case 'activity':
      return { ...state, lastActivityAt: now };
    case 'interrupt':
      return { ...state, lastActivityAt: 0 };
    case 'transcript': {
      // Un mensaje parcial del mismo emisor se reemplaza hasta que llega el final.
      const last = state.transcript[state.transcript.length - 1];
      if (last && last.role === event.role && !last.final) {
        const updated = { ...last, text: event.text, final: event.final };
        return { ...state, transcript: [...state.transcript.slice(0, -1), updated] };
      }
      const line: TranscriptLine = {
        id: state.nextId,
        role: event.role,
        text: event.text,
        final: event.final,
      };
      return { ...state, transcript: [...state.transcript, line], nextId: state.nextId + 1 };
    }
    case 'routine':
      return { ...state, routine: event.items };
    case 'saved':
      return { ...state, saved: { recId: event.recId, code: event.code, qrUrl: event.qrUrl } };
    case 'readings':
      return { ...state, readings: { ingrediente: event.ingrediente, articulos: event.articulos } };
    case 'handoff':
      return { ...state, handoff: event.estado };
    case 'connection_lost':
      // Se conserva todo lo mostrado (rutina, QR, código y transcripción).
      return { ...state, phase: 'lost', lastActivityAt: 0 };
    case 'error':
      return { ...initialKioskState, error: event.code, language: state.language };
  }
}

/** El indicador de escucha está activo solo si hubo audio hace menos de 300 ms. */
export function isListeningActive(state: KioskState, now: number): boolean {
  return state.phase === 'active' && state.lastActivityAt > 0 && now - state.lastActivityAt < 300;
}
