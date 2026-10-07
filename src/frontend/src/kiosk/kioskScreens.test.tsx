import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import axe from 'axe-core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HandoffConfirm } from '../caja/HandoffConfirm';
import { MockCajaApi } from '../caja/cajaApi';
import { SAMPLE_READINGS, SAMPLE_ROUTINE } from '../data/sampleRoutine';
import type { VoiceEvent, VoiceListener, VoiceSession } from '../voice/VoiceSession';
import { ConversationScreen } from './ConversationScreen';
import { IdleScreen } from './IdleScreen';
import { KioskSetup } from './KioskSetup';
import { RoutineScreen } from './RoutineScreen';
import { STRINGS } from './i18n';
import { initialKioskState, kioskReducer, type KioskState } from './kioskState';
import { useVoiceSession } from './useVoiceSession';

const t = STRINGS.es;

function stateAfter(...events: VoiceEvent[]): KioskState {
  return events.reduce(
    (s, event) => kioskReducer(s, { type: 'voice', event, now: 1_000 }),
    kioskReducer(initialKioskState, { type: 'start' }),
  );
}

const withRoutine = (items = SAMPLE_ROUTINE) =>
  stateAfter(
    { type: 'ready', language: 'es' },
    { type: 'transcript', role: 'asesor', text: 'Tu rutina está lista', final: true },
    { type: 'routine', items },
    { type: 'saved', recId: '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11', code: 'ABC-234', qrUrl: 'https://tienda.example/caja?rec=3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11' },
    { type: 'readings', ingrediente: SAMPLE_READINGS.ingrediente, articulos: SAMPLE_READINGS.articulos },
  );

async function violations(container: HTMLElement): Promise<string[]> {
  // El contraste de color no se puede calcular en jsdom: se revisa a mano con herramientas de contraste.
  const results = await axe.run(container, { rules: { 'color-contrast': { enabled: false } } });
  return results.violations.map((v) => `${v.id}: ${v.help}`);
}

afterEach(() => {
  vi.restoreAllMocks();
  window.innerWidth = 1024;
});

// ---- axe-core en cada pantalla (Req. 18.10, 18.11) -------------------------------------------------------------------------

