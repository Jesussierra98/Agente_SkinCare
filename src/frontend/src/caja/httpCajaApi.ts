import { CajaError, priceToCents, type CajaApi, type CajaProduct, type Recommendation } from './cajaApi';

/** Tokens de la sesión de caja. `expiresAt` en milisegundos desde la época. */
export interface AuthTokens {
  accessToken: string;
  expiresAt: number;
}

/** Autenticación de la Vista_Caja (Cognito con SRP en producción; falsa en las pruebas). */
export interface TokenAuth {
  /** Lanza `CajaError('invalid_credentials' | 'network' | 'server')`. */
  signIn(user: string, password: string): Promise<AuthTokens>;
  signOut(): void;
}

export interface HttpCajaApiOptions {
  baseUrl: string;
  auth: TokenAuth;
  fetchFn?: typeof fetch;
  /** Donde se guarda la sesión. Por defecto `sessionStorage`: se pierde al cerrar la pestaña. */
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  now?: () => number;
  timeoutMs?: number;
}

const STORAGE_KEY = 'caja.session';
export const REQUEST_TIMEOUT_MS = 10_000;

function isStep(value: unknown): value is 1 | 2 | 3 | 4 {
  return value === 1 || value === 2 || value === 3 || value === 4;
}

/** Valida y convierte la respuesta de `GET /recomendacion/{id}`. Lanza `CajaError('server')` si no cumple el contrato. */
export function toRecommendation(body: unknown): Recommendation {
  const bad = () => new CajaError('server');
  if (typeof body !== 'object' || body === null) throw bad();
  const b = body as Record<string, unknown>;
  const { rec_id, codigo_corto, fecha_creacion, estado, fecha_atendida, productos } = b;
  if (typeof rec_id !== 'string' || typeof codigo_corto !== 'string' || typeof fecha_creacion !== 'string') throw bad();
  if (estado !== 'PENDIENTE' && estado !== 'ATENDIDA') throw bad();
  if (fecha_atendida !== null && fecha_atendida !== undefined && typeof fecha_atendida !== 'string') throw bad();
  if (!Array.isArray(productos)) throw bad();

  const items: CajaProduct[] = productos.map((raw: unknown) => {
    if (typeof raw !== 'object' || raw === null) throw bad();
    const p = raw as Record<string, unknown>;
    const cents = typeof p.precio === 'string' ? priceToCents(p.precio) : null;
    if (!isStep(p.paso) || typeof p.sku !== 'string' || typeof p.nombre !== 'string' || typeof p.marca !== 'string' || cents === null) {
      throw bad();
    }
    return {
      paso: p.paso,
      sku: p.sku,
      nombre: p.nombre,
      marca: p.marca,
      precioCents: cents,
      imagenUrl: typeof p.imagen_url === 'string' ? p.imagen_url : '',
    };
  });

  return {
    recId: rec_id,
    codigoCorto: codigo_corto,
    fechaCreacion: fecha_creacion,
    estado,
    fechaAtendida: typeof fecha_atendida === 'string' ? fecha_atendida : null,
    productos: items,
  };
}

/** Cliente real de la API_Caja: JWT en `Authorization`, 10 s de tiempo límite y errores con códigos propios. */
export class HttpCajaApi implements CajaApi {
  private readonly baseUrl: string;
  private readonly auth: TokenAuth;
  private readonly fetchFn: typeof fetch;
  private readonly storage: NonNullable<HttpCajaApiOptions['storage']>;
  private readonly now: () => number;
  private readonly timeoutMs: number;

  constructor(options: HttpCajaApiOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, '');
    this.auth = options.auth;
    this.fetchFn = options.fetchFn ?? ((...args) => fetch(...args));
    this.storage = options.storage ?? sessionStorage;
    this.now = options.now ?? Date.now;
    this.timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS;
  }

  async login(user: string, password: string): Promise<void> {
    let tokens: AuthTokens;
    try {
      tokens = await this.withTimeout(this.auth.signIn(user, password));
    } catch (err) {
      throw err instanceof CajaError ? err : new CajaError('server');
    }
    this.storage.setItem(STORAGE_KEY, JSON.stringify(tokens));
  }

  logout(): void {
    this.storage.removeItem(STORAGE_KEY);
    this.auth.signOut();
  }

  isLoggedIn(): boolean {
    return this.session() !== null;
  }

  getRecommendation(idOrCode: string): Promise<Recommendation> {
    return this.request('GET', `/recomendacion/${encodeURIComponent(idOrCode.trim())}`);
  }

  markAttended(recId: string): Promise<Recommendation> {
    return this.request('POST', `/recomendacion/${encodeURIComponent(recId)}/atendida`);
  }

  // ---- internos ------------------------------------------------------------------------------------
  private session(): AuthTokens | null {
    try {
      const raw = this.storage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Partial<AuthTokens>;
      if (typeof parsed.accessToken !== 'string' || typeof parsed.expiresAt !== 'number') return null;
      return parsed.expiresAt > this.now() ? (parsed as AuthTokens) : null;
    } catch {
      return null;
    }
  }

  private withTimeout<T>(promise: Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => reject(new CajaError('network')), this.timeoutMs);
      promise.then(
        (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        (err) => {
          clearTimeout(timer);
          reject(err);
        },
      );
    });
  }

  async confirmHandoff(sessionId: string): Promise<void> {
    await this.send('POST', `/derivaciones/${encodeURIComponent(sessionId)}/confirmar`);
  }

  private async request(method: 'GET' | 'POST', path: string): Promise<Recommendation> {
    const response = await this.send(method, path);
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new CajaError('server');
    }
    return toRecommendation(body);
  }

  /** Llamada autenticada con el tiempo límite y la traducción de estados HTTP a `CajaError`. */
  private async send(method: 'GET' | 'POST', path: string): Promise<Response> {
    const session = this.session();
    if (!session) {
      this.logout();
      throw new CajaError('expired');
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      response = await this.fetchFn(this.baseUrl + path, {
        method,
        headers: { Authorization: `Bearer ${session.accessToken}`, Accept: 'application/json' },
        signal: controller.signal,
      });
    } catch {
      throw new CajaError('network'); // sin red o tiempo agotado
    } finally {
      clearTimeout(timer);
    }

    if (response.status === 401) {
      this.logout(); // token vencido o inválido: la sesión local se cierra
      throw new CajaError('expired');
    }
    if (response.status === 403) throw new CajaError('forbidden');
    if (response.status === 404) throw new CajaError('not_found');
    if (response.status === 400) throw new CajaError('invalid_code');
    if (!response.ok) throw new CajaError('server');
    return response;
  }
}
