import { MockCajaApi, type CajaApi } from './cajaApi';
import { CognitoAuth } from './cognitoAuth';
import { HttpCajaApi } from './httpCajaApi';

export interface CajaEnv {
  VITE_CAJA_API_URL?: string;
  VITE_COGNITO_USER_POOL_ID?: string;
  VITE_COGNITO_CAJA_CLIENT_ID?: string;
  VITE_STORE_DOMAIN?: string;
}

/** `true` si están las tres variables que hacen falta para hablar con la API real y con Cognito. */
export function hasRealCaja(env: CajaEnv): boolean {
  return Boolean(env.VITE_CAJA_API_URL && env.VITE_COGNITO_USER_POOL_ID && env.VITE_COGNITO_CAJA_CLIENT_ID);
}

/** API real si el entorno la configura; si no, la simulada del prototipo (usuario `caja`, cualquier contraseña). */
export function createCajaApi(env: CajaEnv = import.meta.env as CajaEnv): CajaApi {
  if (hasRealCaja(env)) {
    return new HttpCajaApi({
      baseUrl: env.VITE_CAJA_API_URL as string,
      auth: new CognitoAuth(env.VITE_COGNITO_USER_POOL_ID as string, env.VITE_COGNITO_CAJA_CLIENT_ID as string),
    });
  }
  return new MockCajaApi();
}

/** Dominio permitido en el QR (`VITE_STORE_DOMAIN`, por ejemplo `tienda.ejemplo.com`); sin él no se restringe. */
export function allowedQrHost(env: CajaEnv = import.meta.env as CajaEnv): string | undefined {
  const raw = env.VITE_STORE_DOMAIN?.trim();
  if (!raw) return undefined;
  try {
    return new URL(raw.includes('://') ? raw : `https://${raw}`).host;
  } catch {
    return undefined;
  }
}
