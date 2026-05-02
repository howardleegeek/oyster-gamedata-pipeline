# SOP — Standard Operating Procedure

## Quick Start

Run this script **once** after a fresh git clone to go from nothing to a validated buyer-spec tarball.

```bash
./SOP.sh
```

## What It Does

1. Checks prerequisites (Java 21, Node 18+, ffmpeg, Python 3)
2. Creates `.venv` and installs the repo with test dependencies
3. Installs mineflayer npm packages
4. Downloads Paper 1.20.4 server jar
5. Boots Paper server on port 25565
6. Waits for server to be ready
7. Stages 1801 placeholder EXR files
8. Runs end-to-end pipeline (oyster-agent, adapt-buyer-spec, lint)

## Output

- `buyer.tar.gz` — validated buyer spec tarball
- Server runs in background on `:25565`

## Requirements

- macOS
- Homebrew (for installing missing prerequisites)

See `SOP.sh` for full implementation details.
