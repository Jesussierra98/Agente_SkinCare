/** Precio como en el diseño: `$1,234.50` (sin "MXN"). Recibe centavos enteros. */
export function formatPrice(cents: number): string {
  const sign = cents < 0 ? '-' : '';
  const abs = Math.abs(Math.round(cents));
  const whole = Math.floor(abs / 100)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const frac = (abs % 100).toString().padStart(2, '0');
  return `${sign}$${whole}.${frac}`;
}

/** Recorta a `max` caracteres y agrega puntos suspensivos si se cortó. */
export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, Math.max(0, max - 1)).trimEnd() + '…';
}

/** Fecha y hora local como `05/10/2026 18:45`. */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => n.toString().padStart(2, '0');
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** `^[A-Z]{3}-\d{3}$` tras `strip` y mayúsculas. */
export function isValidCode(raw: string): boolean {
  return /^[A-Z]{3}-\d{3}$/.test(raw.trim().toUpperCase());
}
