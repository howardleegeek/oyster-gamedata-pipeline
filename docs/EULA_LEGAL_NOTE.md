# EULA Legal Note (R030)

## Background

Mojang's Minecraft EULA (End User License Agreement) clause 2 restricts
commercial use of server software.  Vendors who are **paid** to host or
distribute Paper Minecraft server jars may be engaged in commercial use,
which requires separate legal review.

## What changed

`bin/integration_test_minipc.sh` no longer auto-accepts the EULA.
Instead it requires the environment variable `EULA_ACCEPTED=1` to be set
explicitly by the operator/vendor.

## Audit trail

When `EULA_ACCEPTED=1` is set, the script writes:

- `eula.txt` — the standard Mojang EULA acceptance flag
- `eula_audit.log` — a timestamped record including the vendor username

## Vendor responsibility

Setting `EULA_ACCEPTED=1` is a **confirmation** that the vendor has:

1. Read the full EULA at <https://aka.ms/MinecraftEULA>
2. Evaluated whether their use case (especially commercial hosting)
   complies with Mojang's terms
3. Consulted legal counsel if uncertain

This script does **not** provide legal advice.  Vendors should perform
their own compliance assessment before deploying Paper Minecraft servers
in production or commercial environments.
