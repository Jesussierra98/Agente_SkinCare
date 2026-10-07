import fc from 'fast-check';
import { describe, expect, it, vi } from 'vitest';
import { isValidCode } from '../lib/format';
import { CajaError, MockCajaApi, parseQrUrl, priceToCents } from './cajaApi';
import { allowedQrHost, createCajaApi, hasRealCaja } from './config';
import { mapCognitoError } from './cognitoAuth';
import { HttpCajaApi, toRecommendation, type AuthTokens, type TokenAuth } from './httpCajaApi';
import { validateImage } from './ScannerScreen';

const REC_ID = '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11';

// ---- Feature: skincare-voice-advisor, Property 41: los validadores aceptan solo lo permitido ----------------

describe('isValidCode', () => {
  it('acepta 3 letras, guion y 3 números, sin importar mayúsculas ni espacios de borde', () => {
    fc.assert(
      fc.property(
        fc.stringMatching(/^[A-Za-z]{3}$/),
        fc.stringMatching(/^[0-9]{3}$/),
        fc.constantFrom('', ' ', '  '),
        (letters, digits, pad) => isValidCode(`${pad}${letters}-${digits}${pad}`),
      ),
    );
  });

  it.each(['', 'ABC123', 'AB-123', 'ABCD-123', 'ABC-12', 'ABC-1234', '123-ABC', 'A1C-123', 'ABC_123'])(
    'rechaza %j',
    (raw) => expect(isValidCode(raw)).toBe(false),
  );
});

describe('validateImage', () => {
  it('solo JPEG o PNG de hasta 10 MB', () => {
    const limit = 10 * 1024 * 1024;
    expect(validateImage('image/jpeg', limit)).toBe(true);
    expect(validateImage('image/png', 1)).toBe(true);
    expect(validateImage('image/png', limit + 1)).toBe(false);
    expect(validateImage('image/gif', 100)).toBe(false);
    expect(validateImage('application/pdf', 100)).toBe(false);
    expect(validateImage('', 0)).toBe(false);
  });

  it('coincide con la regla para cualquier tipo y tamaño', () => {
    fc.assert(
      fc.property(fc.string(), fc.integer({ min: 0, max: 50 * 1024 * 1024 }), (type, size) => {
        const expected = (type === 'image/jpeg' || type === 'image/png') && size <= 10 * 1024 * 1024;
        return validateImage(type, size) === expected;
      }),
    );
  });
});

describe('parseQrUrl', () => {
  it('extrae el rec_id de una URL de QR válida', () => {
    expect(parseQrUrl(`https://tienda.example/caja?rec=${REC_ID}`)).toBe(REC_ID);
    expect(parseQrUrl(`  https://tienda.example/caja/?rec=${REC_ID}  `)).toBe(REC_ID);
    expect(parseQrUrl(`https://tienda.example/caja?rec=${REC_ID.toUpperCase()}`)).toBe(REC_ID.toUpperCase());
  });

  it.each([
    ['otra ruta', `https://tienda.example/otra?rec=${REC_ID}`],
    ['sin rec', 'https://tienda.example/caja'],
    ['no es UUID', 'https://tienda.example/caja?rec=ABC-234'],
    ['UUID que no es v4', 'https://tienda.example/caja?rec=3f0c6a0e-6f0f-1d0e-9d6b-8f1f3e2a9c11'],
    ['no es una URL', 'ABC-234'],
    ['vacío', ''],
  ])('rechaza: %s', (_label, text) => expect(parseQrUrl(text)).toBeNull());

  it('con un dominio permitido rechaza QR de otros dominios', () => {
    const url = `https://tienda.example/caja?rec=${REC_ID}`;
    expect(parseQrUrl(url, 'tienda.example')).toBe(REC_ID);
    expect(parseQrUrl(url, 'TIENDA.example')).toBe(REC_ID);
    expect(parseQrUrl(url, 'otra.example')).toBeNull();
    expect(parseQrUrl(`https://tienda.example.evil.com/caja?rec=${REC_ID}`, 'tienda.example')).toBeNull();
  });
});

