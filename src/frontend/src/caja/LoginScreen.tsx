import { useState, type FormEvent } from 'react';
import { CajaFrame } from './CajaFrame';
import { CajaError } from './cajaApi';

interface Props {
  onLogin: (user: string, password: string) => Promise<void>;
  /** Aviso previo, por ejemplo "La sesión expiró". */
  notice?: string | null;
}

export function LoginScreen({ onLogin, notice }: Props) {
  const [user, setUser] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (user.trim() === '' || password === '') {
      setError('Escribe tu usuario y tu contraseña.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onLogin(user.trim(), password);
    } catch (err) {
      if (err instanceof CajaError && err.code === 'network') {
        // Sin conexión: se conservan ambos valores.
        setError('No hay conexión con el servidor. Revisa la red e inténtalo de nuevo.');
      } else if (err instanceof CajaError && err.code === 'server') {
        // Falla del servicio: no es culpa de las credenciales, así que no se vacía nada.
        setError('No se pudo iniciar sesión por un problema del servicio. Inténtalo de nuevo en un momento.');
      } else {
        // Credenciales rechazadas: se conserva el usuario, se vacía la contraseña y no se dice cuál falló.
        setError('Usuario o contraseña incorrectos.');
        setPassword('');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <CajaFrame>
      <main className="flex flex-1 flex-col">
        <div className="mt-[160px]">
          <h1 className="font-display text-[44px] font-normal leading-[50px] text-ink">
            Acceso de caja
          </h1>
          <p className="mt-2 text-[17px] leading-6 text-muted">
            Entra con el usuario del dispositivo para consultar las rutinas recomendadas.
          </p>

          <form onSubmit={submit} noValidate className="mt-[29px]">
            {notice ? (
              <p role="status" className="mb-4 text-[15px] text-ink">
                {notice}
              </p>
            ) : null}
            <label htmlFor="usuario" className="block text-[15px] text-ink">
              Usuario
            </label>
            <input
              id="usuario"
              type="text"
              value={user}
              maxLength={64}
              autoComplete="username"
              autoCapitalize="none"
              onChange={(e) => setUser(e.target.value)}
              className="mt-[7px] h-[52px] w-full border border-field bg-white px-4 text-[18px] text-ink"
            />
            <label htmlFor="contrasena" className="mt-[17px] block text-[15px] text-ink">
              Contraseña
            </label>
            <input
              id="contrasena"
              type="password"
              value={password}
              maxLength={128}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              className="mt-[7px] h-[52px] w-full border border-field bg-white px-4 text-[18px] text-ink"
            />
            {error ? (
              <p role="alert" className="mt-4 text-[15px] text-danger">
                {error}
              </p>
            ) : null}
            <button
              type="submit"
              disabled={busy}
              className="mt-6 h-[60px] w-full bg-brand text-[18px] font-semibold text-white hover:bg-[#194035] disabled:opacity-60"
            >
              {busy ? 'Entrando…' : 'Entrar'}
            </button>
          </form>
        </div>

        <p className="mt-auto pt-10 text-[14px] leading-[21px] text-muted">
          Uso interno. Si no puedes entrar, pide apoyo a [RESPONSABLE DEL PILOTO].
        </p>
      </main>
    </CajaFrame>
  );
}
