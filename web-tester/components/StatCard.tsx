interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  accent?: boolean;
}

export function StatCard({ label, value, hint, accent }: StatCardProps) {
  return (
    <div className={`card p-5 ${accent ? 'border-amber-accent/40' : ''}`}>
      <div className="text-xs uppercase tracking-wider text-oyster-400 font-semibold">
        {label}
      </div>
      <div
        className={`mt-1 text-3xl font-bold ${
          accent ? 'text-amber-accent' : 'text-oyster-50'
        }`}
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-oyster-400">{hint}</div>}
    </div>
  );
}
