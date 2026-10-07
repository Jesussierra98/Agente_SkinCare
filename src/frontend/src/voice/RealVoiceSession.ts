import type { RoutineItem } from '../data/sampleRoutine';
import { KioskAuthError } from '../auth/kioskAuth';
import { base64ToBytes, bytesToBase64, decimalStringToCents, pcm16ToFloat, rmsPcm16 } from './pcm';
import type { ClientMessage, ServerMessage, ServerRoutineStep } from './protocol';
import type { VoiceEvent, VoiceListener, VoiceSession } from './VoiceSession';
import { ConnectError, connectVoiceSocket } from './wsClient';

const SPEECH_RMS_THRESHOLD = 0.015;

export function defaultWsUrl(): string {
  const fromEnv = import.meta.env.VITE_WS_URL as string | undefined;
  if (fromEnv) return fromEnv;
  const host = window.location.hostname || 'localhost';
  return `ws://${host}:8080/ws`;
}

function toRoutineItem(step: ServerRoutineStep): RoutineItem {
  return {
    paso: step.paso,
    sku: step.sku,
    nombre: step.nombre,
    marca: step.marca,
    precioCents: decimalStringToCents(step.precio),
    imagenUrl: step.imagen_url,
    razonCatalogo: step.razon_catalogo,
    modoUso: step.modo_uso,
  };
}

/**
 * Sesión de voz real: micrófono → WebSocket → agente (Nova 2 Sonic) → bocinas.
 * Protocolo JSON definido en design.md (mensajes `audio`, `text`, `hangup` y los eventos del servidor).
 */
export class RealVoiceSession implements VoiceSession {
  private listeners = new Set<VoiceListener>();
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private captureCtx: AudioContext | null = null;
  private playbackCtx: AudioContext | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private muted = false;
  private closed = false;

  /**
   * @param wsUrl dirección del WebSocket (local o `wss://` de AgentCore).
   * @param getToken access token de Cognito del dispositivo; sin él la conexión es anónima (solo desarrollo local).
   * @param sessionId identificador de sesión (UUID) que se envía como parámetro de consulta.
   */
  constructor(
    private readonly wsUrl: string = defaultWsUrl(),
    private readonly getToken?: () => Promise<string>,
    private readonly sessionId: string = crypto.randomUUID(),
  ) {}

