/**
 * Database row shapes — match the SQL schema in
 * `supabase/migrations/20260507000000_buyer_init.sql`.
 *
 * The buyer portal extends the tester portal's schema with four new
 * tables (buyers, purchases, licenses, cart_items) and reads the existing
 * `tarballs` table (writes go through the tester pipeline).
 */

// --------------------------------------------------------------------------
// New BUYER tables
// --------------------------------------------------------------------------

export interface BuyerRow {
  id: string;
  email: string;
  github_id: string | null;
  github_handle: string | null;
  company_name: string | null;
  created_at: string;
  total_spent_cents: number;
}

export interface PurchaseRow {
  id: string;
  buyer_id: string;
  tarball_id: string;
  amount_cents: number;
  stripe_session_id: string | null;
  purchased_at: string;
  license_id: string | null;
}

export interface LicenseRow {
  id: string;
  purchase_id: string;
  type: 'research' | 'commercial';
  terms_url: string;
  issued_at: string;
}

export interface CartItemRow {
  id: string;
  buyer_id: string;
  tarball_id: string;
  added_at: string;
}

// --------------------------------------------------------------------------
// Existing tester table — read-only here. Mirror of web-tester/types.ts so
// the two portals stay in sync.
// --------------------------------------------------------------------------

export interface TarballRow {
  id: string;
  tester_id: string;
  uploaded_at: string;
  size_bytes: number;
  sha256: string;
  duration_seconds: number;
  d5_verdict: 'pending' | 'accepted' | 'rejected';
  d5_score: number | null;
  paid: boolean;
}

// --------------------------------------------------------------------------
// Catalog projection — the row shape the buyer-side UI actually consumes.
// Adds curatorial metadata (mc_task_type, price_cents, preview info) on
// top of the raw tarball row.
// --------------------------------------------------------------------------

export type McTaskType =
  | 'survival'
  | 'creative'
  | 'redstone'
  | 'pvp'
  | 'mining'
  | 'building'
  | 'exploration'
  | 'farming';

export interface CatalogTarball {
  id: string;
  tester_id: string;
  uploaded_at: string;
  size_bytes: number;
  duration_seconds: number;
  d5_verdict: 'pending' | 'accepted' | 'rejected';
  d5_score: number | null;
  sha256: string;
  // Curated metadata
  mc_task_type: McTaskType;
  title: string;
  description: string;
  price_cents: number;
  // Preview
  poster_url: string;
  video_preview_url: string;
  // Server-side: storage path inside the tarballs bucket
  storage_path?: string;
}

// --------------------------------------------------------------------------
// Filters accepted by GET /api/catalog
// --------------------------------------------------------------------------

export interface CatalogFilters {
  verdict?: 'accepted' | 'pending' | 'rejected';
  task_type?: McTaskType;
  min_size_bytes?: number;
  max_size_bytes?: number;
  min_price_cents?: number;
  max_price_cents?: number;
  limit?: number;
}

// --------------------------------------------------------------------------
// Sample of action_camera.json — first N records returned by
// GET /api/tarball/[id]/preview. Shape mirrors what the recorder writes
// into each tarball.
// --------------------------------------------------------------------------

export interface ActionCameraRecord {
  t_ms: number;
  action: string;
  yaw: number;
  pitch: number;
  pos: [number, number, number];
  buttons: string[];
}
