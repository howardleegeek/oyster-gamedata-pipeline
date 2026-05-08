/**
 * REAL cluster stats — pulled from the live GitHub release API.
 *
 * Howard 2026-05-07 IRON-LAW fix: previous `sample-data.ts` exported
 * fabricated numbers ("24.7 hours", fake `po_1OABCDE...` payout IDs)
 * gated by a "DEV MODE" rationalization. That violated the no-fake-data
 * canon — even with a banner, fabricated numbers shipping in source IS
 * placeholder.
 *
 * This module replaces it with REAL stats from the cluster's actual
 * production output. The fetch hits `api.github.com` directly with the
 * real release tag — no auth needed for a public release. Falls back
 * to a hard-gate (throws) when the API is unreachable so pages render
 * a clear error, NOT fake data.
 */

const REAL_DATA_RELEASE_TAG = "real-data-sample-v1-20260507-0742";
const REPO = "howardleegeek/oyster-gamedata-pipeline";
const GH_API = `https://api.github.com/repos/${REPO}/releases/tags/${REAL_DATA_RELEASE_TAG}`;

export interface RealClusterStats {
  /** Number of REAL=6 tarballs currently published on the GitHub release. */
  publishedAssetCount: number;
  /** Total bytes of all published REAL=6 assets. */
  publishedTotalBytes: number;
  /** ISO timestamp of the most-recently-uploaded asset. */
  latestAssetUploadedAt: string | null;
  /** Asset metadata (name, size, browser_download_url, created_at). */
  assets: {
    name: string;
    size: number;
    browser_download_url: string;
    created_at: string;
  }[];
}

/**
 * Fetch real cluster stats from the GitHub release API.
 *
 * Throws if the API is unreachable / returns non-200. Caller (page or
 * route handler) decides whether to render an error state — we never
 * fabricate data on failure.
 */
export async function fetchRealClusterStats(): Promise<RealClusterStats> {
  const res = await fetch(GH_API, {
    headers: { Accept: "application/vnd.github+json" },
    // Re-fetch every 5 minutes — release rotation buffer lives that long.
    next: { revalidate: 300 },
  });
  if (!res.ok) {
    throw new Error(
      `GitHub API ${res.status} for ${REAL_DATA_RELEASE_TAG}: ${await res.text().catch(() => "")}`,
    );
  }
  const data = await res.json();
  const assets = (data.assets ?? [])
    .filter((a: { name?: string }) => a.name?.startsWith("oyster_REAL6_"))
    .map((a: {
      name: string;
      size: number;
      browser_download_url: string;
      created_at: string;
    }) => ({
      name: a.name,
      size: a.size,
      browser_download_url: a.browser_download_url,
      created_at: a.created_at,
    }));
  assets.sort((a: { created_at: string }, b: { created_at: string }) =>
    b.created_at.localeCompare(a.created_at),
  );
  const publishedTotalBytes = assets.reduce(
    (acc: number, a: { size: number }) => acc + a.size,
    0,
  );
  return {
    publishedAssetCount: assets.length,
    publishedTotalBytes,
    latestAssetUploadedAt: assets[0]?.created_at ?? null,
    assets,
  };
}
