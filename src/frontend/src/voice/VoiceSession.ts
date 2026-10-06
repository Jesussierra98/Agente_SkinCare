import type { RoutineItem, Reading } from '../data/sampleRoutine';
import type { Language } from '../kiosk/i18n';

/** Eventos que la sesión de voz entrega a la interfaz (mismo vocabulario que el protocolo WebSocket). */
export type VoiceEvent =
  | { type: 'ready'; language: Language }
  | { type: 'language'; language: Language }
  | { type: 'activity' }
  | { type: 'transcript'; role: 'cliente' | 'asesor'; text: string; final: boolean }
  | { type: 'interrupt' }
  | { type: 'routine'; items: RoutineItem[] }
  | { type: 'saved'; recId: string; code: string; qrUrl: string }
  | { type: 'readings'; ingrediente: string; articulos: Reading[] }
  | { type: 'handoff'; motivo: string; estado: 'pendiente' | 'confirmada' }
  | { type: 'connection_lost'; reason: string }
  | { type: 'error'; code: 'mic_denied' | 'connect_failed' | 'timeout' };

export type VoiceListener = (event: VoiceEvent) => void;

/**
 * Contrato de una sesión de voz. El prototipo usa `MockVoiceSession`; la
 * sesión real (WebSocket + audio) implementa la misma interfaz.
 */
export interface VoiceSession {
  start(): Promise<void>;
  hangup(): void;
  sendText(text: string): void;
  setMuted(muted: boolean): void;
  subscribe(listener: VoiceListener): () => void;
  /** Solo para la demo del prototipo: saltar a la pantalla de rutina. */
  demoShowRoutine?(): void;
}
