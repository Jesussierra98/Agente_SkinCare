/** Ayudas solo para pruebas. */

/** `true` si el texto contiene únicamente caracteres base64url (sin relleno). */
export function buildBearerCheck(encoded: string): boolean {
  return /^[A-Za-z0-9_-]*$/.test(encoded);
}
