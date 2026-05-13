/**
 * Oyster GameData SDK — TypeScript public API.
 *
 * Buyer-facing entry point. Exposes the typed schema, the HTTP client,
 * and the local validator.
 *
 * @example
 *   import { BuyerClient } from '@oysterworld/gamedata-sdk';
 *
 *   const client = new BuyerClient({ baseUrl: 'https://api.oysterworld.dev/buyer/v1', apiKey: process.env.OYSTER_API_KEY });
 *   const { items } = await client.list({ batch_id: 'vendor-001_batch-2026-05-A' });
 *   for (const m of items) {
 *     await client.download(m.clip_id, { outputDir: './downloads' });
 *     const report = await client.validate(`./downloads/${m.filename}`);
 *     if (!report.passed) console.error('FAIL', report.summary);
 *   }
 */

export {
  parseActionCamera,
  parseActionCameraFrame,
  parseSysteminfo,
  parseVector3,
  parseVector4,
  isPinhole,
  SchemaValidationError,
} from './schema.js';
export type {
  ActionCameraFrame,
  CameraIntrinsics,
  Gameinfo,
  MapBounds,
  Systeminfo,
  TarballMetadata,
  Vec3Like,
  Vec4Like,
  Vector3,
  Vector4,
} from './schema.js';

export { BuyerClient, BuyerClientError } from './client.js';
export type {
  BuyerClientOptions,
  ListOptions,
  ListResult,
  DownloadOptions,
  DownloadResult,
} from './client.js';

export { validateLocalTarball } from './validator.js';
export type {
  LocalValidateOptions,
  LocalValidateReport,
  LocalValidateCriterion,
} from './validator.js';

export const VERSION = '0.1.0';
