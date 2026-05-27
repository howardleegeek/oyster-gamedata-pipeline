# Release Channels And Fallbacks

The project currently has two proven release histories and one source build
candidate path. They are related, but they are not interchangeable.

## Current Situation

| Channel | Current anchor | User surface | Status |
|---|---|---|---|
| Consumer installer | `v0.16.0` | `OysterRecorder-Setup-v0.16.0.exe` + `SHA256SUMS.txt` | Latest public/internal distribution line. Appcast points here. |
| Bundled recorder | `recorder-v0.28.0-rc19.0.3` | 1 GB bundled installer, `OysterRecorder.exe`, onedir zip, MC mod jars, `SHA-256-manifest.txt` | Strong historical recorder/reference line. Not an appcast target. |
| Source candidate | `vendor/recorder` at `e171f20` | No direct user asset | Build input for the next release after Windows installer smoke. |

The authoritative code contract is
`src/oyster_agent_runner/release_channels.py`.

## MECE Rule

Every release asset must belong to exactly one operational bucket:

1. `consumer_installer`: public updater/friend-download installer. This is the
   only channel allowed in `/api/v1/updates/appcast.xml`.
2. `bundled_recorder`: offline/reference bundle assets from the rc19 line.
   These can be used for QA, Minecraft asset recovery, and recorder behavior
   comparison, but they must not be silently substituted into the public
   appcast.
3. `source_candidate`: pinned source used to build the next installer. Source
   freshness does not imply a user-downloadable binary exists.
4. `unknown`: any new asset name that does not match a known contract. Unknown
   assets require an explicit contract update before they are promoted.

## Fallback Order

1. Use the latest green consumer release.
   Gate: release asset HEAD 200, checksum match, backend appcast match,
   Release Distribution Smoke, Backend Remote Smoke, and Windows Installer
   Smoke are all green.
2. Roll back to the previous known-good consumer release when the latest tag
   fails asset, checksum, appcast, or Windows installer smoke.
   Gate: the rollback tag must pass the same consumer gates.
3. Use the rc19 bundled recorder only as a QA/reference fallback when the small
   consumer installer cannot prove capture fidelity or when offline Minecraft
   runtime/mod assets are needed.
   Gate: large bundle size sanity, `SHA-256-manifest.txt`, MC jar matrix, and a
   clean Windows gameplay smoke pass.
4. Rebuild from the pinned recorder source only when both published asset
   paths are stale for the needed fix.
   Gate: new GitHub release carries a real installer and checksum, then backend
   appcast sync verifies that exact release.

## Guardrails

- Do not attach rc19 bundled assets to a `v0.x` release as if they were the
  consumer installer.
- Do not point appcast at a prerelease or bundled asset unless it first becomes
  a normal consumer installer with the expected naming/checksum contract.
- Do not treat `vendor/recorder` source alignment as proof that the public
  installer has changed.
- Do not promote a new game adapter beyond `smoke_ready` unless its output
  satisfies `session_contract.py` and a real Windows session validates.

## Integration Direction

Keep the current `v0.16.0` consumer path as the production baseline. Use
`recorder-v0.28.0-rc19.0.3` as the recovery/reference bundle while rebuilding
the next installer from the release-buildable `vendor/recorder` source pin.
When that build is green, publish a new `v0.x` release with a normal
`OysterRecorder-setup-*.exe`, `SHA256SUMS.txt`, and appcast sync. That gives us
one public surface and one explicit fallback path instead of two competing
"latest" meanings.