describe('priceToCents', () => {
  it.each([
    ['1234.50', 123450],
    ['0.5', 50],
    ['10', 1000],
    ['0.05', 5],
    ['999999.99', 99999999],
  ])('%s → %i centavos', (price, cents) => expect(priceToCents(price)).toBe(cents));

  it.each(['', 'abc', '-1', '1,234.50', '1.234', '1234567.00', ' ', '1e3', '.5'])('rechaza %j', (price) =>
    expect(priceToCents(price)).toBeNull(),
  );

  it('hace ida y vuelta con cualquier cantidad de centavos', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 99_999_999 }), (cents) => {
        const text = `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`;
        return priceToCents(text) === cents;
      }),
    );
  });
});

// ---- contrato de la respuesta -----------------------------------------------------------------------------

function apiBody(overrides: Record<string, unknown> = {}) {
  return {
    rec_id: REC_ID,
    codigo_corto: 'ABC-234',
    fecha_creacion: '2026-10-05T18:45:00Z',
    estado: 'PENDIENTE',
    fecha_atendida: null,
    productos: [
      { paso: 2, sku: 'T', nombre: 'Suero', marca: 'B', precio: '1500.00', imagen_url: 't.jpg' },
      { paso: 1, sku: 'L', nombre: 'Gel', marca: 'A', precio: '1000.50', imagen_url: 'l.jpg' },
    ],
    total_sugerido: '2500.50',
    ...overrides,
  };
}

describe('toRecommendation', () => {
  it('convierte la respuesta de la API', () => {
    const rec = toRecommendation(apiBody());
    expect(rec).toMatchObject({ recId: REC_ID, codigoCorto: 'ABC-234', estado: 'PENDIENTE', fechaAtendida: null });
    expect(rec.productos[1]).toEqual({ paso: 1, sku: 'L', nombre: 'Gel', marca: 'A', precioCents: 100050, imagenUrl: 'l.jpg' });
  });

  it('acepta ATENDIDA con fecha y una imagen ausente', () => {
    const rec = toRecommendation(
      apiBody({ estado: 'ATENDIDA', fecha_atendida: '2026-10-05T19:02:11Z', productos: [{ paso: 1, sku: 'L', nombre: 'Gel', marca: 'A', precio: '1.00' }] }),
    );
    expect(rec.estado).toBe('ATENDIDA');
    expect(rec.fechaAtendida).toBe('2026-10-05T19:02:11Z');
    expect(rec.productos[0]?.imagenUrl).toBe('');
  });

  it.each([
    ['no es un objeto', null],
    ['falta rec_id', apiBody({ rec_id: undefined })],
    ['estado desconocido', apiBody({ estado: 'OTRO' })],
    ['productos no es lista', apiBody({ productos: 'x' })],
    ['paso fuera de rango', apiBody({ productos: [{ paso: 5, sku: 'L', nombre: 'G', marca: 'A', precio: '1.00' }] })],
    ['precio no es cadena decimal', apiBody({ productos: [{ paso: 1, sku: 'L', nombre: 'G', marca: 'A', precio: 1000 }] })],
    ['precio mal formado', apiBody({ productos: [{ paso: 1, sku: 'L', nombre: 'G', marca: 'A', precio: '1,000' }] })],
  ])('rechaza con error de servidor: %s', (_label, body) => {
    expect(() => toRecommendation(body)).toThrowError(expect.objectContaining({ code: 'server' }));
  });
});

// ---- cliente HTTP ----------------------------------------------------------------------------------------------

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    data,
  };
}

function fakeAuth(result: AuthTokens | Error = { accessToken: 'TOKEN', expiresAt: 10_000 }) {
  const signOut = vi.fn();
  const signIn = vi.fn(async () => {
    if (result instanceof Error) throw result;
    return result;
  });
  return { auth: { signIn, signOut } satisfies TokenAuth, signIn, signOut };
}

