/** Onda de audio en barras verticales. Se anima solo cuando `active` es verdadero. */
const HEIGHTS = [
  20, 34, 62, 40, 100, 130, 76, 60, 112, 150, 98, 60, 124, 96, 68, 94, 48, 58, 32, 40, 24,
];

export function Waveform({ active }: { active: boolean }) {
  return (
    <div className="flex h-full items-center justify-center gap-[10px]" aria-hidden="true">
      {HEIGHTS.map((h, i) => (
        <span
          key={i}
          className="wave-bar block w-[6px] origin-center bg-brand"
          style={{
            height: h,
            transform: active ? undefined : 'scaleY(0.35)',
            animation: active ? `wave ${0.7 + (i % 5) * 0.12}s ease-in-out ${i * 0.05}s infinite` : undefined,
            transition: 'transform 200ms ease',
          }}
        />
      ))}
    </div>
  );
}
