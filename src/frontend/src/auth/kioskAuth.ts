/**
 * Autenticación del dispositivo del Kiosco (DD-03): un usuario de servicio por iPad, en el grupo `kiosco`.
 * Se aprovisiona una sola vez en `/kiosk/setup` (usuario y contraseña → `USER_PASSWORD_AUTH`); de ahí en adelante
 * solo se guarda el refresh token y antes de cada conexión se pide un access token nuevo con `REFRESH_TOKEN_AUTH`.
 * La contraseña nunca se guarda. Las llamadas van directo a la API pública de Cognito con `fetch` (sin SDK).
 */

export type KioskAuthErrorCode = 'invalid_credentials' | 'not_provisioned' | 'refresh_failed' | 'network';

export class KioskAuthError extends Error {
  constructor(public readonly code: KioskAuthErrorCode) {
    super(code);
  }
}

export const REFRESH_TOKEN_KEY = 'skincare.kiosk.refreshToken';
/** Un access token se reutiliza mientras le queden más de este margen de vida. */
const EXPIRY_MARGIN_MS = 60_000;
const REQUEST_TIMEOUT_MS = 10_000;

export interface KioskAuthOptions {
  region: string;
  clientId: string;
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  fetchImpl?: typeof fetch;
  now?: () => number;
}

interface AuthResult {
  AccessToken: string;
  ExpiresIn: number;
  RefreshToken?: string;
}

/** Región a partir del ID del User Pool (`us-east-1_AbCdEf` → `us-east-1`). */
export function regionFromPoolId(poolId: string): string | null {
  const m = /^([a-z]{2}(?:-[a-z]+)+-\d)_[A-Za-z0-9]+$/.exec(poolId.trim());
  return m ? m[1]! : null;
}

export class KioskAuth {
  private readonly storage: NonNullable<KioskAuthOptions['storage']>;
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;
  private cached: { token: string; expiresAt: number } | null = null;

  constructor(private readonly opts: KioskAuthOptions) {
    this.storage = opts.storage ?? window.localStorage;
    this.fetchImpl = opts.fetchImpl ?? ((...args) => fetch(...args));
    this.now = opts.now ?? Date.now;
  }

  isProvisioned(): boolean {
    return Boolean(this.storage.getItem(REFRESH_TOKEN_KEY));
  }

  /** Olvida el dispositivo: hay que volver a `/kiosk/setup`. */
  clear(): void {
    this.storage.removeItem(REFRESH_TOKEN_KEY);
    this.cached = null;
  }

  /** Aprovisionamiento único. Guarda solo el refresh token. */
  async provision(username: string, password: string): Promise<void> {
    const result = await this.initiateAuth('USER_PASSWORD_AUTH', { USERNAME: username, PASSWORD: password }, 'invalid_credentials');
    if (!result.RefreshToken) throw new KioskAuthError('invalid_credentials');
    this.storage.setItem(REFRESH_TOKEN_KEY, result.RefreshToken);
    this.remember(result);
  }

  /** Access token vigente; renueva con el refresh token cuando falta poco para que venza. */
  async getAccessToken(): Promise<string> {
    if (this.cached && this.cached.expiresAt - this.now() > EXPIRY_MARGIN_MS) return this.cached.token;
    const refresh = this.storage.getItem(REFRESH_TOKEN_KEY);
    if (!refresh) throw new KioskAuthError('not_provisioned');
    const result = await this.initiateAuth('REFRESH_TOKEN_AUTH', { REFRESH_TOKEN: refresh }, 'refresh_failed');
    this.remember(result);
    return result.AccessToken;
  }

  private remember(result: AuthResult): void {
    this.cached = { token: result.AccessToken, expiresAt: this.now() + result.ExpiresIn * 1000 };
  }

  private async initiateAuth(
    flow: 'USER_PASSWORD_AUTH' | 'REFRESH_TOKEN_AUTH',
    parameters: Record<string, string>,
    rejectedAs: 'invalid_credentials' | 'refresh_failed',
  ): Promise<AuthResult> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let response: Response;
    try {
      response = await this.fetchImpl(`https://cognito-idp.${this.opts.region}.amazonaws.com/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-amz-json-1.1',
          'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
        },
        body: JSON.stringify({ AuthFlow: flow, ClientId: this.opts.clientId, AuthParameters: parameters }),
        signal: controller.signal,
      });
    } catch {
      throw new KioskAuthError('network');
    } finally {
      clearTimeout(timer);
    }
    if (!response.ok) {
      if (response.status === 400 || response.status === 401) {
        // Un refresh token vencido o revocado deja al dispositivo sin aprovisionar.
        if (flow === 'REFRESH_TOKEN_AUTH') this.clear();
        throw new KioskAuthError(rejectedAs);
      }
      throw new KioskAuthError('network');
    }
    const body = (await response.json()) as { AuthenticationResult?: AuthResult };
    if (!body.AuthenticationResult?.AccessToken) throw new KioskAuthError(rejectedAs);
    return body.AuthenticationResult;
  }
}

export interface KioskEnv {
  VITE_AGENT_WS_URL?: string;
  VITE_COGNITO_USER_POOL_ID?: string;
  VITE_COGNITO_KIOSCO_CLIENT_ID?: string;
}

/** `true` si están las tres variables que activan el modo con AgentCore y Cognito. */
export function hasKioskAuth(env: KioskEnv): boolean {
  return Boolean(env.VITE_AGENT_WS_URL && env.VITE_COGNITO_USER_POOL_ID && env.VITE_COGNITO_KIOSCO_CLIENT_ID);
}

export function createKioskAuth(env: KioskEnv = import.meta.env as KioskEnv): KioskAuth | null {
  if (!hasKioskAuth(env)) return null;
  const region = regionFromPoolId(env.VITE_COGNITO_USER_POOL_ID as string);
  if (!region) return null;
  return new KioskAuth({ region, clientId: env.VITE_COGNITO_KIOSCO_CLIENT_ID as string });
}
