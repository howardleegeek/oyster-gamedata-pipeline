import Link from 'next/link';
import { notFound } from 'next/navigation';
import { fetchCatalogById } from '../../../lib/catalog';
import { readCartCookie } from '../../../lib/cart-cookie';
import { sampleActionCameraRecords } from '../../../lib/sample-data';
import {
  formatBytes,
  formatCents,
  formatHours,
  formatRelativeTime,
} from '../../../lib/format';
import { AddToCartButton } from '../../../components/AddToCartButton';

export const dynamic = 'force-dynamic';

interface PageProps {
  params: { id: string };
}

export default async function TarballDetailPage({ params }: PageProps) {
  const { row, liveMode } = await fetchCatalogById(params.id);
  if (!row) notFound();

  const cartIds = new Set(readCartCookie());
  const inCart = cartIds.has(row.id);

  // First 100 records of action_camera.json — synthesized in DEV, derived
  // from the sample fixture in LIVE (the real preview endpoint reads the
  // tarball, but for the page we use the deterministic sample so the UI
  // doesn't have to wait on a 200 MB tarball download just to render).
  const actionRecords = sampleActionCameraRecords(row.id, 8);

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      <Link href="/browse" className="btn-ghost mb-4">
        ← Back to catalog
      </Link>

      <div className="grid md:grid-cols-[1.5fr_1fr] gap-8">
        {/* LEFT — preview */}
        <div>
          {/* Video preview */}
          <div className="card p-0 overflow-hidden mb-4">
            <div className="aspect-video bg-oyster-950 relative">
              {row.video_preview_url ? (
                <video
                  src={row.video_preview_url}
                  poster={row.poster_url}
                  controls
                  className="w-full h-full object-cover"
                />
              ) : row.poster_url ? (
                // No video URL yet — show poster only.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={row.poster_url}
                  alt={row.title}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full grid place-items-center text-oyster-700">
                  no preview available
                </div>
              )}
            </div>
            <div className="px-4 py-2 text-xs text-oyster-400 flex items-center justify-between">
              <span>30-second preview</span>
              <a
                href={`/api/tarball/${row.id}/preview`}
                target="_blank"
                rel="noreferrer"
                className="text-amber-accent hover:underline"
              >
                Download sample JSON →
              </a>
            </div>
          </div>

          {/* JSON sample */}
          <h3 className="text-lg font-semibold mb-2">action_camera.json (first 8 records)</h3>
          <pre className="code-block max-h-96">
{JSON.stringify(actionRecords, null, 2)}
          </pre>
          <p className="mt-2 text-xs text-oyster-400">
            Full sample (100 records) available via{' '}
            <a
              href={`/api/tarball/${row.id}/preview`}
              className="text-amber-accent hover:underline"
            >
              /api/tarball/{row.id.slice(0, 8)}…/preview
            </a>
            . Purchasing unlocks the complete tarball — usually ~30 records / second.
          </p>
        </div>

        {/* RIGHT — purchase column */}
        <aside>
          <div className="card p-5 sticky top-20 space-y-4">
            <div className="flex items-center justify-between gap-2">
              <span className={`tag tag-task-${row.mc_task_type}`}>{row.mc_task_type}</span>
              <span className="tag bg-oyster-950/40 text-oyster-200">
                D5 {(row.d5_score ?? 0).toFixed(2)}
              </span>
            </div>

            <div>
              <h1 className="text-xl font-bold leading-tight">{row.title}</h1>
              <p className="mt-2 text-sm text-oyster-300">{row.description}</p>
            </div>

            <hr className="border-oyster-800/50" />

            <dl className="text-sm space-y-1 text-oyster-300">
              <div className="flex justify-between">
                <dt>Size</dt>
                <dd className="text-oyster-100 font-semibold">
                  {formatBytes(row.size_bytes)}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt>Length</dt>
                <dd className="text-oyster-100 font-semibold">
                  {formatHours(row.duration_seconds / 3600)}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt>Verdict</dt>
                <dd>
                  <span className={`tag tag-${row.d5_verdict}`}>{row.d5_verdict}</span>
                </dd>
              </div>
              <div className="flex justify-between">
                <dt>Uploaded</dt>
                <dd className="text-oyster-100">
                  {formatRelativeTime(row.uploaded_at)}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt>SHA-256</dt>
                <dd className="font-mono text-xs text-oyster-400">
                  {row.sha256.slice(0, 12)}…
                </dd>
              </div>
            </dl>

            <hr className="border-oyster-800/50" />

            <div>
              <div className="text-xs uppercase tracking-wider text-oyster-400">Price</div>
              <div className="text-3xl font-bold text-amber-accent">
                {formatCents(row.price_cents)}
              </div>
              <div className="mt-0.5 text-xs text-oyster-400">
                Includes a perpetual training license. Pick research or commercial at checkout.
              </div>
            </div>

            <AddToCartButton tarballId={row.id} initiallyInCart={inCart} />

            {!liveMode && (
              <p className="text-xs text-amber-accent">
                [DEV MODE] add-to-cart writes to a cookie; checkout is faked.
              </p>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
