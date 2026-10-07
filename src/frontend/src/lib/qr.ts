const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** `true` si el texto es un UUID versión 4. */
export function isUuidV4(text: string): boolean {
  return UUID_V4.test(text);
}

/** URL del QR de la Caja: `https://<dominio>/caja?rec=<uuid>`. El dominio puede traer o no el esquema. */
export function buildQrUrl(domain: string, recId: string): string {
  const base = /^[a-z][a-z0-9+.-]*:\/\//i.test(domain) ? domain : `https://${domain}`;
  return `${base.replace(/\/+$/, '')}/caja?rec=${recId}`;
}

/**
 * Extrae el `rec_id` de una URL de QR `https://<dominio>/caja?rec=<uuid>`.
 * Con `allowedHost` (por ejemplo `tienda.ejemplo.com`), un QR de otro dominio se rechaza.
 */
export function parseQrUrl(text: string, allowedHost?: string): string | null {
  try {
    const url = new URL(text.trim());
    if (allowedHost && url.host.toLowerCase() !== allowedHost.trim().toLowerCase()) return null;
    if (url.pathname.replace(/\/+$/, '') !== '/caja') return null;
    const rec = url.searchParams.get('rec');
    return rec && isUuidV4(rec) ? rec : null;
  } catch {
    return null;
  }
}
