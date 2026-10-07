/**
 * El JWT viaja en el handshake del WebSocket por `Sec-WebSocket-Protocol` (DD-02), porque el navegador no puede
 * poner el header `Authorization`: `["base64UrlBearerAuthorization.<token-base64url>", "base64UrlBearerAuthorization"]`.
 */
export const BEARER_PREFIX = 'base64UrlBearerAuthorization.';
export const BEARER_PROTOCOL = 'base64UrlBearerAuthorization';

/** Texto → base64url sin relleno (UTF-8). */
function toBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(encoded: string): string {
  const padded = encoded.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (encoded.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

/** Subprotocolo con el token codificado. Solo contiene caracteres base64url tras el prefijo. */
export function encodeBearerSubprotocol(token: string): string {
  return BEARER_PREFIX + toBase64Url(token);
}

/** Token original, o `null` si el subprotocolo no tiene el prefijo o la codificación no es válida. */
export function decodeBearerSubprotocol(protocol: string): string | null {
  if (!protocol.startsWith(BEARER_PREFIX)) return null;
  const encoded = protocol.slice(BEARER_PREFIX.length);
  if (!/^[A-Za-z0-9_-]*$/.test(encoded) || encoded.length % 4 === 1) return null;
  try {
    return fromBase64Url(encoded);
  } catch {
    return null;
  }
}

/** Lista para `new WebSocket(url, protocols)`. */
export function bearerProtocols(token: string): string[] {
  return [encodeBearerSubprotocol(token), BEARER_PROTOCOL];
}
