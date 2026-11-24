// One small icon set, one stroke weight. Decorative: always paired with visible text
// or an aria-label on the control that holds it.

const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
} as const;

export function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg {...base} className={className}>
      <path d="M6 3.5 10.5 8 6 12.5" />
    </svg>
  );
}

export function ArrowLeftIcon({ className }: { className?: string }) {
  return (
    <svg {...base} className={className}>
      <path d="M13 8H3M7 4 3 8l4 4" />
    </svg>
  );
}

export function ExternalIcon({ className }: { className?: string }) {
  return (
    <svg {...base} className={className}>
      <path d="M6.5 3.5h6v6M12.5 3.5 4 12" />
    </svg>
  );
}

export function CloseIcon({ className }: { className?: string }) {
  return (
    <svg {...base} className={className}>
      <path d="m4 4 8 8M12 4l-8 8" />
    </svg>
  );
}
