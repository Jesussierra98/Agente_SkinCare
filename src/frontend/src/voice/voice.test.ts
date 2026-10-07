import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KioskAuth, KioskAuthError, REFRESH_TOKEN_KEY, regionFromPoolId } from '../auth/kioskAuth';
import { bearerProtocols } from '../lib/bearer';
import { RealVoiceSession } from './RealVoiceSession';
import type { VoiceEvent } from './VoiceSession';
import { buildSocketUrl, ConnectError, connectVoiceSocket, SESSION_PARAM } from './wsClient';

// ---- WebSocket simulado -------------------------------------------------------------------------------------------------

class FakeSocket {
  static last: FakeSocket;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string, readonly protocols?: string[]) {
    FakeSocket.last = this;
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

const create = (url: string, protocols?: string[]) => new FakeSocket(url, protocols) as unknown as WebSocket;
const READY = { type: 'session_ready', output_sample_rate: 24000, language: 'es' };

describe('buildSocketUrl', () => {
  it('agrega el session_id como parámetro de consulta sin pisar uno existente', () => {
    expect(buildSocketUrl('wss://host/ws', 'abc')).toBe(`wss://host/ws?${SESSION_PARAM}=abc`);
    expect(buildSocketUrl(`wss://host/ws?${SESSION_PARAM}=ya`, 'abc')).toBe(`wss://host/ws?${SESSION_PARAM}=ya`);
    expect(buildSocketUrl('wss://host/ws')).toBe('wss://host/ws');
  });
});

describe('connectVoiceSocket', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('envía el JWT en el subprotocolo y resuelve al recibir session_ready', async () => {
    const onMessage = vi.fn();
    const promise = connectVoiceSocket({ url: 'wss://h/ws', token: 'tok.en', sessionId: 's1', handlers: { onMessage, onClose: vi.fn() }, createSocket: create });
    expect(FakeSocket.last.protocols).toEqual(bearerProtocols('tok.en'));
    expect(FakeSocket.last.url).toContain(`${SESSION_PARAM}=s1`);
    FakeSocket.last.receive(READY);
    await expect(promise).resolves.toMatchObject({ ready: { type: 'session_ready' } });
    FakeSocket.last.receive({ type: 'interrupt' });
    expect(onMessage).toHaveBeenCalledWith({ type: 'interrupt' });
    expect(onMessage).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'session_ready' }));
  });

  it('sin token no manda subprotocolos (servidor local)', () => {
    void connectVoiceSocket({ url: 'ws://h/ws', handlers: { onMessage: vi.fn(), onClose: vi.fn() }, createSocket: create }).catch(() => undefined);
    expect(FakeSocket.last.protocols).toBeUndefined();
  });

  it('falla con timeout a los 5 s si no llega session_ready', async () => {
    const promise = connectVoiceSocket({ url: 'wss://h/ws', handlers: { onMessage: vi.fn(), onClose: vi.fn() }, createSocket: create });
    const assertion = expect(promise).rejects.toMatchObject({ code: 'timeout' });
    await vi.advanceTimersByTimeAsync(4_999);
    expect(FakeSocket.last.closed).toBe(false);
    await vi.advanceTimersByTimeAsync(2);
    await assertion;
    expect(FakeSocket.last.closed).toBe(true);
  });

  it('un rechazo del servidor antes de estar listo es connect_failed', async () => {
    const promise = connectVoiceSocket({ url: 'wss://h/ws', handlers: { onMessage: vi.fn(), onClose: vi.fn() }, createSocket: create });
    FakeSocket.last.onerror?.();
    await expect(promise).rejects.toBeInstanceOf(ConnectError);
    await expect(promise).rejects.toMatchObject({ code: 'connect_failed' });
  });

  it('avisa el cierre solo si el socket ya estaba listo', async () => {
    const onClose = vi.fn();
    const promise = connectVoiceSocket({ url: 'wss://h/ws', handlers: { onMessage: vi.fn(), onClose }, createSocket: create });
    FakeSocket.last.receive(READY);
    await promise;
    FakeSocket.last.onclose?.();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// ---- autenticación del dispositivo (DD-03) ------------------------------------------------------------------------------

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

function cognito(responses: Array<{ status: number; body?: unknown } | Error>) {
  const calls: { target: string; body: Record<string, unknown> }[] = [];
  const fetchImpl = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const next = responses.shift()!;
    calls.push({
      target: String((init?.headers as Record<string, string>)['X-Amz-Target']),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
    });
    if (next instanceof Error) throw next;
    return new Response(JSON.stringify(next.body ?? {}), { status: next.status });
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

const result = (token: string, extra: object = {}) => ({ status: 200, body: { AuthenticationResult: { AccessToken: token, ExpiresIn: 3600, ...extra } } });

describe('KioskAuth', () => {
  it('se aprovisiona una vez y guarda solo el refresh token', async () => {
    const storage = memoryStorage();
    const { fetchImpl, calls } = cognito([result('a1', { RefreshToken: 'r1' })]);
    const auth = new KioskAuth({ region: 'us-east-1', clientId: 'cid', storage, fetchImpl });
    expect(auth.isProvisioned()).toBe(false);
    await auth.provision('kiosco-01', 'secreta');
    expect(auth.isProvisioned()).toBe(true);
    expect(calls[0]!.body).toMatchObject({ AuthFlow: 'USER_PASSWORD_AUTH', ClientId: 'cid' });
    expect([...storage.data.values()]).toEqual(['r1']); // la contraseña y el access token no se guardan
  });

  it('renueva el access token con REFRESH_TOKEN_AUTH y lo reutiliza mientras vigente', async () => {
    const storage = memoryStorage();
    storage.setItem(REFRESH_TOKEN_KEY, 'r1');
    const { fetchImpl, calls } = cognito([result('a2'), result('a3')]);
    let now = 0;
    const auth = new KioskAuth({ region: 'us-east-1', clientId: 'cid', storage, fetchImpl, now: () => now });
    expect(await auth.getAccessToken()).toBe('a2');
    expect(calls[0]).toMatchObject({ target: 'AWSCognitoIdentityProviderService.InitiateAuth', body: { AuthFlow: 'REFRESH_TOKEN_AUTH', AuthParameters: { REFRESH_TOKEN: 'r1' } } });
    now = 3_000_000; // faltan 10 min: sigue vigente
    expect(await auth.getAccessToken()).toBe('a2');
    expect(calls).toHaveLength(1);
    now = 3_560_000; // faltan < 60 s: se renueva antes de conectar
    expect(await auth.getAccessToken()).toBe('a3');
    expect(calls).toHaveLength(2);
  });

  it('sin aprovisionar no llama a Cognito', async () => {
    const { fetchImpl } = cognito([]);
    const auth = new KioskAuth({ region: 'us-east-1', clientId: 'c', storage: memoryStorage(), fetchImpl });
    await expect(auth.getAccessToken()).rejects.toMatchObject({ code: 'not_provisioned' });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('un refresh token rechazado deja el dispositivo sin aprovisionar', async () => {
    const storage = memoryStorage();
    storage.setItem(REFRESH_TOKEN_KEY, 'viejo');
    const auth = new KioskAuth({ region: 'us-east-1', clientId: 'c', storage, fetchImpl: cognito([{ status: 400 }]).fetchImpl });
    await expect(auth.getAccessToken()).rejects.toMatchObject({ code: 'refresh_failed' });
    expect(auth.isProvisioned()).toBe(false);
  });

  it('credenciales incorrectas y falta de red tienen códigos distintos', async () => {
    const bad = new KioskAuth({ region: 'us-east-1', clientId: 'c', storage: memoryStorage(), fetchImpl: cognito([{ status: 400 }]).fetchImpl });
    await expect(bad.provision('u', 'p')).rejects.toMatchObject({ code: 'invalid_credentials' });
    const offline = new KioskAuth({ region: 'us-east-1', clientId: 'c', storage: memoryStorage(), fetchImpl: cognito([new TypeError('red')]).fetchImpl });
    await expect(offline.provision('u', 'p')).rejects.toBeInstanceOf(KioskAuthError);
    await expect(
      new KioskAuth({ region: 'us-east-1', clientId: 'c', storage: memoryStorage(), fetchImpl: cognito([new TypeError('red')]).fetchImpl }).provision('u', 'p'),
    ).rejects.toMatchObject({ code: 'network' });
  });

  it('la región sale del ID del User Pool', () => {
    expect(regionFromPoolId('us-east-1_AbCdEf123')).toBe('us-east-1');
    expect(regionFromPoolId('sin-region')).toBeNull();
  });
});

// ---- micrófono y sesión real --------------------------------------------------------------------------------------------

describe('RealVoiceSession', () => {
  class FakeAudioContext {
    audioWorklet = { addModule: async () => undefined };
    destination = {};
    async resume() {}
    async close() {}
  }

  beforeEach(() => {
    vi.stubGlobal('AudioContext', FakeAudioContext);
    vi.stubGlobal(
      'AudioWorkletNode',
      class {
        port = { postMessage() {}, onmessage: null };
        connect() {}
      },
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it('pide el micrófono con cancelación de eco y supresión de ruido, y reporta mic_denied', async () => {
    const getUserMedia = vi.fn(async () => {
      throw new DOMException('denied', 'NotAllowedError');
    });
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
    const events: VoiceEvent[] = [];
    const session = new RealVoiceSession('ws://x/ws');
    session.subscribe((e) => events.push(e));
    await session.start();
    expect(getUserMedia).toHaveBeenCalledWith({ audio: expect.objectContaining({ echoCancellation: true, noiseSuppression: true }) });
    expect(events.map((e) => e.type)).toEqual(['phase', 'error']);
    expect(events[0]).toEqual({ type: 'phase', phase: 'requesting_mic' });
    expect(events[1]).toEqual({ type: 'error', code: 'mic_denied' });
  });

  it('sin aprovisionar reporta not_provisioned y cierra el audio', async () => {
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [], getAudioTracks: () => [] })) } });
    const events: VoiceEvent[] = [];
    const session = new RealVoiceSession('wss://x/ws', async () => {
      throw new KioskAuthError('not_provisioned');
    });
    session.subscribe((e) => events.push(e));
    await session.start();
    expect(events.at(-1)).toEqual({ type: 'error', code: 'not_provisioned' });
    expect(events.some((e) => e.type === 'phase' && e.phase === 'connecting')).toBe(true);
  });
});
