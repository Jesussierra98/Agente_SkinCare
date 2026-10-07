import { KioskHeader } from '../components/KioskHeader';
import {
  MicIcon,
  StepMicIcon,
  StepPhoneIcon,
  StepRoutineIcon,
  StepWaveIcon,
} from '../components/Icons';
import type { VoiceErrorCode } from '../voice/VoiceSession';
import type { KioskStrings } from './i18n';

const STEP_ICONS = [StepMicIcon, StepWaveIcon, StepRoutineIcon, StepPhoneIcon];

interface Props {
  t: KioskStrings;
  onStart: () => void;
  starting: boolean;
  error: VoiceErrorCode | null;
}

const ERROR_TEXT: Record<NonNullable<Props['error']>, string> = {
  mic_denied: 'Se requiere acceso al micrófono para conversar. Permítelo e inténtalo de nuevo.',
  connect_failed: 'No se pudo iniciar la conversación. Inténtalo de nuevo.',
  timeout: 'La conversación tardó demasiado en iniciar. Inténtalo de nuevo.',
  not_provisioned: 'Este dispositivo todavía no está configurado. Pide apoyo al responsable de la tienda.',
  auth_failed: 'No se pudo validar este dispositivo. Pide apoyo al responsable de la tienda.',
};

export function IdleScreen({ t, onStart, starting, error }: Props) {
  return (
    <div className="flex min-h-screen flex-col">
      <KioskHeader brand={t.brand} brandSub={t.brandSub} />

      <main className="grid flex-1 grid-cols-[500fr_526fr] gap-14 px-14 pb-12 pt-[52px]">
        <section className="flex flex-col">
          <p className="eyebrow">{t.voiceAdvice}</p>
          <h1 className="mt-6 font-display text-[68px] font-normal leading-[70px] text-ink">
            {t.idleTitle}
          </h1>
          <p className="mt-4 max-w-[480px] text-[20px] leading-8 text-ink/85">{t.idleIntro}</p>

          <div className="mt-auto">
            {error ? (
              <p role="alert" className="mb-4 text-[15px] text-danger">
                {ERROR_TEXT[error]}
              </p>
            ) : null}
            <button
              type="button"
              onClick={onStart}
              disabled={starting}
              className="flex h-[84px] w-full items-center justify-center gap-4 bg-brand text-[22px] font-semibold text-white transition-colors hover:bg-[#194035] disabled:opacity-70"
            >
              <MicIcon width={24} height={24} />
              {t.startCta}
            </button>
            <p className="mt-4 max-w-[500px] text-[14px] leading-[22px] text-muted">{t.anonNotice}</p>
          </div>
        </section>

        <section aria-labelledby="como-funciona" className="flex flex-col">
          <h2 id="como-funciona" className="eyebrow">
            {t.howItWorks}
          </h2>
          <ol className="mt-5 grid flex-1 grid-cols-2 grid-rows-2 gap-4">
            {t.steps.map((step, i) => {
              const Icon = STEP_ICONS[i]!;
              return (
                <li key={step.title} className="flex flex-col justify-between bg-white p-6">
                  <div className="flex items-start justify-between text-brand">
                    <span
                      className="font-display text-[56px] leading-[48px] text-ink"
                      aria-hidden="true"
                    >
                      {i + 1}
                    </span>
                    <Icon />
                  </div>
                  <div>
                    <h3 className="text-[20px] font-medium leading-[26px] text-ink">{step.title}</h3>
                    <p className="mt-1.5 text-[16px] leading-[21px] text-muted">{step.body}</p>
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      </main>
    </div>
  );
}
