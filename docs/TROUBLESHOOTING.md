# R050 Troubleshooting Guide

This document covers common errors vendors may encounter. Each entry provides symptom, root cause, fix, and reference.

---

## Install Issues

### TS-01: Java Not Found
**Symptom**: `java: command not found` or `JAVA_HOME not set`
**Root cause**: Java runtime not installed or not in PATH
**Fix**: `sudo apt install openjdk-17-jdk && export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64`
**Reference**: https://openjdk.org/install/

### TS-02: Python Version Mismatch
**Symptom**: `Python 3.10 required, found 3.8.x`
**Root cause**: System Python version too old
**Fix**: Install Python 3.10+ via pyenv: `pyenv install 3.10.12 && pyenv global 3.10.12`
**Reference**: https://github.com/pyenv/pyenv

### TS-03: nvm Install Fails
**Symptom**: `nvm: command not found` after install script
**Root cause**: Shell config not reloaded or nvm not sourced
**Fix**: `source ~/.nvm/nvm.sh` or add to `~/.bashrc`: `[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"`
**Reference**: https://github.com/nvm-sh/nvm#troubleshooting

### TS-04: OpenEXR Install Windows
**Symptom**: `error: Microsoft Visual C++ 14.0 is required` when installing OpenEXR
**Root cause**: Missing Visual Studio Build Tools
**Fix**: Install "Desktop development with C++" workload from Visual Studio Build Tools
**Reference**: https://visualstudio.microsoft.com/visual-cpp-build-tools/

### TS-05: Antivirus Quarantine
**Symptom**: Installed binaries disappear or "Access denied" errors
**Root cause**: Antivirus software quarantining executables
**Fix**: Add project directory to antivirus exclusion list; restore quarantined files
**Reference**: https://support.microsoft.com/en-us/windows/add-an-exclusion-to-windows-security-811816c0-4dfd-af4a-47e4-c301afe13b26

### TS-06: pip Permission Denied
**Symptom**: `ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied`
**Root cause**: Installing to system Python without sudo or virtualenv
**Fix**: Use `python -m venv venv && source venv/bin/activate` then `pip install -r requirements.txt`
**Reference**: https://pip.pypa.io/en/stable/user_guide/#user-installs

### TS-07: Node Version Mismatch
**Symptom**: `error: Node.js version 18.x required, found 16.x`
**Root cause**: Outdated Node.js version
**Fix**: `nvm install 18 && nvm use 18`
**Reference**: https://nodejs.org/en/download/

### TS-08: CUDA Not Detected
**Symptom**: `CUDA not available` or `torch.cuda.is_available() returns False`
**Root cause**: CUDA toolkit or NVIDIA driver not installed
**Fix**: Install NVIDIA driver and CUDA toolkit matching PyTorch version
**Reference**: https://pytorch.org/get-started/locally/

### TS-09: Rust Compiler Missing
**Symptom**: `error: can't find rustc` when building native extensions
**Root cause**: Rust not installed for packages requiring native compilation
**Fix**: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
**Reference**: https://rustup.rs/

### TS-10: Homebrew Package Not Found
**Symptom**: `Error: No available formula with the name "xxx"`
**Root cause**: Package not in default tap or brew outdated
**Fix**: `brew update && brew search <package>` or use alternate tap
**Reference**: https://docs.brew.sh/Taps

---

## Network Issues

### TS-11: HuggingFace Blocked
**Symptom**: `ConnectionError: Couldn't reach huggingface.co` or timeout
**Root cause**: Firewall blocking huggingface.co or DNS issues
**Fix**: Set mirror: `export HF_ENDPOINT=https://hf-mirror.com` or use VPN
**Reference**: https://huggingface.co/docs/huggingface_hub/troubleshooting

### TS-12: pip Timeout
**Symptom**: `pip._vendor.urllib3.exceptions.ReadTimeoutError: HTTPSConnectionPool timed out`
**Root cause**: Slow network or PyPI server overload
**Fix**: `pip install --timeout 120 <package>` or use mirror: `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple`
**Reference**: https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-timeout

