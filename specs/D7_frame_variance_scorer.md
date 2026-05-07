# D7 — Video frame variance scorer

Implement `bin/video_variance_score.py video.mp4`. Returns JSON:
  {"variance_score": 0.0-1.0, "verdict": "REAL"|"SYNTHETIC", "evidence": "..."}

Sample 30 frames evenly, compute pairwise SSIM. Real footage has SSIM <
0.85 between random pairs (motion). Synthetic testsrc has SSIM > 0.95.

Pure Python via scikit-image SSIM. Tests: synthetic constant-frame video
→ SYNTHETIC, real video → REAL.