describe('accesibilidad (axe-core)', () => {
  it('pantalla de inicio', async () => {
    const { container } = render(<IdleScreen t={t} onStart={vi.fn()} starting={false} error={null} />);
    expect(await violations(container)).toEqual([]);
  });

  it('conversación', async () => {
    const state = stateAfter({ type: 'ready', language: 'es' }, { type: 'transcript', role: 'asesor', text: 'Hola', final: true });
    const { container } = render(
      <ConversationScreen t={t} state={state} listening={true} onHangup={vi.fn()} onMute={vi.fn()} onSend={vi.fn()} />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it('tu rutina con QR y lecturas', async () => {
    const { container } = render(<RoutineScreen t={t} state={withRoutine()} onHangup={vi.fn()} />);
    expect(await violations(container)).toEqual([]);
  });
});

// ---- sin formularios ni listas de preguntas (Req. 7.1) ---------------------------------------------------------------------

describe('conversación sin formularios de preguntas', () => {
  it('solo hay el campo de texto del diseño: ninguna lista de opciones, casillas ni selectores', () => {
    const state = stateAfter({ type: 'ready', language: 'es' });
    const { container } = render(
      <ConversationScreen t={t} state={state} listening={false} onHangup={vi.fn()} onMute={vi.fn()} onSend={vi.fn()} />,
    );
    expect(container.querySelectorAll('form')).toHaveLength(1);
    expect(container.querySelectorAll('input[type="radio"], input[type="checkbox"], select, textarea')).toHaveLength(0);
    expect(container.querySelectorAll('input[type="text"]')).toHaveLength(1);
  });
});

// ---- indicador de escucha ---------------------------------------------------------------------------------------------------

describe('estado de la voz en pantalla', () => {
  it('con la voz perdida se muestra el aviso y se conserva la rutina, el código y la transcripción', () => {
    const lost = kioskReducer(withRoutine(), { type: 'voice', event: { type: 'connection_lost', reason: 'closed' }, now: 2_000 });
    render(<RoutineScreen t={t} state={lost} onHangup={vi.fn()} />);
    expect(screen.getByText(t.voiceEnded)).toBeInTheDocument();
    expect(screen.queryByText(t.advisorListening)).not.toBeInTheDocument();
    expect(screen.getByText('ABC-234')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem').length).toBeGreaterThanOrEqual(4);
  });

  it('con la voz activa se lee «El asesor sigue escuchando»', () => {
    render(<RoutineScreen t={t} state={withRoutine()} onHangup={vi.fn()} />);
    expect(screen.getByText(t.advisorListening)).toBeInTheDocument();
  });
});

// ---- tarjetas de producto ----------------------------------------------------------------------------------------------------

describe('rutina', () => {
  function cards() {
    return within(screen.getAllByRole('list')[0]!).getAllByRole('heading', { level: 2 });
  }

  it.each([
    [1280, 'repeat(4'],
    [1024, 'repeat(4'],
    [800, 'repeat(2'],
    [600, 'repeat(2'],
    [420, 'repeat(1'],
  ])('con %ipx de ancho usa %s columnas', (width, expected) => {
    window.innerWidth = width;
    const { container } = render(<RoutineScreen t={t} state={withRoutine()} onHangup={vi.fn()} />);
    expect(container.querySelector('ol')!.getAttribute('style')).toContain(expected);
  });

  it('un paso sin producto muestra una tarjeta que lo indica y conserva los demás', () => {
    render(<RoutineScreen t={t} state={withRoutine(SAMPLE_ROUTINE.filter((i) => i.paso !== 3))} onHangup={vi.fn()} />);
    expect(screen.getAllByText(t.noProductForStep)).toHaveLength(1);
    expect(cards()).toHaveLength(4);
    expect(screen.getByText('Gel limpiador suave')).toBeInTheDocument();
  });

  it('si la imagen falla muestra la de reemplazo y conserva marca, nombre, razón, SKU, precio y el botón', () => {
    const { container } = render(
      <RoutineScreen t={t} state={withRoutine([{ ...SAMPLE_ROUTINE[0]!, imagenUrl: 'https://cdn.example/no-existe.jpg' }, ...SAMPLE_ROUTINE.slice(1)])} onHangup={vi.fn()} />,
    );
    const img = container.querySelector('img')!;
    fireEvent.error(img);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getAllByText(t.imagePlaceholder).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Gel limpiador suave')).toBeInTheDocument();
    expect(screen.getByText(/SKU 000375947/)).toBeInTheDocument();
    expect(screen.getByText('$680.00')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: t.howToUse })).toHaveLength(4);
  });

  it('el popover se abre, cierra otros, se cierra con Escape, con «Cerrar» o fuera, y devuelve el foco al botón', async () => {
    render(<RoutineScreen t={t} state={withRoutine()} onHangup={vi.fn()} />);
    const [first, second] = screen.getAllByRole('button', { name: t.howToUse });

    fireEvent.click(first!);
    expect(screen.getByRole('dialog')).toHaveTextContent(SAMPLE_ROUTINE[0]!.modoUso);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());

    fireEvent.click(second!); // abrir otro cierra el anterior
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(screen.getByRole('dialog')).toHaveTextContent(SAMPLE_ROUTINE[1]!.modoUso);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(second).toHaveFocus());

    fireEvent.click(first!);
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: t.close }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(first).toHaveFocus());

    fireEvent.click(first!);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('la nota del pie y la leyenda de PubMed están tal cual', () => {
    render(<RoutineScreen t={t} state={withRoutine()} onHangup={vi.fn()} />);
    expect(screen.getByText(t.routineNote)).toBeInTheDocument();
    expect(screen.getByText(t.readingsSource)).toBeInTheDocument();
  });
});

// ---- colgar y errores ---------------------------------------------------------------------------------------------------------

