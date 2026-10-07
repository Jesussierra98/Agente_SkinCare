import { Html5Qrcode } from 'html5-qrcode';

/** Id del contenedor donde la biblioteca coloca el video de la cámara. */
export const READER_ID = 'qr-reader';

export interface CameraScan {
  stop(): Promise<void>;
}

function release(reader: Html5Qrcode): void {
  try {
    reader.clear();
  } catch {
    // ya estaba liberado
  }
}

/** Abre la cámara trasera y avisa con el texto de cada QR leído. Lanza si no hay permiso o cámara. */
export async function startCameraScan(onText: (text: string) => void): Promise<CameraScan> {
  const reader = new Html5Qrcode(READER_ID, { verbose: false });
  await reader.start(
    { facingMode: 'environment' },
    { fps: 10, qrbox: { width: 170, height: 170 } },
    (text) => onText(text),
    () => undefined, // cuadros sin QR: se ignoran
  );
  return {
    async stop() {
      try {
        await reader.stop();
      } catch {
        // ya estaba detenida
      }
      release(reader);
    },
  };
}

/** Lee el QR de una foto. Lanza si la imagen no contiene un QR legible. */
export async function scanImageFile(file: File): Promise<string> {
  const reader = new Html5Qrcode(READER_ID, { verbose: false });
  try {
    return await reader.scanFile(file, false);
  } finally {
    release(reader);
  }
}
