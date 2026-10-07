import { AuthenticationDetails, CognitoUser, CognitoUserPool } from 'amazon-cognito-identity-js';
import { CajaError } from './cajaApi';
import type { AuthTokens, TokenAuth } from './httpCajaApi';

const REJECTED = new Set([
  'NotAuthorizedException',
  'UserNotFoundException',
  'UserNotConfirmedException',
  'PasswordResetRequiredException',
  'InvalidParameterException',
]);

/** Traduce un error de Cognito a un código de Caja. Credenciales malas y usuario inexistente se tratan igual. */
export function mapCognitoError(err: unknown): CajaError {
  const e = err as { code?: string; name?: string; message?: string } | null;
  const name = e?.code ?? e?.name ?? '';
  if (REJECTED.has(name)) return new CajaError('invalid_credentials');
  if (name === 'NetworkError' || err instanceof TypeError || /network|failed to fetch/i.test(e?.message ?? '')) {
    return new CajaError('network');
  }
  return new CajaError('server');
}

/** Cognito con `USER_SRP_AUTH`: la contraseña nunca viaja en claro. La sesión del SDK vive en `sessionStorage`. */
export class CognitoAuth implements TokenAuth {
  private readonly pool: CognitoUserPool;

  constructor(userPoolId: string, clientId: string, storage: Storage = sessionStorage) {
    this.pool = new CognitoUserPool({ UserPoolId: userPoolId, ClientId: clientId, Storage: storage });
  }

  signIn(user: string, password: string): Promise<AuthTokens> {
    return new Promise<AuthTokens>((resolve, reject) => {
      const cognitoUser = new CognitoUser({ Username: user, Pool: this.pool, Storage: sessionStorage });
      cognitoUser.authenticateUser(new AuthenticationDetails({ Username: user, Password: password }), {
        onSuccess: (session) => {
          const access = session.getAccessToken();
          resolve({ accessToken: access.getJwtToken(), expiresAt: access.getExpiration() * 1000 });
        },
        onFailure: (err) => reject(mapCognitoError(err)),
        // Contraseña temporal: la Vista_Caja no gestiona cambios de contraseña.
        newPasswordRequired: () => reject(new CajaError('invalid_credentials')),
      });
    });
  }

  signOut(): void {
    this.pool.getCurrentUser()?.signOut();
  }
}
