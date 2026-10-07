import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import axe from 'axe-core';
import { describe, expect, it, vi } from 'vitest';
import type { Recommendation } from './cajaApi';
import { LoginScreen } from './LoginScreen';
import { ResultScreen } from './ResultScreen';
import { ScannerScreen } from './ScannerScreen';

vi.mock('./qrReader', () => ({
  READER_ID: 'qr-reader',
  startCameraScan: vi.fn(async () => {
    throw new DOMException('denegado', 'NotAllowedError');
  }),
  scanImageFile: vi.fn(),
}));

const rec: Recommendation = {
  recId: '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11',
  codigoCorto: 'ABC-234',
  fechaCreacion: '2026-10-05T18:45:00Z',
  estado: 'PENDIENTE',
  fechaAtendida: null,
  productos: [1, 2, 3, 4].map((paso) => ({ paso: paso as 1 | 2 | 3 | 4, sku: `S${paso}`, nombre: `P${paso}`, marca: 'M', precioCents: 10000 * paso, imagenUrl: '' })),
};

async function violations(container: HTMLElement): Promise<string[]> {
  const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return results.violations.map((v) => `${v.id}: ${v.help}`);
}

describe('accesibilidad de la Caja (axe-core)', () => {
  it('acceso', async () => {
    const { container } = render(<LoginScreen onLogin={vi.fn()} />);
    expect(await violations(container)).toEqual([]);
  });

  it('lectura de QR', async () => {
    const { container } = render(<ScannerScreen onExit={vi.fn()} onSearchCode={vi.fn()} onScanned={vi.fn()} busy={false} error={null} />);
    expect(await violations(container)).toEqual([]);
  });

  it('recomendación', async () => {
    const { container } = render(<ResultScreen rec={rec} marking={false} error={null} onExit={vi.fn()} onMark={vi.fn()} onReadAnother={vi.fn()} />);
    expect(await violations(container)).toEqual([]);
  });
});

describe('lectura de QR', () => {
  it('con la cámara denegada muestra el mensaje y deja disponibles la foto y el código manual', async () => {
    render(<ScannerScreen onExit={vi.fn()} onSearchCode={vi.fn()} onScanned={vi.fn()} busy={false} error={null} />);
    fireEvent.click(screen.getByRole('button', { name: /Escanear con la cámara/ }));
    expect(await screen.findByText(/No se pudo abrir la cámara/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Subir la foto del QR/ })).toBeEnabled();
    expect(screen.getByPlaceholderText('ABC-123')).toBeEnabled();
    await waitFor(() => expect(screen.getByRole('button', { name: /Escanear con la cámara/ })).toBeEnabled());
  });
});