class FakeSession implements VoiceSession {
  listeners = new Set<VoiceListener>();
  hangups = 0;
  async start() {}
  hangup() {
    this.hangups += 1;
  }
  sendText() {}
  setMuted() {}
  subscribe(l: VoiceListener) {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }
  emit(e: VoiceEvent) {
    this.listeners.forEach((l) => l(e));
  }
}

describe('useVoiceSession', () => {
  it('colgar cierra la sesión y deja la pantalla de inicio en menos de 1 segundo', async () => {
    const session = new FakeSession();
    const { result } = renderHook(() => useVoiceSession(() => session));
    await act(async () => {
      await result.current.start();
    });
    act(() => session.emit({ type: 'ready', language: 'es' }));
    expect(result.current.state.phase).toBe('active');

    const before = performance.now();
    act(() => result.current.hangup());
    expect(performance.now() - before).toBeLessThan(1000);
    expect(session.hangups).toBe(1);
    expect(result.current.state.phase).toBe('ended');
    expect(result.current.listening).toBe(false);
  });

  it.each([
    ['mic_denied', /micrófono/i],
    ['connect_failed', /No se pudo iniciar/],
    ['timeout', /tardó demasiado/],
    ['not_provisioned', /no está configurado/],
  ] as const)('el error %s se muestra con un mensaje y permite reintentar', (code, message) => {
    const onStart = vi.fn();
    render(<IdleScreen t={t} onStart={onStart} starting={false} error={code} />);
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    fireEvent.click(screen.getByRole('button', { name: t.startCta }));
    expect(onStart).toHaveBeenCalledTimes(1);
  });
});

// ---- pantallas internas sin diseño ------------------------------------------------------------------------------------------

describe('KioskSetup', () => {
  it('sin configuración de acceso lo avisa', () => {
    render(<KioskSetup auth={null} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/VITE_COGNITO_KIOSCO_CLIENT_ID/);
  });

  it('aprovisiona con usuario y contraseña y vacía la contraseña', async () => {
    const provision = vi.fn(async () => undefined);
    render(<KioskSetup auth={{ provision } as never} />);
    fireEvent.change(screen.getByLabelText('Usuario'), { target: { value: 'kiosco-01' } });
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: 'secreta' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar dispositivo' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/configurado/));
    expect(provision).toHaveBeenCalledWith('kiosco-01', 'secreta');
    expect(screen.getByLabelText('Contraseña')).toHaveValue('');
  });

  it('no envía con campos vacíos', () => {
    const provision = vi.fn();
    render(<KioskSetup auth={{ provision } as never} />);
    fireEvent.click(screen.getByRole('button', { name: 'Guardar dispositivo' }));
    expect(provision).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});

describe('HandoffConfirm', () => {
  const SESSION = '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11';

  it('pide iniciar sesión y luego confirma la derivación', async () => {
    const api = new MockCajaApi();
    const spy = vi.spyOn(api, 'confirmHandoff');
    render(<HandoffConfirm sessionId={SESSION} api={api} />);
    fireEvent.change(screen.getByLabelText(/Usuario/), { target: { value: 'caja' } });
    fireEvent.change(screen.getByLabelText(/Contraseña/), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: /Entrar/ }));
    const confirm = await screen.findByRole('button', { name: /Confirmar que lo atenderé/ }, { timeout: 3000 });
    fireEvent.click(confirm);
    expect(await screen.findByRole('status')).toHaveTextContent(/Confirmado/);
    expect(spy).toHaveBeenCalledWith(SESSION);
  });

  it('una derivación inexistente muestra el error y permite reintentar', async () => {
    const api = new MockCajaApi();
    await api.login('caja', 'x');
    render(<HandoffConfirm sessionId="no-es-uuid" api={api} />);
    fireEvent.click(screen.getByRole('button', { name: /Confirmar que lo atenderé/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/ya no existe/);
    expect(screen.getByRole('button', { name: /Confirmar que lo atenderé/ })).toBeEnabled();
  });
});