### TS-13: S3 Upload Slow
**Symptom**: S3 uploads taking hours for large files
**Root cause**: Single-threaded upload or wrong region
**Root cause**: Use multipart upload: `aws s3 cp --no-progress --expected-size <bytes> <file> s3://bucket/`
**Fix**: Or use `s5cmd` for parallel uploads: `s5cmd cp <file> s3://bucket/`
**Reference**: https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html

### TS-14: RCON Connection Refused
**Symptom**: `ConnectionRefusedError: [Errno 111] Connection refused` when connecting to RCON
**Root cause**: RCON not enabled in server.properties or wrong port
**Fix**: Set `enable-rcon=true` and `rcon.port=25575` in server.properties, restart server
**Reference**: https://minecraft.fandom.com/wiki/Server.properties

### TS-15: GitHub Rate Limited
**Symptom**: `HTTP 403: API rate limit exceeded`
**Root cause**: Unauthenticated requests limited to 60/hour
**Fix**: Set token: `export GITHUB_TOKEN=ghp_xxx` or use authenticated git clone
**Reference**: https://docs.github.com/en/rest/overview/resources-in-the-rest-api#rate-limiting

### TS-16: SSL Certificate Error
**Symptom**: `SSLError: certificate verify failed`
**Root cause**: Outdated CA certificates or corporate proxy
**Fix**: `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org <package>`
**Reference**: https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-trusted-host

### TS-17: WebSocket Connection Failed
**Symptom**: `WebSocket connection to 'ws://localhost:4455/' failed`
**Root cause**: OBS WebSocket server not running or wrong port
**Fix**: Enable WebSocket server in OBS Tools menu, verify port in settings
**Reference**: https://github.com/obsproject/obs-websocket

### TS-18: Docker Pull Timeout
**Symptom**: `Error: pull access denied` or timeout pulling images
**Root cause**: Docker Hub rate limits or network issues
**Fix**: `docker login` or use mirror: `docker pull registry.docker-cn.com/library/<image>`
**Reference**: https://docs.docker.com/docker-hub/download-rate-limit/

---

## Runtime Issues

### TS-19: Paper Boot Timeout
**Symptom**: Server startup hangs at "Loading libraries..." or times out
**Root cause**: Insufficient memory or slow disk I/O
**Fix**: Increase heap: `java -Xmx4G -Xms4G -jar paper.jar` and use SSD storage
**Reference**: https://docs.papermc.io/paper/aikars-flags

### TS-20: Mineflayer Login Fail
**Symptom**: `Error: Failed to login: Invalid credentials` or timeout
**Root cause**: Wrong auth mode or Microsoft auth required
**Fix**: Set `auth: 'offline'` for cracked servers or use `prismarine-auth-client` for Microsoft
**Reference**: https://github.com/PrismarineJS/mineflayer#api

### TS-21: OBS WebSocket Auth Fail
**Symptom**: `Error: Authentication failed` connecting to OBS WebSocket
**Root cause**: Password mismatch or not configured
**Fix**: Check password in OBS Tools > WebSocket Server Settings, use same in client
**Reference**: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md

### TS-22: DepthAnything OOM
**Symptom**: `CUDA out of memory` when running DepthAnything inference
**Root cause**: GPU VRAM insufficient for model size
**Fix**: Use smaller model variant or reduce batch size: `model = DepthAnything.from_pretrained('depth-anything/Depth-Anything-V2-Small')`
**Reference**: https://github.com/DepthAnything/Depth-Anything-V2

### TS-23: Port Already in Use
**Symptom**: `OSError: [Errno 98] Address already in use`
**Root cause**: Another process using the port
**Fix**: Find and kill: `lsof -i :<port> && kill -9 <PID>` or change port in config
**Reference**: https://man7.org/linux/man-pages/man8/lsof.8.html

