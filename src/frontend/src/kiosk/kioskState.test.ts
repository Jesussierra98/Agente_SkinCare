import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { SAMPLE_READINGS, SAMPLE_ROUTINE } from '../data/sampleRoutine';
import type { VoiceEvent } from '../voice/VoiceSession';
import { initialKioskState, isListeningActive, kioskReducer, type KioskState } from './kioskState';

const RUNS = { numRuns: 100 };

function apply(state: KioskState, ...events: VoiceEvent[]): KioskState {
  return events.reduce((s, event) => kioskReducer(s, { type: 'voice', event, now: 1_000 }), state);
}

const activeWithRoutine = apply(
  kioskReducer(initialKioskState, { type: 'start' }),
  { type: 'phase', phase: 'requesting_mic' },
  { type: 'phase', phase: 'connecting' },
  { type: 'ready', language: 'es' },
  { type: 'transcript', role: 'asesor', text: 'Hola', final: true },
  { type: 'transcript', role: 'cliente', text: 'Tengo la piel seca', final: true },
  { type: 'routine', items: SAMPLE_ROUTINE },
  { type: 'saved', recId: 'r', code: 'ABC-234', qrUrl: 'https://tienda.example/caja?rec=r' },
  { type: 'readings', ingrediente: SAMPLE_READINGS.ingrediente, articulos: SAMPLE_READINGS.articulos },
  { type: 'activity' },
);

describe('fases del Kiosco', () => {
  it('recorre idle → requesting_mic → connecting → active', () => {
    let state = kioskReducer(initialKioskState, { type: 'start' });
    expect(state.phase).toBe('connecting');
    state = apply(state, { type: 'phase', phase: 'requesting_mic' });
    expect(state.phase).toBe('requesting_mic');
    state = apply(state, { type: 'phase', phase: 'connecting' });
    expect(state.phase).toBe('connecting');
    state = apply(state, { type: 'ready', language: 'en' });
    expect(state).toMatchObject({ phase: 'active', language: 'en' });
  });

  it('un error de micrófono o de conexión vuelve al inicio con el código del error', () => {
    const state = apply(kioskReducer(initialKioskState, { type: 'start' }), { type: 'error', code: 'mic_denied' });
    expect(state).toMatchObject({ phase: 'idle', error: 'mic_denied', routine: null });
  });

  it('colgar deja la fase en ended, sin rutina, QR ni transcripción', () => {
    const state = kioskReducer(activeWithRoutine, { type: 'hangup' });
    expect(state).toMatchObject({ phase: 'ended', routine: null, saved: null, readings: null, transcript: [] });
  });

  it('empezar de nuevo después de colgar limpia el error anterior', () => {
    const failed = apply(kioskReducer(initialKioskState, { type: 'start' }), { type: 'error', code: 'timeout' });
    expect(kioskReducer(failed, { type: 'start' }).error).toBeNull();
  });
});

// ---- Feature: skincare-voice-advisor, Property 46: el indicador de escucha está activo solo con audio reciente --------

describe('indicador de escucha', () => {
  it('está activo exactamente cuando hubo audio hace menos de 300 ms', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 1_000_000 }), fc.integer({ min: 0, max: 2_000 }), (last, elapsed) => {
        const state: KioskState = { ...activeWithRoutine, lastActivityAt: last };
        expect(isListeningActive(state, last + elapsed)).toBe(elapsed < 300);
      }),
      RUNS,
    );
  });

  it('nunca está activo fuera de la fase active, sin audio o después de una interrupción', () => {
    fc.assert(
      fc.property(fc.constantFrom('idle', 'requesting_mic', 'connecting', 'lost', 'ended' as const), (phase) => {
        expect(isListeningActive({ ...activeWithRoutine, phase, lastActivityAt: 1_000 }, 1_050)).toBe(false);
      }),
      RUNS,
    );
    expect(isListeningActive({ ...activeWithRoutine, lastActivityAt: 0 }, 50)).toBe(false);
    expect(isListeningActive(apply(activeWithRoutine, { type: 'interrupt' }), 1_010)).toBe(false);
  });
});

// ---- Feature: skincare-voice-advisor, Property 48: perder la conexión de voz conserva lo mostrado -----------------------

describe('pérdida de la conexión de voz', () => {
  it('conserva rutina, QR, código, lecturas y transcripción, y apaga el indicador', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 30 }), (reason) => {
        const lost = apply(activeWithRoutine, { type: 'connection_lost', reason });
        expect(lost.phase).toBe('lost');
        expect(lost.routine).toEqual(activeWithRoutine.routine);
        expect(lost.saved).toEqual(activeWithRoutine.saved);
        expect(lost.readings).toEqual(activeWithRoutine.readings);
        expect(lost.transcript).toEqual(activeWithRoutine.transcript);
        expect(isListeningActive(lost, 1_010)).toBe(false);
      }),
      RUNS,
    );
  });
});

describe('transcripción', () => {
  it('un parcial del mismo emisor se reemplaza hasta que llega el final', () => {
    const state = apply(
      kioskReducer(initialKioskState, { type: 'start' }),
      { type: 'transcript', role: 'cliente', text: 'Tengo la', final: false },
      { type: 'transcript', role: 'cliente', text: 'Tengo la piel seca', final: true },
      { type: 'transcript', role: 'asesor', text: 'Entiendo', final: false },
    );
    expect(state.transcript.map((l) => [l.role, l.text, l.final])).toEqual([
      ['cliente', 'Tengo la piel seca', true],
      ['asesor', 'Entiendo', false],
    ]);
  });
});
