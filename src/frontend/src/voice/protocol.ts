import type { Language } from '../kiosk/i18n';

/** Paso de la rutina tal como lo envía el servidor (precio como cadena decimal). */
export interface ServerRoutineStep {
  paso: 1 | 2 | 3 | 4;
  sku: string;
  nombre: string;
  marca: string;
  precio: string;
  imagen_url: string;
  razon_catalogo: string;
  modo_uso: string;
}

/** Mensajes servidor → cliente del protocolo JSON del WebSocket. */
export type ServerMessage =
  | { type: 'session_ready'; session_id?: string; output_sample_rate: number; language?: Language }
  | { type: 'audio'; data: string }
  | { type: 'interrupt' }
  | { type: 'transcript'; role: 'cliente' | 'asesor'; text: string; final: boolean }
  | { type: 'routine'; pasos: ServerRoutineStep[] }
  | { type: 'saved'; rec_id: string; codigo_corto: string; qr_url: string }
  | { type: 'readings'; ingrediente: string; articulos: { titulo: string }[] }
  | { type: 'handoff'; motivo: string; estado: 'pendiente' | 'confirmada' | 'sin_notificar' }
  | { type: 'language'; language: Language }
  | { type: 'info'; message?: string }
  | { type: 'connection_error'; reason: string };

/** Mensajes cliente → servidor. */
export type ClientMessage =
  | { type: 'audio'; data: string }
  | { type: 'text'; text: string }
  | { type: 'hangup' };
