# D10 — Bundle session hash signer
Implement `bin/bundle_session_sign.py bundle.tar.gz`. Computes SHA256 of bundle minus signature, writes `bundle_signature.txt` with timestamp + machine fingerprint. Pure stdlib. Tests: sign + verify, tamper detection.
