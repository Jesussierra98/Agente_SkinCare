import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Cashier } from './Cashier';
import type { Recommendation } from './cajaApi';
import { ResultScreen } from './ResultScreen';

const SLOW = { timeout: 4000 };

function recommendation(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    recId: '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11',
    codigoCorto: 'ABC-234',
    fechaCreacion: '2026-10-05T18:45:00Z',
    estado: 'PENDIENTE',
    fechaAtendida: null,
    productos: [
      { paso: 3, sku: 'H1', nombre: 'Crema', marca: 'B', precioCents: 120050, imagenUrl: '' },
      { paso: 1, sku: 'L1', nombre: 'Gel', marca: 'A', precioCents: 68000, imagenUrl: '' },
      { paso: 4, sku: 'S1', nombre: 'Solar', marca: 'C', precioCents: 90000, imagenUrl: '' },
      { paso: 2, sku: 'T1', nombre: 'Suero', marca: 'D', precioCents: 150000, imagenUrl: '' },
    ],
    ...overrides,
  };
}

function renderResult(rec: Recommendation, props: Partial<Parameters<typeof ResultScreen>[0]> = {}) {
  return render(
    <ResultScreen rec={rec} onExit={vi.fn()} onMark={vi.fn()} onReadAnother={vi.fn()} marking={false} error={null} {...props} />,
  );
}

// ---- Feature: skincare-voice-advisor, Property 40: la vista de Caja refleja el estado -----------------------

describe('ResultScreen', () => {
  it('con la recomendación pendiente, el botón está habilitado', () => {
    renderResult(recommendation());
    expect(screen.getByRole('status')).toHaveTextContent('PENDIENTE');
    expect(screen.getByRole('button', { name: 'Marcar como atendida' })).toBeEnabled();
  });

  it('con la recomendación atendida, el botón se deshabilita y se muestra la fecha', () => {
    renderResult(recommendation({ estado: 'ATENDIDA', fechaAtendida: '2026-10-05T19:02:11Z' }));
    expect(screen.getByRole('status')).toHaveTextContent('ATENDIDA');
    expect(screen.getByRole('button', { name: 'Marcar como atendida' })).toBeDisabled();
    expect(screen.getByText(/atendida \d{2}\/\d{2}\/\d{4} \d{2}:\d{2}/)).toBeInTheDocument();
  });

  it('muestra los productos ordenados por paso con SKU y precio', () => {
    renderResult(recommendation());
    const rows = screen.getAllByRole('listitem').map((li) => li.textContent ?? '');
    expect(rows).toHaveLength(4);
    expect(rows[0]).toContain('LIMPIEZA');
    expect(rows[1]).toContain('TRATAMIENTO');
    expect(rows[2]).toContain('HIDRATACIÓN');
    expect(rows[3]).toContain('PROTECCIÓN SOLAR');
    expect(rows[2]).toContain('SKU H1');
    expect(rows[2]).toContain('$1,200.50');
  });

  it('mientras guarda, el botón se deshabilita y un error deja todo listo para reintentar', () => {
    const { rerender } = renderResult(recommendation(), { marking: true });
    expect(screen.getByRole('button', { name: 'Guardando…' })).toBeDisabled();
    rerender(
      <ResultScreen rec={recommendation()} onExit={vi.fn()} onMark={vi.fn()} onReadAnother={vi.fn()} marking={false} error="No se completó el despacho. Inténtalo de nuevo." />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('No se completó el despacho');
    expect(screen.getByRole('button', { name: 'Marcar como atendida' })).toBeEnabled();
  });
});

// ---- flujo completo con la API simulada (sin credenciales ni cámara) ------------------------------------

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function login(user = 'caja', password = 'cualquiera') {
  fill('Usuario', user);
  fill('Contraseña', password);
  fireEvent.click(screen.getByRole('button', { name: 'Entrar' }));
}

describe('Cashier (API simulada)', () => {
  it('sin sesión solo se ve el acceso, nunca datos de rutinas', () => {
    render(<Cashier />);
    expect(screen.getByRole('heading', { name: 'Acceso de caja' })).toBeInTheDocument();
    expect(screen.queryByText('Recomendación')).not.toBeInTheDocument();
    expect(screen.queryByText('Lee el QR del cliente')).not.toBeInTheDocument();
  });

  it('campos vacíos: avisa sin intentar entrar', () => {
    render(<Cashier />);
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Escribe tu usuario y tu contraseña.');
  });

  it('credenciales rechazadas: conserva el usuario, vacía la contraseña y no dice cuál falló', async () => {
    render(<Cashier />);
    await login('otro', 'mala');
    expect(await screen.findByText('Usuario o contraseña incorrectos.', {}, SLOW)).toBeInTheDocument();
    expect(screen.getByLabelText('Usuario')).toHaveValue('otro');
    expect(screen.getByLabelText('Contraseña')).toHaveValue('');
  });

  it('entrar, buscar por código y marcar como atendida', async () => {
    render(<Cashier />);
    await login();
    expect(await screen.findByRole('heading', { name: 'Lee el QR del cliente' }, SLOW)).toBeInTheDocument();

    fill('O escribe el código que aparece bajo el QR', 'abc-234');
    fireEvent.click(screen.getByRole('button', { name: 'Buscar' }));
    expect(await screen.findByRole('heading', { name: 'ABC-234' }, SLOW)).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('PENDIENTE');

    fireEvent.click(screen.getByRole('button', { name: 'Marcar como atendida' }));
    await within(document.body).findByText('ATENDIDA', {}, SLOW);
    expect(screen.getByRole('button', { name: 'Marcar como atendida' })).toBeDisabled();
  });

  it('un código con buen formato pero inexistente responde que no se encontró', async () => {
    render(<Cashier />);
    await login();
    await screen.findByRole('heading', { name: 'Lee el QR del cliente' }, SLOW);
    fill('O escribe el código que aparece bajo el QR', 'ZZZ-999');
    fireEvent.click(screen.getByRole('button', { name: 'Buscar' }));
    expect(await screen.findByText(/No encontramos esa recomendación/, {}, SLOW)).toBeInTheDocument();
  });

  it('un código con mal formato se rechaza sin consultar', async () => {
    render(<Cashier />);
    await login();
    await screen.findByRole('heading', { name: 'Lee el QR del cliente' }, SLOW);
    fill('O escribe el código que aparece bajo el QR', 'ABC');
    fireEvent.click(screen.getByRole('button', { name: 'Buscar' }));
    expect(screen.getByRole('alert')).toHaveTextContent('El código debe tener 3 letras, un guion y 3 números');
  });

  it('una imagen que no es JPEG ni PNG se rechaza con un mensaje', async () => {
    render(<Cashier />);
    await login();
    await screen.findByRole('heading', { name: 'Lee el QR del cliente' }, SLOW);
    const input = screen.getByLabelText('Subir la foto del QR');
    fireEvent.change(input, { target: { files: [new File(['x'], 'qr.gif', { type: 'image/gif' })] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('La imagen debe ser JPEG o PNG de hasta 10 MB');
  });
});
