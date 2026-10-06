/** Funciones puras de audio y conversión usadas por la sesión de voz. */

/** Float32 [-1, 1] → Int16 (PCM16). */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]!));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

/** Int16 (PCM16) → Float32 [-1, 1]. */
export function pcm16ToFloat(samples: Int16Array): Float32Array {
  const out = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i]!;
    out[i] = s < 0 ? s / 0x8000 : s / 0x7fff;
  }
  return out;
}

/** Bytes → base64, en bloques para no desbordar la pila con audio grande. */
export function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/** Nivel RMS de un marco PCM16 (0 a 1). */
export function rmsPcm16(samples: Int16Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = samples[i]! / 0x8000;
    sum += v * v;
  }
  return Math.sqrt(sum / samples.length);
}

/** `"1234.50"` → 123450 centavos, sin pasar por punto flotante. */
export function decimalStringToCents(value: string): number {
  const m = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim());
  if (!m) return 0;
  const cents = Number(m[2]) * 100 + Number((m[3] ?? '').padEnd(2, '0') || 0);
  return m[1] === '-' ? -cents : cents;
}
