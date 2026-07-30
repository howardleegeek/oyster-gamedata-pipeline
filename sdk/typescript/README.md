# @oysterworld/gamedata-sdk (TypeScript)

Thin TypeScript SDK for the **Oyster GameData buyer-spec v1** tarball
format. Designed for Node.js 18+ training-pipeline integration.

The TS SDK is intentionally thinner than the Python SDK — it exposes:

* **Typed schemas** for `systeminfo.json`, `action_camera.json`, and the
  catalog-level `TarballMetadata`.
* **Runtime parsers/validators** (`parseSysteminfo`, `parseActionCameraFrame`)
  that accept both PRD `{x,y,z}` and array `[x,y,z]` vector forms.
* **`BuyerClient`** — HTTP client (`.list()`, `.getMetadata()`,
  `.download()`) over a presigned-URL backend.
* **`validateLocalTarball(path)`** — structural + schema validation that
  runs offline on a buyer's CI machine.

The full 24-criterion content-level lint lives in the Python SDK / CLI
(`oyster-gamedata validate`), because that requires OpenEXR + numpy.

## Install

```bash
npm install @oysterworld/gamedata-sdk
# or
bun add @oysterworld/gamedata-sdk
```

## Quick start

```ts
import { BuyerClient } from '@oysterworld/gamedata-sdk';

const client = new BuyerClient({
  baseUrl: 'https://api.oysterworld.dev/buyer/v1',
  apiKey: process.env.OYSTER_API_KEY,
});

// 1. List available clips
const { items } = await client.list({ batch_id: 'vendor-001_batch-2026-05-A' });

// 2. Download
for (const meta of items) {
  await client.download(meta.clip_id, { outputDir: './downloads' });

  // 3. Validate locally (offline)
  const report = await client.validate(`./downloads/${meta.filename}`);
  if (!report.passed) {
    console.error(meta.clip_id, report.summary);
    for (const r of report.results.filter((r) => !r.passed)) {
      console.error('  FAIL:', r.name, r.message);
    }
  }
}
```

## Local-only usage (no API)

```ts
import { validateLocalTarball, parseSysteminfo } from '@oysterworld/gamedata-sdk';

const report = await validateLocalTarball('./clip.tar.gz');
console.log(report.summary);
console.log(report.systeminfo);   // typed Systeminfo
```

## Build

```bash
bun install
bun test          # unit + integration tests
bun run build     # emits dist/
bun run typecheck
```
