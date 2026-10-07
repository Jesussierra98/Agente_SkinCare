import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { CameraIcon, UploadIcon } from '../components/Icons';
import { isValidCode } from '../lib/format';
import { CajaFrame } from './CajaFrame';
import { READER_ID, scanImageFile, startCameraScan, type CameraScan } from './qrReader';

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

export function validateImage(type: string, size: number): boolean {
  return (type === 'image/jpeg' || type === 'image/png') && size <= MAX_IMAGE_BYTES;
}

interface Props {
  onExit: () => void;
  /** Busca por código escrito. */
  onSearchCode: (code: string) => void;
  /** Recibe el texto del QR leído con la cámara o desde una foto. */
  onScanned: (text: string) => void;
  busy: boolean;
  error: string | null;
  initialCode?: string;
  /**
   * Solo para el prototipo con datos simulados: si se indica, la cámara y la foto no leen nada real y
   * entregan este texto, para poder revisar el flujo sin un QR.
   */
  simulatedQr?: string;
}

export function ScannerScreen({ onExit, onSearchCode, onScanned, busy, error, initialCode = '', simulatedQr }: Props) {
  const [code, setCode] = useState(initialCode);
  const [localError, setLocalError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const camera = useRef<CameraScan | null>(null);

  // La cámara se apaga al salir de la pantalla.
  useEffect(() => {
    return () => {
      void camera.current?.stop();
      camera.current = null;
    };
  }, []);

  function submit(e: FormEvent) {
    e.preventDefault();
    const normalized = code.trim().toUpperCase();
    if (!isValidCode(normalized)) {
      setLocalError('El código debe tener 3 letras, un guion y 3 números, por ejemplo ABC-123.');
      return;
    }
    setLocalError(null);
    onSearchCode(normalized);
  }

  async function startCamera() {
    setLocalError(null);
    setScanning(true);
    if (simulatedQr !== undefined) {
      setTimeout(() => {
        setScanning(false);
        onScanned(simulatedQr);
      }, 1400);
      return;
    }
    try {
      let delivered = false;
      camera.current = await startCameraScan((text) => {
        if (delivered) return; // una sola lectura por apertura de la cámara
        delivered = true;
        void camera.current?.stop();
        camera.current = null;
        setScanning(false);
        onScanned(text);
      });
    } catch {
      camera.current = null;
      setScanning(false);
      setLocalError('No se pudo abrir la cámara. Revisa el permiso del navegador, sube una foto o escribe el código.');
    }
  }

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!validateImage(file.type, file.size)) {
      setLocalError('La imagen debe ser JPEG o PNG de hasta 10 MB. Intenta de nuevo o escribe el código.');
      return;
    }
    setLocalError(null);
    if (simulatedQr !== undefined) {
      onScanned(simulatedQr);
      return;
    }
    try {
      onScanned(await scanImageFile(file));
    } catch {
      setLocalError('No pudimos leer un QR en la foto. Intenta de nuevo o escribe el código.');
    }
  }

  const message = localError ?? error;

  return (
    <CajaFrame onExit={onExit} tall>
      <main className="flex flex-1 flex-col">
        <h1 className="mt-[22px] font-display text-[36px] font-normal leading-[44px] text-ink">
          Lee el QR del cliente
        </h1>

        <div className="relative mt-[18px] h-[300px] overflow-hidden bg-ink">
          <div id={READER_ID} className="absolute inset-0" />
          <div
            className={`pointer-events-none absolute left-1/2 top-[40px] h-[170px] w-[170px] -translate-x-1/2 border-2 border-dashed ${
              scanning ? 'border-white' : 'border-white/90'
            }`}
            aria-hidden="true"
          />
          <p className="absolute inset-x-0 bottom-5 text-center text-[15px] font-medium text-white">
            {scanning ? 'Leyendo…' : 'Vista de la cámara · apunta al código'}
          </p>
        </div>

        <button
          type="button"
          onClick={() => void startCamera()}
          disabled={busy || scanning}
          className="mt-5 flex h-[60px] items-center justify-center gap-3 bg-brand text-[18px] font-semibold text-white hover:bg-[#194035] disabled:opacity-60"
        >
          <CameraIcon />
          Escanear con la cámara
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy || scanning}
          className="mt-5 flex h-[56px] items-center justify-center gap-3 border border-ink bg-transparent text-[18px] text-ink hover:bg-white disabled:opacity-60"
        >
          <UploadIcon />
          Subir la foto del QR
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png"
          className="hidden"
          onChange={(e) => void onFile(e)}
          aria-label="Subir la foto del QR"
        />

        <form onSubmit={submit} noValidate className="mt-auto pt-10">
          <label htmlFor="codigo" className="block text-[14px] text-ink">
            O escribe el código que aparece bajo el QR
          </label>
          <div className="mt-2 flex gap-3">
            <input
              id="codigo"
              type="text"
              value={code}
              maxLength={7}
              autoCapitalize="characters"
              autoComplete="off"
              placeholder="ABC-123"
              onChange={(e) => setCode(e.target.value)}
              className="h-[52px] min-w-0 flex-1 border border-field bg-white px-4 font-sans text-[20px] uppercase tracking-[0.08em] text-ink placeholder:text-faint"
            />
            <button
              type="submit"
              disabled={busy}
              className="h-[52px] w-[90px] bg-ink text-[16px] font-semibold text-white hover:bg-black disabled:opacity-60"
            >
              {busy ? 'Buscando…' : 'Buscar'}
            </button>
          </div>
          {message ? (
            <p role="alert" className="mt-3 text-[15px] text-danger">
              {message}
            </p>
          ) : null}
        </form>
      </main>
    </CajaFrame>
  );
}
