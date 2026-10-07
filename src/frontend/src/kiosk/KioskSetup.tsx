import { useState, type FormEvent } from 'react';
import { createKioskAuth, type KioskAuth } from '../auth/kioskAuth';

/**
 * Aprovisionamiento único del dispositivo (`/kiosk/setup`, DD-03): el responsable escribe el usuario de servicio
 * del Kiosco una sola vez; después solo se conserva el refresh token (la contraseña no se guarda).
 * Pantalla interna sin diseño entregado: ver la fila 17 de `ui-reference.md`.
 */
export function KioskSetup({ auth = createKioskAuth() }: { auth?: KioskAuth | null }) {
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!auth || !user.trim() || !password) {
      setMessage({ kind: 'error', text: 'Escribe el usuario y la contraseña del dispositivo.' });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await auth.provision(user.trim(), password);
      setPassword('');
      setMessage({ kind: 'ok', text: 'Dispositivo configurado. Ya puedes abrir el Kiosco.' });
    } catch (err) {
      const code = err instanceof Error ? err.message : '';
      setMessage({
        kind: 'error',
        text: code === 'network' ? 'No hay conexión con el servicio de acceso. Inténtalo de nuevo.' : 'Usuario o contraseña incorrectos.',
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-6">
      <h1 className="font-display text-[40px] leading-[44px] text-ink">Configurar dispositivo</h1>
      {!auth ? (
        <p role="alert" className="text-[15px] text-danger">
          Falta la configuración de acceso (variables VITE_AGENT_WS_URL, VITE_COGNITO_USER_POOL_ID y
          VITE_COGNITO_KIOSCO_CLIENT_ID).
        </p>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <label className="flex flex-col gap-1 text-[14px] text-ink">
            Usuario
            <input
              value={user}
              onChange={(e) => setUser(e.target.value)}
              autoComplete="username"
              maxLength={64}
              className="h-12 border border-ink bg-white px-3 text-[16px]"
            />
          </label>
          <label className="flex flex-col gap-1 text-[14px] text-ink">
            Contraseña
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              maxLength={128}
              className="h-12 border border-ink bg-white px-3 text-[16px]"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="h-12 bg-brand text-[16px] font-semibold text-white disabled:opacity-70"
          >
            Guardar dispositivo
          </button>
          {message ? (
            <p role={message.kind === 'error' ? 'alert' : 'status'} className={message.kind === 'error' ? 'text-danger' : 'text-brand'}>
              {message.text}
            </p>
          ) : null}
        </form>
      )}
    </main>
  );
}
