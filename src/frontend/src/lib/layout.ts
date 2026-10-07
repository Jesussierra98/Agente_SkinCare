/** Columnas de la rutina según el ancho del viewport: 4 (≥1024 px), 2 (600 a 1023 px) o 1 (<600 px). Req. 18.3. */
export function columnsForWidth(width: number): 1 | 2 | 4 {
  if (width >= 1024) return 4;
  if (width >= 600) return 2;
  return 1;
}
