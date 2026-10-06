import { KioskHeader, StatusDot } from '../components/KioskHeader';
import { MicIcon, MicOffIcon, PhoneHangupIcon } from '../components/Icons';
import { Transcript } from '../components/Transcript';
import { Waveform } from '../components/Waveform';
import type { KioskStrings } from './i18n';
import type { KioskState } from './kioskState';

interface Props {
  t: KioskStrings;
  state: KioskState;
  listening: boolean;
  onHangup: () => void;
  onMute: (muted: boolean) => void;
  onSend: (text: string) => void;
  onDemoRoutine?: () => void;
}

export function ConversationScreen({
  t,
  state,
  listening,
  onHangup,
  onMute,
  onSend,
  onDemoRoutine,
}: Props) {
  const lost = state.phase === 'lost';
  const status = lost ? t.voiceEnded : `${t.inProgress} · ${t.languageName}`;

  return (
    <div className="flex h-screen flex-col">
      <KioskHeader brand={t.brand} brandSub={t.brandSub} height={76}>
        <StatusDot label={status} active={!lost} />
      </KioskHeader>

      <main className="grid min-h-0 flex-1 grid-cols-[500fr_526fr] gap-14 px-14 pb-10 pt-6">
        <section className="flex flex-col pt-[18px]">
          <p className="eyebrow">{t.listening}</p>
          <div className="mt-[25px] h-[200px] bg-white">
            <Waveform active={listening && !lost} />
          </div>
          <p className="mt-9 font-display text-[44px] font-normal leading-[46px] text-ink">
            {t.speakNaturally}
          </p>

          <div className="mt-auto">
            <div className="flex gap-3">
              <button
                type="button"
                aria-pressed={state.muted}
                disabled={lost}
                onClick={() => onMute(!state.muted)}
                className="flex h-[76px] flex-[202] items-center justify-center gap-3 border border-ink bg-transparent text-[18px] text-ink hover:bg-white disabled:opacity-50"
              >
                {state.muted ? <MicIcon /> : <MicOffIcon />}
                {state.muted ? t.unmute : t.mute}
              </button>
              <button
                type="button"
                onClick={onHangup}
                className="flex h-[76px] flex-[284] items-center justify-center gap-3 bg-danger text-[21px] font-semibold text-white hover:bg-[#862626]"
              >
                <PhoneHangupIcon />
                {t.hangup}
              </button>
            </div>
            <p className="mt-3 text-[14px] text-muted">{t.hangupNote}</p>
          </div>
        </section>

        <section className="flex min-h-0 flex-col">
          <div className="flex h-[14px] items-start justify-end">
            {onDemoRoutine ? (
              <button
                type="button"
                onClick={onDemoRoutine}
                className="text-[12px] leading-none text-muted underline underline-offset-2 hover:text-ink"
              >
                {t.demoLink}
              </button>
            ) : null}
          </div>
          <div className="mt-[3px] flex min-h-0 flex-1 flex-col">
            <Transcript
              t={t}
              lines={state.transcript}
              onSend={onSend}
              endedNotice={lost ? t.voiceEnded : null}
            />
          </div>
        </section>
      </main>
    </div>
  );
}
