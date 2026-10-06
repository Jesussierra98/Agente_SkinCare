import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { VoiceSession } from '../voice/VoiceSession';
import { initialKioskState, isListeningActive, kioskReducer } from './kioskState';

/**
 * Conecta una `VoiceSession` (mock o real) con el estado de la pantalla.
 * `createSession` se llama cada vez que se inicia una conversación.
 */
export function useVoiceSession(createSession: () => VoiceSession) {
  const [state, dispatch] = useReducer(kioskReducer, initialKioskState);
  const sessionRef = useRef<VoiceSession | null>(null);
  const unsubRef = useRef<(() => void) | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Reloj para apagar el indicador de escucha ≤300 ms después del último audio.
  useEffect(() => {
    if (state.phase !== 'active') return;
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, [state.phase]);

  const close = useCallback(() => {
    unsubRef.current?.();
    unsubRef.current = null;
    sessionRef.current?.hangup();
    sessionRef.current = null;
  }, []);

  useEffect(() => close, [close]);

  const start = useCallback(async () => {
    close();
    dispatch({ type: 'start' });
    const session = createSession();
    sessionRef.current = session;
    unsubRef.current = session.subscribe((event) =>
      dispatch({ type: 'voice', event, now: Date.now() }),
    );
    try {
      await session.start();
    } catch {
      dispatch({ type: 'voice', event: { type: 'error', code: 'connect_failed' }, now: Date.now() });
    }
  }, [close, createSession]);

  const hangup = useCallback(() => {
    close();
    dispatch({ type: 'reset' });
  }, [close]);

  const setMuted = useCallback((muted: boolean) => {
    sessionRef.current?.setMuted(muted);
    dispatch({ type: 'setMuted', muted });
  }, []);

  const sendText = useCallback((text: string) => {
    sessionRef.current?.sendText(text);
  }, []);

  const demoShowRoutine = useCallback(() => sessionRef.current?.demoShowRoutine?.(), []);

  const listening = useMemo(() => isListeningActive(state, now), [state, now]);

  return { state, listening, start, hangup, setMuted, sendText, demoShowRoutine };
}
