import { useMemo, useState } from 'react';
import { SAMPLE_REC_ID } from '../data/sampleRoutine';
import { CajaError, MockCajaApi, parseQrUrl, type CajaApi, type Recommendation } from './cajaApi';
import { allowedQrHost, createCajaApi } from './config';
import { LoginScreen } from './LoginScreen';
import { ResultScreen } from './ResultScreen';
import { ScannerScreen } from './ScannerScreen';

type View = 'login' | 'scan' | 'result';

function messageFor(err: unknown): string {
  if (err instanceof CajaError) {
    switch (err.code) {
      case 'not_found':
        return 'No encontramos esa recomendación. Revisa el código o lee otro QR.';
      case 'invalid_code':
        return 'El código debe tener 3 letras, un guion y 3 números, por ejemplo ABC-123.';
      case 'forbidden':
        return 'Este usuario no tiene permiso para consultar recomendaciones.';
      case 'network':
      case 'server':
        return 'No se pudo completar la consulta. Inténtalo de nuevo.';
      default:
        return 'Ocurrió un error. Inténtalo de nuevo.';
    }
  }
  return 'Ocurrió un error. Inténtalo de nuevo.';
}

export function Cashier() {
  // API real (Cognito + API Gateway) si el entorno la configura; si no, la simulada: usuario `caja`, cualquier contraseña.
  const api: CajaApi = useMemo(() => createCajaApi(), []);
  const simulated = api instanceof MockCajaApi;
  const [view, setView] = useState<View>('login');
  const [notice, setNotice] = useState<string | null>(null);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastCode, setLastCode] = useState('');

  async function lookup(idOrCode: string) {
    setBusy(true);
    setError(null);
    setLastCode(idOrCode);
    try {
      const found = await api.getRecommendation(idOrCode);
      setRec(found);
      setView('result');
    } catch (err) {
      if (err instanceof CajaError && err.code === 'expired') {
        api.logout();
        setNotice('La sesión expiró. Entra de nuevo; conservamos el código que escribiste.');
        setView('login');
      } else {
        setError(messageFor(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function mark() {
    if (!rec) return;
    setBusy(true);
    setError(null);
    try {
      setRec(await api.markAttended(rec.recId));
    } catch (err) {
      // Se mantiene PENDIENTE y el botón habilitado.
      setError(
        err instanceof CajaError && err.code === 'not_found'
          ? 'No se completó el despacho: la recomendación ya no existe.'
          : 'No se completó el despacho. Inténtalo de nuevo.',
      );
    } finally {
      setBusy(false);
    }
  }

  function exit() {
    api.logout();
    setRec(null);
    setError(null);
    setNotice(null);
    setView('login');
  }

  if (view === 'login' || !api.isLoggedIn()) {
    return (
      <LoginScreen
        notice={notice}
        onLogin={async (user, password) => {
          await api.login(user, password);
          setNotice(null);
          setView('scan');
        }}
      />
    );
  }

  if (view === 'result' && rec) {
    return (
      <ResultScreen
        rec={rec}
        marking={busy}
        error={error}
        onExit={exit}
        onMark={mark}
        onReadAnother={() => {
          setRec(null);
          setError(null);
          setView('scan');
        }}
      />
    );
  }

  return (
    <ScannerScreen
      onExit={exit}
      busy={busy}
      error={error}
      initialCode={lastCode}
      onSearchCode={lookup}
      onScanned={(text) => {
        const recId = parseQrUrl(text, allowedQrHost());
        if (recId === null) {
          setError('Ese QR no corresponde a una recomendación. Prueba con otro o escribe el código.');
          return;
        }
        void lookup(recId);
      }}
      // Con datos simulados, leer el QR equivale a abrir la rutina de muestra.
      simulatedQr={simulated ? `https://tienda.example/caja?rec=${SAMPLE_REC_ID}` : undefined}
    />
  );
}