### TS-24: Segmentation Fault
**Symptom**: `Segmentation fault (core dumped)` during inference
**Root cause**: Incompatible library versions or GPU driver issues
**Fix**: Reinstall PyTorch matching CUDA version: `pip install torch --force-reinstall --index-url https://download.pytorch.org/whl/cu118`
**Reference**: https://pytorch.org/get-started/previous-versions/

### TS-25: Permission Denied on Script
**Symptom**: `bash: ./script.sh: Permission denied`
**Root cause**: Script lacks execute permission
**Fix**: `chmod +x script.sh`
**Reference**: https://man7.org/linux/man-pages/man1/chmod.1.html

### TS-26: Memory Lock Failed
**Symptom**: `mlockall failed: Cannot allocate memory`
**Root cause**: System limits on locked memory too low
**Fix**: Edit `/etc/security/limits.conf`: `* hard memlock unlimited` then logout/login
**Reference**: https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/6/html/performance_tuning_guide/s-memory-tuning

### TS-27: Too Many Open Files
**Symptom**: `OSError: [Errno 24] Too many open files`
**Root cause**: Process exceeding file descriptor limit
**Fix**: `ulimit -n 65535` or set permanently in `/etc/security/limits.conf`
**Reference**: https://man7.org/linux/man-pages/man1/ulimit.1p.html

### TS-28: Zombie Process Accumulation
**Symptom**: System slow with many `<defunct>` processes
**Root cause**: Parent not reaping child processes
**Fix**: Kill parent process or use `prctl(PR_SET_CHILD_SUBREAPER)` in code
**Reference**: https://man7.org/linux/man-pages/man2/prctl.2.html

---

## Lint Issues

### TS-29: mouse_dx Out of Range
**Symptom**: `LintError: mouse_dx value 5000 exceeds max 4096`
**Root cause**: Mouse delta exceeds expected range, possible data corruption
**Fix**: Check recording setup; clamp values: `mouse_dx = min(max(dx, -4096), 4096)`
**Reference**: See R030 data schema spec

### TS-30: Pitch Out of Range
**Symptom**: `LintError: pitch value 100 outside valid range [-90, 90]`
**Root cause**: Invalid pitch value in recording
**Fix**: Validate: `pitch = max(-90, min(90, pitch))` and re-record if systematic
**Reference**: See R030 coordinate system spec

### TS-31: fx != fy (Focal Length Mismatch)
**Symptom**: `LintError: fx (800) != fy (801), expected equal`
**Root cause**: Non-square pixels or calibration error
**Fix**: Re-calibrate camera or verify camera intrinsics; use `fx = fy = (fx + fy) / 2`
**Reference**: See R030 camera intrinsics spec

### TS-32: Quaternion Not Unit
**Symptom**: `LintError: quaternion norm 1.05 != 1.0`
**Root cause**: Floating point drift or corrupted rotation data
**Fix**: Normalize: `q = q / np.linalg.norm(q)`
**Reference**: See R030 rotation format spec

### TS-33: Frame Gap Detected
**Symptom**: `LintError: frame gap of 50ms between frame 100 and 101`
**Root cause**: Dropped frames during recording
**Fix**: Check disk I/O during recording; interpolate missing frames if acceptable
**Reference**: See R030 timing requirements

### TS-34: Record Count Mismatch
**Symptom**: `LintError: expected 1000 records, found 998`
**Root cause**: Incomplete write or truncated file
**Fix**: Re-run recording; verify disk space and process completion
**Reference**: See R030 data integrity spec

### TS-35: Timestamp Not Monotonic
**Symptom**: `LintError: timestamp decreased from 1000 to 990`
**Root cause**: Clock sync issue or data corruption
**Fix**: Sort records by timestamp; investigate source of non-monotonicity
**Reference**: See R030 timestamp spec

### TS-36: Invalid Block ID
**Symptom**: `LintError: unknown block ID 99999`
**Root cause**: Modded blocks or outdated block registry
**Fix**: Update block registry mapping; add custom block definitions
**Reference**: See R030 block registry spec

