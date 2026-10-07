import { useCallback, useEffect, useMemo } from 'react';
import { createKioskAuth } from '../auth/kioskAuth';
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
  const auth = useMemo(() => createKioskAuth(), []);
  const createSession = useCallback((): VoiceSession => {
    if (mock) {
      const { scenario, language } = scenarioFromUrl(window.location.search);
      return new MockVoiceSession(scenario, language);
    }
    // Con AgentCore y Cognito configurados, cada conexión lleva un access token recién renovado (DD-03).
    if (auth) {
      return new RealVoiceSession(import.meta.env.VITE_AGENT_WS_URL as string, () => auth.getAccessToken());
    }
    return new RealVoiceSession(new URLSearchParams(window.location.search).get('ws') ?? undefined);
  }, [mock, auth]);

  const { state, listening, start, hangup, setMuted, sendText, demoShowRoutine } =
    useVoiceSession(createSession);

  const t = STRINGS[state.language];

  useEffect(() => {
    document.documentElement.lang = state.language;
  }, [state.language]);

  if (state.phase === 'idle' || state.phase === 'ended') {
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
