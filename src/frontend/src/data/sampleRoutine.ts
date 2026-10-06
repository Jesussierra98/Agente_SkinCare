/** Datos de muestra para el prototipo. No son del catálogo real. */

export interface RoutineItem {
  paso: 1 | 2 | 3 | 4;
  sku: string;
  nombre: string;
  marca: string;
  /** Precio en centavos (evita errores de punto flotante). */
  precioCents: number;
  imagenUrl: string;
  razonCatalogo: string;
  modoUso: string;
}

export interface Reading {
  titulo: string;
}

export const SAMPLE_ROUTINE: RoutineItem[] = [
  {
    paso: 1,
    sku: '000375947',
    nombre: 'Gel limpiador suave',
    marca: 'MARCA EJEMPLO',
    precioCents: 68000,
    imagenUrl: '',
    razonCatalogo: 'Limpia sin resecar y respeta la barrera de la piel.',
    modoUso: 'Aplica sobre la piel húmeda, masajea 30 segundos y enjuaga con agua tibia. Mañana y noche.',
  },
  {
    paso: 2,
    sku: '000412083',
    nombre: 'Sérum de niacinamida',
    marca: 'MARCA EJEMPLO',
    precioCents: 124950,
    imagenUrl: '',
    razonCatalogo: 'Ayuda a unificar el tono y a controlar el brillo.',
    modoUso: 'Después de limpiar, aplica 3 a 4 gotas en rostro y cuello. Usa por la noche.',
  },
  {
    paso: 3,
    sku: '000518274',
    nombre: 'Crema hidratante ligera',
    marca: 'MARCA EJEMPLO',
    precioCents: 89900,
    imagenUrl: '',
    razonCatalogo: 'Hidratación de 24 horas con textura ligera.',
    modoUso: 'Aplica una capa uniforme sobre el sérum, mañana y noche.',
  },
  {
    paso: 4,
    sku: '000620391',
    nombre: 'Protector solar FPS 50',
    marca: 'MARCA EJEMPLO',
    precioCents: 102000,
    imagenUrl: '',
    razonCatalogo: 'Protección de amplio espectro de uso diario.',
    modoUso: 'Aplica como último paso de la mañana y reaplica cada 2 horas al sol.',
  },
];

export const SAMPLE_READINGS: { ingrediente: string; articulos: Reading[] } = {
  ingrediente: 'niacinamida',
  articulos: [
    { titulo: 'Niacinamide and skin barrier function: a narrative review' },
    { titulo: 'Topical niacinamide in facial hyperpigmentation: a randomized study' },
  ],
};

export const SAMPLE_CODE = 'ABC-234';
export const SAMPLE_REC_ID = '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11';
