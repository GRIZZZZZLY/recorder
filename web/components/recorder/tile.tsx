export function Tile({ label, value, hint }:
  { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-2xl border border-separator-border bg-background-primary-default p-6">
      <p className="text-caption-1-medium uppercase tracking-[0.12em] text-text-tertiary">{label}</p>
      <p className="nums mt-2 text-title-1-medium text-text-primary">{value}</p>
      {hint ? <p className="mt-1 text-body-2-regular text-text-tertiary">{hint}</p> : null}
    </div>
  );
}
