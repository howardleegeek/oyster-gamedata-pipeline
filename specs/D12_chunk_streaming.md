# D12 — Tarball chunk streaming uploader stub
Implement `bin/upload_tarball_chunked.py tarball.tar.gz --endpoint URL`. Splits a 100MB+ tarball into 5MB chunks, uploads each via HTTP POST with retry. Pure stdlib + urllib. Tests: chunk a 12MB file → 3 chunks, retry on 500.
