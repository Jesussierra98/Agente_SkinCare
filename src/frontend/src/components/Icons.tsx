import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement>;

const base = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

export function MicIcon(props: P) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" {...base} {...props}>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" />
    </svg>
  );
}

export function MicOffIcon(props: P) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" {...base} {...props}>
      <path d="M9 9.5V6a3 3 0 0 1 5.6-1.5M15 10v1a3 3 0 0 1-4.6 2.5" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 10.2 5.3M18.5 11.5c0 .8-.1 1.5-.4 2.2M12 18v3M4 4l16 16" />
    </svg>
  );
}

export function PhoneHangupIcon(props: P) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" {...base} {...props}>
      <path d="M3 14.5c0-1.2 2.7-3.5 9-3.5s9 2.3 9 3.5v1.2c0 .5-.4.8-.8.8h-3.4c-.5 0-.8-.3-.8-.8v-1.4c-1.7-.5-3.4-.5-5 0v1.4c0 .5-.3.8-.8.8H3.8c-.4 0-.8-.3-.8-.8z" />
    </svg>
  );
}

export function CameraIcon(props: P) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" {...base} {...props}>
      <path d="M4 8h3l1.5-2h7L17 8h3v11H4z" />
      <circle cx="12" cy="13.5" r="3.5" />
    </svg>
  );
}

export function UploadIcon(props: P) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" {...base} {...props}>
      <path d="M12 16V4M7 9l5-5 5 5M4 20h16" />
    </svg>
  );
}

/* Iconos lineales de las 4 tarjetas "Cómo funciona" */

export function StepMicIcon(props: P) {
  return (
    <svg viewBox="0 0 50 30" width="50" height="30" {...base} strokeWidth={1.8} {...props}>
      <rect x="1" y="1" width="48" height="28" />
      <rect x="21.5" y="6" width="7" height="12" rx="3.5" />
      <path d="M17.5 14.5a7.5 7.5 0 0 0 15 0M25 22v3" />
    </svg>
  );
}

export function StepWaveIcon(props: P) {
  return (
    <svg viewBox="0 0 46 40" width="46" height="40" {...base} strokeWidth={1.8} {...props}>
      <path d="M3 15v10M9 10v20M15 3v34M21 12v16M27 6v28M33 11v18M39 15v10M45 17v6" />
    </svg>
  );
}

export function StepRoutineIcon(props: P) {
  return (
    <svg viewBox="0 0 50 42" width="50" height="42" {...base} strokeWidth={1.8} {...props}>
      <rect x="1" y="1" width="14" height="40" />
      <rect x="22" y="1" width="26" height="22" />
      <rect x="28" y="6" width="8" height="8" />
      <path d="M22 29h26M22 35h18" />
    </svg>
  );
}

export function StepPhoneIcon(props: P) {
  return (
    <svg viewBox="0 0 28 48" width="28" height="48" {...base} strokeWidth={1.8} {...props}>
      <rect x="1" y="1" width="26" height="46" rx="3" />
      <rect x="8" y="14" width="12" height="12" />
      <path d="M11 40h6" />
    </svg>
  );
}
