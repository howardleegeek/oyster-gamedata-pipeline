/**
 * Display helpers — money, time, file sizes. Mirrored from web-tester so
 * the two portals render numbers identically.
 */

export function formatCents(cents: number): string {
  const dollars = cents / 100;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(dollars);
}

export function formatHours(hours: number): string {
  if (hours < 1) {
    const minutes = Math.round(hours * 60);
    return `${minutes} min`;
  }
  return `${hours.toFixed(1)} h`;
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`;
}

export function formatGB(bytes: number): string {
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diffSec = Math.round((now - then) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * List-of-tarballs aggregator used by /cart and /checkout.
 * Pricing rule: $price_per_gb_cents per GB, rounded UP to the nearest cent.
 * Research license discount applied as a flat percent off subtotal.
 */
export function totalCents(
  sizesBytes: number[],
  pricePerGbCents: number,
  discountPct = 0,
): number {
  const totalGb = sizesBytes.reduce((acc, b) => acc + b, 0) / 1024 / 1024 / 1024;
  const gross = Math.ceil(totalGb * pricePerGbCents);
  const discount = Math.floor((gross * discountPct) / 100);
  return Math.max(gross - discount, 0);
}
