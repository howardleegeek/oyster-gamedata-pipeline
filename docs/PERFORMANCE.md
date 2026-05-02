# PERFORMANCE.md — Pipeline Performance Characteristics

This document summarizes the performance characteristics of the Minecraft AI agent pipeline, based on data from the 100-iteration sprint v2.

## 1. Per-Iteration Cost Breakdown

Each iteration of the pipeline consists of three main phases:

| Phase | Duration | Percentage |
|-------|----------|------------|
| Capture | 11.2s | ~11% |
| Adapter | 87.0s | ~88% |
| Lint | <1s | ~1% |
| **Total** | **~98.2s** | **100%** |

The adapter phase dominates the pipeline, accounting for approximately 88% of total iteration time.

## 2. Adapter:Capture Ratio Analysis

The adapter-to-capture ratio is approximately **7:1** (87.0s vs 11.2s).

This ratio is driven by:
- **FFmpeg video encoding**: Processing captured frames into video format
- **EXR file copying**: High-resolution frame data transfer

Both operations are I/O intensive and scale with frame resolution and video length. The capture phase is relatively lightweight, involving only screenshot acquisition from the Minecraft client.

## 3. Throughput Analysis

### Sprint Performance
- **1 sprint** = ~28-100s per iteration (varies by complexity)
- **100 iterations** completed in sprint v2

### Contention Issues
- **4× contention is BAD**: Running 4 concurrent bots causes severe resource contention
- **Paper server limit**: Maximum 2 bots recommended per Paper server instance
- Exceeding this limit causes:
  - Increased latency
  - Timeout failures
  - Unpredictable behavior

## 4. Scaling Guidance

### DO: Parallelize Across Machines
```
mac-1 ──► Paper server (bot-1, bot-2)
mac-2 ──► Paper server (bot-3, bot-4)
```

Each machine should run its own Paper server instance with a maximum of 2 bots.

### DO NOT: Parallelize via Threads
- Thread-based parallelism within a single Paper server causes contention
- Minecraft's single-threaded tick loop becomes a bottleneck
- Memory sharing between bots leads to race conditions

### Recommended Architecture
- **Horizontal scaling**: Add more machines, each with dedicated Paper server
- **Isolation**: Each Paper instance manages its own world state
- **Load balancing**: Distribute iterations across machines evenly

## 5. Failure Modes

During the 100-iteration sprint v2, **2 failures** were surfaced and resolved:

| Failure | Description | Resolution |
|---------|-------------|------------|
| Rotation drift | Camera orientation desynchronized from agent state | Fixed with quaternion-based rotation tracking |
| Pathfinder timeout | Navigation failed to complete within time limit | Fixed with increased timeout threshold and retry logic |

Both issues were identified through the sprint's stress testing and have been addressed in subsequent versions.

## 6. Resource Footprint

### Memory Usage
- **~1GB RAM per Paper server instance**
- Additional overhead for bot processes (~200-500MB each)

### Disk Usage
- **~14GB per 100-iteration sprint**
- Breakdown:
  - Video files (FFmpeg output): ~10GB
  - EXR frames: ~3GB
  - Logs and metadata: ~1GB

### Recommendations
- Monitor disk space before starting long sprints
- Clean up intermediate files after processing
- Use SSD storage for better I/O performance during capture/adapt phases

---

*Data sourced from SPRINT_REPORT.md — 100-iteration sprint v2 results.*