### TS-37: Y Coordinate Below Bedrock
**Symptom**: `LintError: Y=-10 below minimum -64`
**Root cause**: Invalid position data or wrong world height
**Fix**: Validate world height range; check coordinate transformation
**Reference**: See R030 world coordinate spec

### TS-38: Duplicate Frame ID
**Symptom**: `LintError: duplicate frame_id 12345`
**Root cause**: Frame counter reset or data duplication
**Fix**: Ensure unique frame IDs; check recording loop logic
**Reference**: See R030 frame ID spec

### TS-39: Missing Required Field
**Symptom**: `LintError: missing required field 'yaw' in record 500`
**Root cause**: Incomplete data capture or serialization error
**Fix**: Verify all required fields populated; check schema version
**Reference**: See R030 schema definition

### TS-40: Invalid Action Type
**Symptom**: `LintError: unknown action type 99`
**Root cause**: Action type not in defined enum
**Fix**: Update action type enum or fix data source
**Reference**: See R030 action schema spec

---

## Submission Issues

### TS-41: Manifest SHA Mismatch
**Symptom**: `ValidationError: file.sha256 does not match computed hash`
**Root cause**: File modified after manifest creation or corruption
**Fix**: Regenerate manifest: `sha256sum <file> > checksums.txt` and repackage
**Reference**: See R040 submission spec

### TS-42: S3 Access Denied
**Symptom**: `AccessDenied: Access Denied` when uploading to S3
**Root cause**: Missing or insufficient IAM permissions
**Fix**: Verify bucket policy and IAM role; ensure `s3:PutObject` permission
**Reference**: https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-access-control.html

### TS-43: Tarball Corrupt
**Symptom**: `tar: Unexpected EOF in archive` or `gzip: invalid compressed data`
**Root cause**: Incomplete upload or disk error during creation
**Fix**: Verify tarball integrity: `tar -tzf archive.tar.gz` and re-create if needed
**Reference**: https://www.gnu.org/software/tar/manual/

### TS-44: File Size Exceeds Limit
**Symptom**: `ValidationError: file size 5GB exceeds maximum 4GB`
**Root cause**: Submission package too large
**Fix**: Split into multiple archives or compress with higher ratio: `tar -cvJf archive.tar.xz`
**Reference**: See R040 size limits

### TS-45: Missing Metadata File
**Symptom**: `ValidationError: metadata.json not found in archive`
**Root cause**: Incorrect archive structure
**Fix**: Ensure archive contains `metadata.json` at root level
**Reference**: See R040 archive structure spec

### TS-46: Invalid JSON Format
**Symptom**: `JSONDecodeError: Expecting ',' delimiter`
**Root cause**: Malformed JSON in metadata or config files
**Fix**: Validate JSON: `python -m json.tool metadata.json` and fix syntax errors
**Reference**: https://www.json.org/json-en.html

### TS-47: Version Mismatch
**Symptom**: `ValidationError: schema version 1.0 not supported, expected 2.0`
**Root cause**: Outdated submission format
**Fix**: Update metadata to current schema version; regenerate with latest tools
**Reference**: See R040 schema versioning

### TS-48: Missing Checksum File
**Symptom**: `ValidationError: SHA256SUMS file not found`
**Root cause**: Checksum file not included in submission
**Fix**: Generate: `sha256sum * > SHA256SUMS` and include in archive
**Reference**: See R040 checksum requirements

### TS-49: Upload Timeout
**Symptom**: `ConnectionTimeout: Connection timed out` during S3 upload
**Root cause**: Large file or slow network
**Fix**: Use multipart upload or increase timeout: `aws s3 cp --cli-read-timeout 300`
**Reference**: https://docs.aws.amazon.com/cli/latest/topic/s3-faq.html

