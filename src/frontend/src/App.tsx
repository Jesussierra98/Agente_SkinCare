import { Cashier } from './caja/Cashier';
import { HandoffConfirm } from './caja/HandoffConfirm';
import { Kiosk } from './kiosk/Kiosk';
import { KioskSetup } from './kiosk/KioskSetup';

/** Enrutado mínimo por ruta: `/` Kiosco, `/kiosk/setup` alta del dispositivo, `/caja` Caja y `/derivacion/<id>` confirmación. */
export function App() {
  const path = window.location.pathname.replace(/\/+$/, '');
  if (path === '/caja') return <Cashier />;
  if (path === '/kiosk/setup') return <KioskSetup />;
  const handoff = /^\/derivacion\/([0-9a-f-]{36})$/i.exec(path);
  if (handoff) return <HandoffConfirm sessionId={handoff[1]!} />;
  return <Kiosk />;
}
