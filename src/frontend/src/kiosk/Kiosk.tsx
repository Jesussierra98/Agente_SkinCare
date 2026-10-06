import { useCallback, useEffect } from 'react';
import { MockVoiceSession, scenarioFromUrl } from '../voice/MockVoiceSession';
import { RealVoiceSession } from '../voice/RealVoiceSession';
import type { VoiceSession } from '../voice/VoiceSession';
import { ConversationScreen } from './ConversationScreen';
import { IdleScreen } from './IdleScreen';
import { RoutineScreen } from './RoutineScreen';
import { STRINGS } from './i18n';
import { useVoiceSession } from './useVoiceSession';

export function Kiosk() {
  // Sesión real (micrófono + agente con Nova Sonic) por defecto; `?mode=mock` usa el guion simulado.
  const mock = new URLSearchParams(window.location.search).get('mode') === 'mock';
  const createSession = useCallback((): VoiceSession => {
    if (mock) {
      const { scenario, language } = scenarioFromUrl(window.location.search);
      return new MockVoiceSession(scenario, language);
    }
    return new RealVoiceSession(new URLSearchParams(window.location.search).get('ws') ?? undefined);
  }, [mock]);

  const { state, listening, start, hangup, setMuted, sendText, demoShowRoutine } =
    useVoiceSession(createSession);

  const t = STRINGS[state.language];

  useEffect(() => {
    document.documentElement.lang = state.language;
  }, [state.language]);

  if (state.phase === 'idle') {
    return <IdleScreen t={t} onStart={start} starting={false} error={state.error} />;
  }

  if (state.routine) {
    return <RoutineScreen t={t} state={state} onHangup={hangup} />;
  }

  return (
    <ConversationScreen
      t={t}
      state={state}
      listening={listening}
      onHangup={hangup}
      onMute={setMuted}
      onSend={sendText}
      onDemoRoutine={import.meta.env.DEV && mock ? demoShowRoutine : undefined}
    />
  );
}
