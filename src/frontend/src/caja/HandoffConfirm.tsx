import { useMemo, useState } from 'react';
import { CajaFrame } from './CajaFrame';
import { CajaError, type CajaApi } from './cajaApi';
import { createCajaApi } from './config';
import { LoginScreen } from './LoginScreen';

type Status = 'ready' | 'busy' | 'done' | 'not_found' | 'error';

/**
 * Pantalla mínima que abre el enlace de la notificación (`/derivacion/<session_id>`, Req. 12.7, DD-14):
 * el asesor entra con su usuario de caja y confirma que atenderá al cliente. Sin diseño entregado (ui-reference, fila 18).
 */
export function HandoffConfirm({ sessionId, api: injected }: { sessionId: string; api?: CajaApi }) {
  const api: CajaApi = useMemo(() => injected ?? createCajaApi(), [injected]);
  const [loggedIn, setLoggedIn] = useState(() => api.isLoggedIn());
  const [status, setStatus] = useState<Status>('ready');

  if (!loggedIn) {
    return (
      <LoginScreen
        onLogin={async (user, password) => {
          await api.login(user, password);
          setLoggedIn(true);
        }}
      />
    );
  }

  async function confirm() {
    setStatus('busy');
    try {
      await api.confirmHandoff(sessionId);
      setStatus('done');
    } catch (err) {
      if (err instanceof CajaError && err.code === 'expired') {
        api.logout();
        setLoggedIn(false);
        setStatus('ready');
      } else {
        setStatus(err instanceof CajaError && err.code === 'not_found' ? 'not_found' : 'error');
      }
    }
  }

  return (
    <CajaFrame>
      <h1 className="font-display text-[34px] leading-[38px] text-ink">Cliente en espera</h1>
      <p className="mt-3 text-[15px] leading-[21px] text-muted">
        Un cliente del Kiosco pidió hablar con un asesor. Confirma que lo atenderás en piso.
      </p>
      {status === 'done' ? (
        <p role="status" className="mt-6 text-[16px] text-brand">
          Confirmado. El cliente ya sabe que un asesor va a atenderlo.
        </p>
      ) : (
        <button
          type="button"
          onClick={confirm}
          disabled={status === 'busy'}
          className="mt-6 h-12 w-full bg-brand text-[16px] font-semibold text-white disabled:opacity-70"
        >
          Confirmar que lo atenderé
        </button>
      )}
      {status === 'not_found' ? (
        <p role="alert" className="mt-4 text-[15px] text-danger">
          Esa derivación ya no existe o el enlace no es válido.
        </p>
      ) : null}
      {status === 'error' ? (
        <p role="alert" className="mt-4 text-[15px] text-danger">
          No se pudo confirmar. Inténtalo de nuevo.
        </p>
      ) : null}
    </CajaFrame>
  );
}
