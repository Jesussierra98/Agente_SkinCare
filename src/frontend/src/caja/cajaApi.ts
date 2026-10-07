import { SAMPLE_CODE, SAMPLE_REC_ID, SAMPLE_ROUTINE } from '../data/sampleRoutine';
import { isValidCode } from '../lib/format';
import { isUuidV4 } from '../lib/qr';

export interface CajaProduct {
  paso: 1 | 2 | 3 | 4;
  sku: string;
  nombre: string;
  marca: string;
  precioCents: number;
  imagenUrl: string;
}

export interface Recommendation {
  recId: string;
  codigoCorto: string;
  fechaCreacion: string;
  estado: 'PENDIENTE' | 'ATENDIDA';
  fechaAtendida: string | null;
  productos: CajaProduct[];
}

export type CajaErrorCode =
  | 'invalid_credentials'
  | 'network'
  | 'not_found'
  | 'invalid_code'
  | 'server'
  | 'expired'
  | 'forbidden';

export class CajaError extends Error {
  constructor(public readonly code: CajaErrorCode) {
    super(code);
  }
}

/** Contrato de la API de Caja. El prototipo usa `MockCajaApi`; la versión real llama a API Gateway. */
export interface CajaApi {
  login(user: string, password: string): Promise<void>;
  logout(): void;
  isLoggedIn(): boolean;
  /** Acepta `rec_id` (UUID) o código corto. */
  getRecommendation(idOrCode: string): Promise<Recommendation>;
  markAttended(recId: string): Promise<Recommendation>;
  /** Un asesor confirma que atenderá la derivación de una sesión del Kiosco (`session_id` UUID). */
  confirmHandoff(sessionId: string): Promise<void>;
}

/** Precio de la API (`"1234.50"`) a centavos enteros, sin pasar por punto flotante. `null` si el formato no es válido. */
export function priceToCents(price: string): number | null {
  const match = /^(\d{1,6})(?:\.(\d{1,2}))?$/.exec(price.trim());
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] ?? '').padEnd(2, '0'));
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
export { parseQrUrl } from '../lib/qr';

/** Implementación en memoria para revisar el diseño. Usuario de prueba: `caja` con cualquier contraseña. */
export class MockCajaApi implements CajaApi {
  private loggedIn = false;
  private store = new Map<string, Recommendation>();

  constructor() {
    const base = (recId: string, code: string, estado: Recommendation['estado']): Recommendation => ({
      recId,
      codigoCorto: code,
      fechaCreacion: new Date(Date.now() - 20 * 60_000).toISOString(),
      estado,
      fechaAtendida: estado === 'ATENDIDA' ? new Date().toISOString() : null,
      productos: SAMPLE_ROUTINE.map((p) => ({
        paso: p.paso,
        sku: p.sku,
        nombre: p.nombre,
        marca: p.marca,
        precioCents: p.precioCents,
        imagenUrl: p.imagenUrl,
      })),
    });
    this.store.set(SAMPLE_CODE, base(SAMPLE_REC_ID, SAMPLE_CODE, 'PENDIENTE'));
    this.store.set('ATN-222', base('7b1d2c9e-5a44-4f3b-8c21-0d9e6f7a1b22', 'ATN-222', 'ATENDIDA'));
  }

  async login(user: string, password: string): Promise<void> {
    await delay(500);
    if (user.trim().toLowerCase() !== 'caja' || password.length === 0) {
      throw new CajaError('invalid_credentials');
    }
    this.loggedIn = true;
  }

  logout(): void {
    this.loggedIn = false;
  }

  isLoggedIn(): boolean {
    return this.loggedIn;
  }

  async getRecommendation(idOrCode: string): Promise<Recommendation> {
    if (!this.loggedIn) throw new CajaError('expired');
    await delay(400);
    const raw = idOrCode.trim();
    let found: Recommendation | undefined;
    if (isUuidV4(raw)) {
      found = [...this.store.values()].find((r) => r.recId === raw.toLowerCase());
    } else {
      const code = raw.toUpperCase();
      if (!isValidCode(code)) throw new CajaError('invalid_code');
      found = this.store.get(code);
    }
    if (!found) throw new CajaError('not_found');
    return structuredClone(found);
  }

  async markAttended(recId: string): Promise<Recommendation> {
    if (!this.loggedIn) throw new CajaError('expired');
    await delay(500);
    const rec = [...this.store.values()].find((r) => r.recId === recId);
    if (!rec) throw new CajaError('not_found');
    // Idempotente: si ya estaba atendida se conservan los valores originales.
    if (rec.estado === 'PENDIENTE') {
      rec.estado = 'ATENDIDA';
      rec.fechaAtendida = new Date().toISOString();
    }
    return structuredClone(rec);
  }

  async confirmHandoff(sessionId: string): Promise<void> {
    if (!this.loggedIn) throw new CajaError('expired');
    await delay(300);
    if (!isUuidV4(sessionId)) throw new CajaError('not_found');
  }
}
