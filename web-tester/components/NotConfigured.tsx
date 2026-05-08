/**
 * Hard-gate component shown when a required service (Supabase or Stripe)
 * is not configured. Replaces the previous "DEV MODE with fake sample
 * data" approach — Howard 2026-05-07 IRON-LAW: never fabricate data,
 * even behind a banner. Show the operator what's missing and how to
 * fix it, then render NOTHING fake.
 */

interface NotConfiguredProps {
  service: "Supabase" | "Stripe Connect" | "Storage";
  envVars: string[];
  docsUrl?: string;
}

export function NotConfigured({ service, envVars, docsUrl }: NotConfiguredProps) {
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-6 dark:border-amber-700 dark:bg-amber-950">
      <h2 className="text-lg font-semibold text-amber-900 dark:text-amber-100">
        {service} not configured
      </h2>
      <p className="mt-2 text-sm text-amber-800 dark:text-amber-200">
        This page renders real {service.toLowerCase()} data. Set the
        following environment variables in your <code>.env.local</code>{" "}
        (and on Vercel for deployed mode) and reload:
      </p>
      <ul className="mt-3 list-inside list-disc text-sm font-mono text-amber-900 dark:text-amber-100">
        {envVars.map((v) => (
          <li key={v}>{v}</li>
        ))}
      </ul>
      {docsUrl && (
        <p className="mt-3 text-sm">
          <a
            href={docsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-amber-900 underline hover:text-amber-700 dark:text-amber-100"
          >
            Setup guide →
          </a>
        </p>
      )}
      <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
        Iron-law: this page does NOT show fabricated data. Configure the
        service above to see real values.
      </p>
    </div>
  );
}
