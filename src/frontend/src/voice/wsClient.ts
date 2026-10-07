import { bearerProtocols } from '../lib/bearer';
import type { ServerMessage } from './protocol';

export const SESSION_PARAM = 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id';
export const CONNECT_TIMEOUT_MS = 5000;

/** Agrega el identificador de sesión como parámetro de consulta (sin pisar uno que ya venga en la URL). */
export function buildSocketUrl(base: string, sessionId?: string): string {
  if (!sessionId) return base;
  const url = new URL(base);
  if (!url.searchParams.has(SESSION_PARAM)) url.searchParams.set(SESSION_PARAM, sessionId);
  return url.toString();
}

export interface SocketHandlers {
  onMessage: (msg: ServerMessage) => void;
  /** El socket se cerró después de estar listo. */
  onClose: () => void;
}

export interface ConnectOptions {
  url: string;
  /** JWT de Cognito; sin token la conexión es anónima (solo el servidor local de desarrollo). */
  token?: string;
  sessionId?: string;
  timeoutMs?: number;
  handlers: SocketHandlers;
  /** Para pruebas. */
  createSocket?: (url: string, protocols?: string[]) => WebSocket;
}

export class ConnectError extends Error {
  constructor(public readonly code: 'timeout' | 'connect_failed') {
    super(code);
  }
}

/**
 * Abre el WebSocket (con el JWT en el subprotocolo, si lo hay) y espera `session_ready`.
 * Falla con `ConnectError('timeout')` a los 5 s y con `ConnectError('connect_failed')` si el servidor
 * rechaza o cierra la conexión antes de estar listo.
 */
export function connectVoiceSocket(opts: ConnectOptions): Promise<{ ws: WebSocket; ready: Extract<ServerMessage, { type: 'session_ready' }> }> {
  const make = opts.createSocket ?? ((url: string, protocols?: string[]) => new WebSocket(url, protocols));
  const url = buildSocketUrl(opts.url, opts.sessionId);
  const timeoutMs = opts.timeoutMs ?? CONNECT_TIMEOUT_MS;

  return new Promise((resolve, reject) => {
    const ws = make(url, opts.token ? bearerProtocols(opts.token) : undefined);
    let ready = false;
    const timer = setTimeout(() => {
      if (ready) return;
      // Primero se rechaza: cerrar el socket dispara `onclose`, que reportaría connect_failed en lugar de timeout.
      reject(new ConnectError('timeout'));
      ws.close();
    }, timeoutMs);

    ws.onmessage = (e) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(e.data as string) as ServerMessage;
      } catch {
        return;
      }
      if (msg.type === 'session_ready' && !ready) {
        ready = true;
        clearTimeout(timer);
        resolve({ ws, ready: msg });
        return;
      }
      opts.handlers.onMessage(msg);
    };
    ws.onerror = () => {
      if (!ready) {
        clearTimeout(timer);
        reject(new ConnectError('connect_failed'));
      }
    };
    ws.onclose = () => {
      clearTimeout(timer);
      if (!ready) reject(new ConnectError('connect_failed'));
      else opts.handlers.onClose();
    };
  });
}
