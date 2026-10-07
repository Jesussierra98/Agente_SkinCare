import { useEffect, useState } from 'react';
import { columnsForWidth } from '../lib/layout';

/** Columnas de la rutina para el ancho actual de la ventana (4, 2 o 1). */
export function useColumns(): 1 | 2 | 4 {
  const [columns, setColumns] = useState(() => columnsForWidth(window.innerWidth));
  useEffect(() => {
    const onResize = () => setColumns(columnsForWidth(window.innerWidth));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return columns;
}
