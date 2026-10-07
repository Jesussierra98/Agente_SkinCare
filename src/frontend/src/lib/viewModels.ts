import type { Reading, RoutineItem } from '../data/sampleRoutine';
import { formatPrice, truncate } from './format';

export const REASON_MAX_CHARS = 120;
export const READING_TITLE_MAX_CHARS = 150;
export const READINGS_MAX = 5;

export interface ProductCardViewModel {
  paso: RoutineItem['paso'];
  marca: string;
  nombre: string;
  razon: string;
  sku: string;
  precio: string;
  modoUso: string;
}

/** Lo que muestra una tarjeta de producto: la razón se corta a 120 caracteres y el precio va con miles y 2 decimales. */
export function productCardViewModel(item: RoutineItem): ProductCardViewModel {
  return {
    paso: item.paso,
    marca: item.marca,
    nombre: item.nombre,
    razon: truncate(item.razonCatalogo, REASON_MAX_CHARS),
    sku: item.sku,
    precio: formatPrice(item.precioCents),
    modoUso: item.modoUso,
  };
}

/** Títulos de la tarjeta de lecturas: los primeros 5, en orden, cada uno de hasta 150 caracteres. */
export function readingsViewModel(articulos: Reading[]): string[] {
  return articulos.slice(0, READINGS_MAX).map((a) => truncate(a.titulo, READING_TITLE_MAX_CHARS));
}