function jsonResponse(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function build(fetchFn: typeof fetch, overrides: { now?: () => number; timeoutMs?: number; auth?: TokenAuth } = {}) {
  const storage = memoryStorage();
  const fake = fakeAuth();
  const api = new HttpCajaApi({
    baseUrl: 'https://api.example/',
    auth: overrides.auth ?? fake.auth,
    fetchFn,
    storage,
    now: overrides.now ?? (() => 1_000),
    timeoutMs: overrides.timeoutMs ?? 50,
  });
  return { api, storage, ...fake };
}

describe('HttpCajaApi', () => {
  it('inicia sesión, guarda los tokens y los olvida al salir', async () => {
    const { api, storage, signIn, signOut } = build(vi.fn());
    expect(api.isLoggedIn()).toBe(false);
    await api.login('caja-01', 'secreto');
    expect(signIn).toHaveBeenCalledWith('caja-01', 'secreto');
    expect(api.isLoggedIn()).toBe(true);
    expect(storage.data.size).toBe(1);
    api.logout();
    expect(api.isLoggedIn()).toBe(false);
    expect(storage.data.size).toBe(0);
    expect(signOut).toHaveBeenCalled();
  });

  it('la sesión vence sola cuando pasa expiresAt', async () => {
    let now = 1_000;
    const { api } = build(vi.fn(), { now: () => now });
    await api.login('u', 'p');
    expect(api.isLoggedIn()).toBe(true);
    now = 10_001;
    expect(api.isLoggedIn()).toBe(false);
  });

  it('consulta con el token en Authorization y convierte la respuesta', async () => {
    const fetchFn = vi.fn(async () => jsonResponse(200, apiBody()));
    const { api } = build(fetchFn as unknown as typeof fetch);
    await api.login('u', 'p');
    const rec = await api.getRecommendation(' abc-234 ');
    expect(rec.codigoCorto).toBe('ABC-234');
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('https://api.example/recomendacion/abc-234');
    expect(init.method).toBe('GET');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer TOKEN');
  });

  it('marcar como atendida usa POST y la ruta /atendida', async () => {
    const fetchFn = vi.fn(async () => jsonResponse(200, apiBody({ estado: 'ATENDIDA', fecha_atendida: '2026-10-05T19:02:11Z' })));
    const { api } = build(fetchFn as unknown as typeof fetch);
    await api.login('u', 'p');
    const rec = await api.markAttended(REC_ID);
    expect(rec.estado).toBe('ATENDIDA');
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`https://api.example/recomendacion/${REC_ID}/atendida`);
    expect(init.method).toBe('POST');
  });

  it('sin sesión no llama a la API y pide entrar de nuevo', async () => {
    const fetchFn = vi.fn();
    const { api } = build(fetchFn as unknown as typeof fetch);
    await expect(api.getRecommendation('ABC-234')).rejects.toMatchObject({ code: 'expired' });
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it.each([
    [400, 'invalid_code'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [500, 'server'],
    [503, 'server'],
  ])('el estado HTTP %i es %s', async (status, code) => {
    const { api } = build((async () => jsonResponse(status)) as unknown as typeof fetch);
    await api.login('u', 'p');
    await expect(api.getRecommendation('ABC-234')).rejects.toMatchObject({ code });
    expect(api.isLoggedIn()).toBe(true); // estos errores no cierran la sesión
  });

  it('un 401 cierra la sesión local', async () => {
    const { api, signOut } = build((async () => jsonResponse(401)) as unknown as typeof fetch);
    await api.login('u', 'p');
    await expect(api.getRecommendation('ABC-234')).rejects.toMatchObject({ code: 'expired' });
    expect(api.isLoggedIn()).toBe(false);
    expect(signOut).toHaveBeenCalled();
  });

  it('sin red es un error de red', async () => {
    const { api } = build((async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch);
    await api.login('u', 'p');
    await expect(api.getRecommendation('ABC-234')).rejects.toMatchObject({ code: 'network' });
  });

  it('pasado el tiempo límite se cancela la solicitud y es un error de red', async () => {
    const hang = ((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('abortado', 'AbortError')));
      })) as unknown as typeof fetch;
    const { api } = build(hang, { timeoutMs: 20 });
    await api.login('u', 'p');
    await expect(api.getRecommendation('ABC-234')).rejects.toMatchObject({ code: 'network' });
  });

  it('una respuesta que no cumple el contrato es un error de servidor', async () => {
    const { api } = build((async () => jsonResponse(200, { hola: 1 })) as unknown as typeof fetch);
    await api.login('u', 'p');
    await expect(api.getRecommendation('ABC-234')).rejects.toMatchObject({ code: 'server' });
  });

  it('credenciales rechazadas: el error llega tal cual y no se guarda sesión', async () => {
    const { auth } = fakeAuth(new CajaError('invalid_credentials'));
    const { api, storage } = build(vi.fn(), { auth });
    await expect(api.login('u', 'mala')).rejects.toMatchObject({ code: 'invalid_credentials' });
    expect(api.isLoggedIn()).toBe(false);
    expect(storage.data.size).toBe(0);
  });

  it('un error inesperado al entrar es un error de servidor', async () => {
    const { auth } = fakeAuth(new Error('boom'));
    const { api } = build(vi.fn(), { auth });
    await expect(api.login('u', 'p')).rejects.toMatchObject({ code: 'server' });
  });

  it('si Cognito no responde en el tiempo límite es un error de red', async () => {
    const never: TokenAuth = { signIn: () => new Promise(() => undefined), signOut: () => undefined };
    const { api } = build(vi.fn(), { auth: never, timeoutMs: 20 });
    await expect(api.login('u', 'p')).rejects.toMatchObject({ code: 'network' });
  });

  it('ignora una sesión guardada con formato dañado', () => {
    const { api, storage } = build(vi.fn());
    storage.setItem('caja.session', '{no es json');
    expect(api.isLoggedIn()).toBe(false);
    storage.setItem('caja.session', JSON.stringify({ accessToken: 5, expiresAt: 'x' }));
    expect(api.isLoggedIn()).toBe(false);
  });
});

// ---- Cognito y configuración ------------------------------------------------------------------------------------

describe('mapCognitoError', () => {
  it.each(['NotAuthorizedException', 'UserNotFoundException', 'UserNotConfirmedException', 'PasswordResetRequiredException'])(
    '%s se trata como credenciales rechazadas (sin decir cuál falló)',
    (code) => expect(mapCognitoError({ code }).code).toBe('invalid_credentials'),
  );

  it('los fallos de red son de red', () => {
    expect(mapCognitoError(new TypeError('Failed to fetch')).code).toBe('network');
    expect(mapCognitoError({ code: 'NetworkError' }).code).toBe('network');
    expect(mapCognitoError({ message: 'Network error' }).code).toBe('network');
  });

  it('lo demás es un error de servidor', () => {
    expect(mapCognitoError({ code: 'TooManyRequestsException' }).code).toBe('server');
    expect(mapCognitoError(null).code).toBe('server');
  });
});

describe('configuración', () => {
  const full = {
    VITE_CAJA_API_URL: 'https://api.example',
    VITE_COGNITO_USER_POOL_ID: 'us-east-1_AbCdEf123',
    VITE_COGNITO_CAJA_CLIENT_ID: '1a2b3c4d5e6f7g8h9i0j',
  };

  it('usa la API simulada si falta cualquiera de las variables', () => {
    expect(hasRealCaja({})).toBe(false);
    expect(createCajaApi({})).toBeInstanceOf(MockCajaApi);
    for (const key of Object.keys(full)) {
      const partial: Record<string, string> = { ...full };
      delete partial[key];
      expect(createCajaApi(partial)).toBeInstanceOf(MockCajaApi);
    }
  });

  it('usa la API real si están las tres', () => {
    expect(hasRealCaja(full)).toBe(true);
    expect(createCajaApi(full)).toBeInstanceOf(HttpCajaApi);
  });

  it('el dominio permitido del QR se toma de VITE_STORE_DOMAIN', () => {
    expect(allowedQrHost({})).toBeUndefined();
    expect(allowedQrHost({ VITE_STORE_DOMAIN: 'tienda.example' })).toBe('tienda.example');
    expect(allowedQrHost({ VITE_STORE_DOMAIN: 'https://tienda.example/' })).toBe('tienda.example');
    expect(allowedQrHost({ VITE_STORE_DOMAIN: '   ' })).toBeUndefined();
  });
});
