# Vendor FAQ (English Version)

## Table of Contents
- [Setup & Environment (Q1-Q8)](#setup--environment-q1-q8)
- [Recording & Capture (Q9-Q15)](#recording--capture-q9-q15)
- [Data Format & Acceptance (Q16-Q22)](#data-format--acceptance-q16-q22)
- [Upload & Submission (Q23-Q26)](#upload--submission-q23-q26)
- [Billing & Partnership (Q27-Q30)](#billing--partnership-q27-q30)

---

### Q1: Must I use Minecraft?
No, you can use any game engine or stack that meets the PRD specifications. The requirements focus on the output format and quality, not the specific software used to generate it.

### Q2: Must I use OBS?
No, OBS is not mandatory. You can use ShadowPlay, ReLive, SwitchBoard, or any other recording software that produces the required 1080p, 30 fps video with synchronized depth frames.

### Q3: Can I run DepthAnything without a GPU?
Yes, but CPU processing is 5-10x slower. For acceptable performance, a GPU is recommended. Unity G-buffer exports are also accepted as an alternative to DepthAnything.

### Q4: SOP.sh fails — what to do?
Run `bin/doctor.sh` to diagnose common issues. Check the `logs/` directory for detailed error messages. Most failures are due to missing dependencies or incorrect environment setup.

### Q5: Which Java version?
Java 21 is required. Install via `brew install openjdk@21` on macOS or use SDKMAN! on Linux. Verify with `java --version`.

### Q6: Need a Mojang account?
No, offline mode is sufficient. You do not need a licensed Minecraft account for data collection purposes.

### Q7: Can I use WSL2 Linux?
Yes, WSL2 with Ubuntu 22.04 is recommended on Windows systems. It provides better performance and compatibility with our toolchain compared to native Windows.

### Q8: Apple Silicon performance?
M2 or newer Apple Silicon chips are recommended. While M1 can work, M2+ provides significantly better performance for real-time depth estimation and video processing.

### Q9: Do operators need to code?
No, operators only need to follow the SOP.sh script. The entire pipeline is automated with a single command interface.

### Q10: Recording crashed — what now?
Discard the corrupted clip and re-record. We do not accept spliced or edited clips. Each 5-6 minute clip must be continuous and unmodified.

### Q11: How to ensure stable 30 fps?
Disable V-Sync, close background applications, and use hardware with RTX 3060 or better. Monitor frame times during recording to ensure consistent performance.

### Q12: What should operators do in-game?
Operators should engage in free exploration with balanced movement: approximately 40% forward (W), 20% left (A), 20% right (D), and 20% backward (S). Avoid repetitive patterns.

### Q13: Combat / death / view-switch — violations?
Yes, all combat sequences, player deaths, and view switching are rejected. Clips must contain only exploration and navigation gameplay.

### Q14: Must clips be exactly 5 min?
Clips must be 5-6 minutes in duration. Anything outside this range (shorter or longer) will be rejected.

### Q15: Must screen be 1080p?
Yes, strict 1920×1080 resolution is required. No other resolutions or aspect ratios are accepted.

### Q16: Does action_camera.json field order matter?
No, field order does not matter, but all 20 required fields must be present with valid data types.

### Q17: Quaternion order?
Quaternions must be in [x, y, z, w] order. This is consistent with most game engines and mathematics libraries.

### Q18: Coordinate system: left or right hand?
Left-hand coordinate system: +X points right, +Y points up, +Z points forward (into the screen).

### Q19: Depth EXR must be 6 fps?
Yes, depth EXR sequences must match the video frame rate of 6 fps, resulting in exactly 1800 frames for a 5-minute clip.

### Q20: gameinfo.xlsx fields incomplete?
All 14 required fields must be complete. Missing any single field will result in rejection of the entire batch.

### Q21: Common lint failures?
Common failures include: stationary frames >10%, WASD key imbalance, fx≠fy focal lengths, and frame gaps in the timestamp sequence.

### Q22: Can I write my own lint?
Yes, you can write custom validation scripts, but submissions must still pass the official `oyster-buyer-lint` tool. Your custom lint can provide additional checks.

### Q23: S3 upload too slow?
Use `bin/upload_s3.sh` which implements multipart upload with resume capability. It handles connections as slow as 200 Kbps through intelligent chunking.

### Q24: How to use SFTP?
We provide vendor-specific SFTP accounts with chroot isolation. See SUBMISSION_FORMAT.md §3.2 for connection details and directory structure.

### Q25: manifest.yaml required every batch?
Yes, every batch submission requires a valid manifest.yaml file. Use `bin/generate_manifest.py` to automate creation with proper checksums.

### Q26: Partial upload failure?
`aws s3 sync` automatically retries failed transfers. Failed clip IDs are reported to stderr for manual re-upload.

### Q27: How is unit price determined?
Email your estimated monthly capacity and technical stack capability to howard.linra@gmail.com. A Statement of Work with unit pricing will be provided within 48 hours.

### Q28: Payment terms?
30% advance payment upon contract signing, 70% upon acceptance of deliverables within 7 business days. All payments via wire transfer.

### Q29: Can I re-record rejected clips?
Yes, you can re-record rejected clips with new clip_id values. There is no penalty for re-recording, but ensure the new clips pass all validation checks.

### Q30: Minimum batch size?
100-500 clips per batch recommended. Monthly capacity of 1000+ clips with no upper limit. Smaller batches may have proportionally higher overhead costs.

---

**Question not answered?** Contact us via email (howard.linra@gmail.com) or WhatsApp for immediate support.

*Last updated: $(date +%Y-%m-%d)*