### TS-50: Invalid Bucket Name
**Symptom**: `InvalidBucketName: The specified bucket is not valid`
**Root cause**: Bucket name doesn't match expected submission bucket
**Fix**: Verify bucket name in submission instructions; check region
**Reference**: See R040 submission endpoint spec

---

## Additional Common Issues

### TS-51: Environment Variable Not Set
**Symptom**: `KeyError: 'API_KEY'` or `NoneType` for env vars
**Root cause**: Required environment variable not configured
**Fix**: Export variable: `export API_KEY=xxx` or add to `.env` file and load with `python-dotenv`
**Reference**: https://github.com/theskumar/python-dotenv

### TS-52: Git LFS Not Pulled
**Symptom**: `error: external file is 0 bytes` or binary files are pointers
**Root cause**: Git LFS files not downloaded
**Fix**: `git lfs pull` or `git lfs fetch --all`
**Reference**: https://git-lfs.github.com/

### TS-53: Virtualenv Not Activated
**Symptom**: Packages installed but `ModuleNotFoundError` when running
**Root cause**: Virtual environment not activated in current shell
**Fix**: `source venv/bin/activate` (Linux/Mac) or `venv\Scripts\activate` (Windows)
**Reference**: https://docs.python.org/3/library/venv.html

### TS-54: Disk Space Exhausted
**Symptom**: `OSError: [Errno 28] No space left on device`
**Root cause**: Disk full from logs, caches, or large datasets
**Fix**: Clean up: `du -sh * | sort -h` to find large directories; clear caches
**Reference**: https://man7.org/linux/man-pages/man1/du.1.html

### TS-55: Incompatible Python Architecture
**Symptom**: `OSError: Python is not installed as a framework` (macOS)
**Root cause**: Wrong Python build for OS
**Fix**: Install Python via Homebrew: `brew install python@3.10` or use official installer
**Reference**: https://docs.brew.sh/Homebrew-and-Python

### TS-56: Locale Not Set
**Symptom**: `UnicodeEncodeError: 'ascii' codec can't encode character`
**Root cause**: System locale not configured for UTF-8
**Fix**: `export LC_ALL=en_US.UTF-8 && export LANG=en_US.UTF-8`
**Reference**: https://wiki.archlinux.org/title/Locale

### TS-57: Inotify Watch Limit
**Symptom**: `SystemError: inotify watch limit reached`
**Root cause**: Too many file watchers for hot reload/file watching
**Fix**: `echo fs.inotify.max_user_watches=524288 | sudo tee -a /etc/sysctl.conf && sudo sysctl -p`
**Reference**: https://man7.org/linux/man-pages/man7/inotify.7.html

### TS-58: GPU Driver Mismatch
**Symptom**: `CUDA driver version is insufficient for CUDA runtime version`
**Root cause**: NVIDIA driver older than CUDA toolkit
**Fix**: Update NVIDIA driver to match CUDA version requirements
**Reference**: https://docs.nvidia.com/deploy/cuda-compatibility/

### TS-59: Shared Library Not Found
**Symptom**: `ImportError: libxxx.so.1: cannot open shared object file`
**Root cause**: Library installed but not in library path
**Fix**: `export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH` or install to standard path
**Reference**: https://man7.org/linux/man-pages/man8/ldconfig.8.html

### TS-60: Process Killed by OOM Killer
**Symptom**: Process dies unexpectedly, `dmesg` shows `Out of memory: Kill process`
**Root cause**: System RAM exhausted, kernel killed process
**Fix**: Add swap: `sudo fallocate -l 8G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
**Reference**: https://www.kernel.org/doc/Documentation/cgroup-v1/memory.txt

---

## Quick Reference

| Category | Common Fix |
|----------|------------|
| Install | Use virtualenv/pyenv, check PATH |
| Network | Check firewall, use mirrors, increase timeout |
| Runtime | Check memory, ports, permissions |
| Lint | Validate data, check schema |
| Submission | Verify checksums, check permissions |

For issues not covered here, check the project repository issues or contact support.