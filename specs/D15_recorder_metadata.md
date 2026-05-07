# D15 — Recorder metadata stamper
Implement `bin/stamp_real_metadata.py video.mp4`. Adds an FFmpeg metadata tag `comment=oyster-real-screen-capture` + `composer=oyster-recorder-v0.24.0` so D5 validator can distinguish real screen captures from testsrc. Pure stdlib + subprocess. Tests: stamp + read back.