  subscribe(listener: VoiceListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(event: VoiceEvent) {
    if (this.closed && event.type !== 'error') return;
    this.listeners.forEach((l) => l(event));
  }

  async start(): Promise<void> {
    this.closed = false;
    // Los AudioContext se crean aquí, dentro del gesto del usuario (política de autoplay).
    this.playbackCtx = new AudioContext({ sampleRate: 24000 });
    this.captureCtx = new AudioContext();
    void this.playbackCtx.resume();
    void this.captureCtx.resume();

    this.emit({ type: 'phase', phase: 'requesting_mic' });
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch {
      this.teardown();
      this.emit({ type: 'error', code: 'mic_denied' });
      return;
    }

    try {
      await Promise.all([
        this.playbackCtx.audioWorklet.addModule('/worklets/pcm16-playback.js'),
        this.captureCtx.audioWorklet.addModule('/worklets/pcm16-capture.js'),
      ]);
      this.playbackNode = new AudioWorkletNode(this.playbackCtx, 'pcm16-playback', {
        outputChannelCount: [1],
      });
      this.playbackNode.connect(this.playbackCtx.destination);
      this.emit({ type: 'phase', phase: 'connecting' });
      await this.openSocket();
    } catch (err) {
      this.teardown();
      this.emit({ type: 'error', code: this.errorCode(err) });
      return;
    }
    this.startCapture();
  }

  /** Traduce un fallo de conexión o de autenticación al código que entiende la pantalla de inicio. */
  private errorCode(err: unknown): 'timeout' | 'connect_failed' | 'not_provisioned' | 'auth_failed' {
    if (err instanceof ConnectError) return err.code;
    if (err instanceof KioskAuthError) return err.code === 'not_provisioned' ? 'not_provisioned' : 'auth_failed';
    return 'connect_failed';
  }

  /** Abre el WebSocket (con el JWT si hay autenticación) y espera `session_ready` (máximo 5 s). */
  private async openSocket(): Promise<void> {
    const token = this.getToken ? await this.getToken() : undefined;
    const { ws, ready } = await connectVoiceSocket({
      url: this.wsUrl,
      token,
      sessionId: this.sessionId,
      handlers: {
        onMessage: (msg) => this.handle(msg),
        onClose: () => {
          if (!this.closed) {
            this.emit({ type: 'connection_lost', reason: 'closed' });
            this.releaseAudio();
          }
        },
      },
    });
    this.ws = ws;
    this.emit({ type: 'ready', language: ready.language ?? 'es' });
  }

  private startCapture() {
    if (!this.captureCtx || !this.stream) return;
    const source = this.captureCtx.createMediaStreamSource(this.stream);
    const node = new AudioWorkletNode(this.captureCtx, 'pcm16-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { targetRate: 16000, frameSamples: 1024 },
    });
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      const ws = this.ws;
      if (this.muted || this.closed || !ws || ws.readyState !== WebSocket.OPEN) return;
      const frame = new Int16Array(e.data);
      ws.send(
        JSON.stringify({ type: 'audio', data: bytesToBase64(new Uint8Array(frame.buffer, frame.byteOffset, frame.byteLength)) }),
      );
      if (rmsPcm16(frame) > SPEECH_RMS_THRESHOLD) this.emit({ type: 'activity' });
    };
    // El nodo debe estar conectado a la salida para que el navegador lo procese; ganancia 0 = sin eco.
    const silent = this.captureCtx.createGain();
    silent.gain.value = 0;
    source.connect(node);
    node.connect(silent);
    silent.connect(this.captureCtx.destination);
  }

  private handle(msg: ServerMessage) {
    switch (msg.type) {
      case 'audio': {
        if (!this.playbackNode) return;
        const bytes = base64ToBytes(msg.data);
        const pcm = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength - (bytes.byteLength % 2)));
        const samples = pcm16ToFloat(pcm);
        this.playbackNode.port.postMessage({ type: 'push', samples }, [samples.buffer]);
        this.emit({ type: 'activity' });
        return;
      }
      case 'interrupt':
        this.playbackNode?.port.postMessage({ type: 'clear' });
        this.emit({ type: 'interrupt' });
        return;
      case 'transcript':
        this.emit({ type: 'transcript', role: msg.role, text: msg.text, final: msg.final });
        return;
      case 'routine':
        this.emit({ type: 'routine', items: msg.pasos.map(toRoutineItem) });
        return;
      case 'saved':
        this.emit({ type: 'saved', recId: msg.rec_id, code: msg.codigo_corto, qrUrl: msg.qr_url });
        return;
      case 'readings':
        this.emit({ type: 'readings', ingrediente: msg.ingrediente, articulos: msg.articulos });
        return;
      case 'handoff':
        this.emit({
          type: 'handoff',
          motivo: msg.motivo,
          estado: msg.estado === 'confirmada' ? 'confirmada' : 'pendiente',
        });
        return;
      case 'language':
        this.emit({ type: 'language', language: msg.language });
        return;
      case 'connection_error':
        this.emit({ type: 'connection_lost', reason: msg.reason });
        this.releaseAudio();
        return;
      default:
        return;
    }
  }

  private send(message: ClientMessage): void {
    this.ws?.send(JSON.stringify(message));
  }

  sendText(text: string): void {
    const t = text.trim();
    if (!t || this.ws?.readyState !== WebSocket.OPEN) return;
    this.send({ type: 'text', text: t });
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = !muted));
  }

  hangup(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      try {
        this.send({ type: 'hangup' });
      } catch {
        /* el socket ya se cerró */
      }
    }
    this.closed = true;
    this.teardown();
  }

  /** Detiene micrófono y bocinas pero deja el socket abierto (la pantalla sigue mostrando la rutina). */
  private releaseAudio() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.playbackNode?.port.postMessage({ type: 'clear' });
    void this.captureCtx?.close().catch(() => undefined);
    void this.playbackCtx?.close().catch(() => undefined);
    this.captureCtx = null;
    this.playbackCtx = null;
    this.playbackNode = null;
  }

  private teardown() {
    this.releaseAudio();
    const ws = this.ws;
    this.ws = null;
    if (ws && ws.readyState <= WebSocket.OPEN) ws.close();
  }
}
