import { Cashier } from './caja/Cashier';
import { Kiosk } from './kiosk/Kiosk';

/** Enrutado mínimo por ruta: `/` es el Kiosco y `/caja` la Vista_Caja. */
export function App() {
  const path = window.location.pathname.replace(/\/+$/, '');
  if (path === '/caja') return <Cashier />;
  return <Kiosk />;
}
