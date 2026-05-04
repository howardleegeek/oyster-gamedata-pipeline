# R052 Alpha Runbook: 1-Week Alpha Test Protocol

## Purpose
Architect recommended Phase C: 1 vendor 1 week alpha test before scale.
This document defines the alpha protocol for testing the video capture and processing pipeline with a single vendor over a 7-day period.

## 1. Goal
- **Primary Objective**: One vendor produces 50 lint-PASS clips in 7 days (~7 clips per day)
- **Success Metrics**:
  - 90%+ lint PASS rate across all submitted clips
  - 0 unrecoverable corruption incidents
  - Vendor achieves self-sufficiency in operations
  - All blockers from prior audits are closed

## 2. Setup (Day 0)
### Vendor Onboarding
1. Vendor completes onboarding via `VENDOR_ONBOARDING.md`
2. Vendor installs minipc-equivalent stack on their system
3. System requirements verification:
   - Windows 10/11 with WSL2 enabled
   - Minimum 16GB RAM
   - 500GB free disk space
   - Stable internet connection
   - OBS Studio installed and configured
4. Initial system validation:
   - WSL2 Ubuntu instance running
   - Docker containers operational
   - Capture pipeline test run
   - Lint validation test

### Environment Checklist
- [ ] WSL2 properly configured and running
- [ ] Docker daemon active in WSL2
- [ ] Required containers pulled and running
- [ ] OBS Studio configured with correct settings
- [ ] Test capture completed successfully
- [ ] Lint validation passed on test clip

## 3. Daily Checkpoints (Day 1-7)

### Day 1: Initial Operation
- **Target**: 5 clips
- **Quality**: All clips must pass lint validation
- **Manual Review**: 100% of clips manually reviewed
- **Focus**: System stability, operator training, process familiarization

### Day 3: Ramp-up Phase
- **Target**: 7 clips/day (21 total by end of day)
- **Quality**: Maintain high lint PASS rate
- **Manual Review**: 50% of clips manually reviewed
- **Focus**: Process optimization, identifying bottlenecks

### Day 5: Production Readiness
- **Target**: 10 clips/day (41 total by end of day)
- **Quality**: Consistent lint PASS rate
- **Manual Review**: 20% of clips manually reviewed
- **Focus**: Efficiency improvements, automation validation

### Day 7: Production Rate
- **Target**: 14 clips/day (50+ total by end of week)
- **Quality**: Production-level quality standards
- **Manual Review**: 5% of clips manually reviewed (production sampling rate)
- **Focus**: Sustained production capacity

## 4. Failure Modes to Monitor

### Critical Failure Modes
1. **WSL2 Reboot Mid-Capture**
   - Symptoms: Capture process interrupted, partial files
   - Detection: Incomplete clip files, missing metadata
   - Mitigation: Automatic restart mechanisms, checkpointing

2. **Disk Space Exhaustion**
   - Symptoms: Write failures, system warnings
   - Detection: Daily free space monitoring (< 50GB threshold)
   - Mitigation: Automated cleanup of temporary files, disk space alerts

3. **HuggingFace Block in Vendor's Region**
   - Symptoms: Model download failures, inference errors
   - Detection: Network connectivity tests, API response monitoring
   - Mitigation: Regional proxy configuration, fallback models

4. **OBS Audio Glitch**
   - Symptoms: Audio sync issues, dropped audio frames
   - Detection: Audio waveform analysis, sync validation in lint
   - Mitigation: Audio buffer optimization, hardware check

5. **Paper World Corruption from Prior Crashes**
   - Symptoms: Inconsistent world state, physics anomalies
   - Detection: World validation checks, consistency tests
   - Mitigation: Regular world saves, crash recovery procedures

### Daily Monitoring Checklist
- [ ] WSL2 stability (no unexpected reboots)
- [ ] Disk free space (> 50GB available)
- [ ] HuggingFace connectivity (model access functional)
- [ ] OBS audio/video sync (no glitches detected)
- [ ] Paper world integrity (no corruption)
- [ ] Network connectivity (stable throughout day)

## 5. Daily Report Template

Vendor completes this template daily and submits by EOD:

### Daily Status Report - Day [X]

**Date**: [YYYY-MM-DD]
**Vendor**: [Vendor Name/ID]
**Operator**: [Operator Name]

**Production Metrics**:
- Clips submitted today: [number]
- Total clips to date: [number]
- Lint PASS rate today: [percentage]
- Lint PASS rate cumulative: [percentage]

**Failed Clips Analysis**:
- Total failed clips: [number]
- Failure reasons:
  1. [Reason 1]: [count]
  2. [Reason 2]: [count]
  3. [Reason 3]: [count]

**Operational Metrics**:
- Operator hours today: [hours]
- System uptime: [percentage]
- Average clip processing time: [minutes]

**Issues Filed**:
1. [Issue ID] - [Brief description] - [Status]
2. [Issue ID] - [Brief description] - [Status]
3. [Issue ID] - [Brief description] - [Status]

**System Health**:
- Disk free space: [GB]
- WSL2 stability: [Stable/Unstable - details]
- Network connectivity: [Good/Poor - details]
- OBS performance: [Good/Poor - details]

**Blockers & Challenges**:
- [List any blockers preventing progress]
- [Technical challenges encountered]
- [Process inefficiencies identified]

**Plan for Next Day**:
- [Target clip count]
- [Focus areas for improvement]
- [Specific issues to address]

## 6. Escalation Protocol

### Performance Thresholds
- **Green**: >90% lint PASS rate - Continue normal operations
- **Yellow**: 80-90% lint PASS rate - Monitor closely, minor adjustments
- **Red**: <80% lint PASS rate - **PAUSE OPERATIONS**

### Red Zone Protocol
If pass rate falls below 80% on any day:

1. **Immediate Pause**: Stop all capture operations
2. **Root Cause Analysis**:
   - Review failed clips for patterns
   - Check system health metrics
   - Verify operator procedures
3. **Diagnostic Steps**:
   - Run system diagnostics
   - Test individual pipeline components
   - Validate environment configuration
4. **Resolution**:
   - Implement fixes for identified issues
   - Test fixes with small batch
   - Resume operations only when pass rate >90% in test batch
5. **Documentation**:
   - Document root cause and solution
   - Update procedures if needed
   - Report to project lead

### Escalation Contacts
- Primary Technical Contact: [Contact Info]
- Project Lead: [Contact Info]
- Emergency Support: [Contact Info]

## 7. Exit Criteria for Moving to Beta

### Quantitative Criteria
1. **Volume**: 50+ clips successfully submitted
2. **Quality**: 90%+ lint PASS rate across all clips
3. **Reliability**: 0 unrecoverable corruption incidents
4. **Efficiency**: Average processing time meets targets

### Qualitative Criteria
1. **Vendor Self-Sufficiency**:
   - Vendor can operate independently
   - Vendor can troubleshoot common issues
   - Vendor follows all procedures correctly

2. **System Stability**:
   - No critical failure modes triggered
   - All monitoring systems functional
   - Recovery procedures validated

3. **Process Maturity**:
   - Daily reporting consistent and complete
   - Issue tracking and resolution effective
   - Quality control processes working

4. **Documentation Complete**:
   - All blockers from prior audits closed
   - Procedures documented and validated
   - Known issues cataloged with workarounds

### Final Alpha Review
Before moving to beta, conduct final review:

1. **Data Review**: Analyze all 50+ clips for quality consistency
2. **Process Review**: Validate all operational procedures
3. **System Review**: Confirm system stability and performance
4. **Vendor Assessment**: Confirm vendor readiness for scale
5. **Risk Assessment**: Identify any remaining risks for beta phase

### Beta Readiness Sign-off
- [ ] All quantitative criteria met
- [ ] All qualitative criteria satisfied
- [ ] Final review completed
- [ ] Risk assessment documented
- [ ] Project lead approval obtained
- [ ] Vendor confirmed ready for beta phase

## Appendix A: Daily Targets Summary

| Day | Daily Target | Cumulative Target | Manual Review % | Focus Area |
|-----|--------------|-------------------|-----------------|------------|
| 1   | 5 clips      | 5 clips           | 100%            | System validation, training |
| 2   | 7 clips      | 12 clips          | 100%            | Process refinement |
| 3   | 7 clips      | 19 clips          | 50%             | Efficiency optimization |
| 4   | 7 clips      | 26 clips          | 50%             | Quality consistency |
| 5   | 10 clips     | 36 clips          | 20%             | Production readiness |
| 6   | 10 clips     | 46 clips          | 20%             | Sustained operations |
| 7   | 14 clips     | 60+ clips         | 5%              | Production rate validation |

## Appendix B: Lint Validation Criteria

Clips must pass all lint checks:
- Video resolution: 1920x1080
- Frame rate: 30 FPS
- Audio: 48kHz, stereo
- Duration: 30-45 seconds
- File format: MP4 with H.264/AAC
- Metadata: Complete and accurate
- Content: No corruption, glitches, or artifacts

## Appendix C: Emergency Procedures

### Immediate Response Checklist
If system fails during capture:

1. **Stop OBS recording immediately**
2. **Check WSL2 status**: `wsl --status`
3. **Verify disk space**: `df -h`
4. **Check Docker containers**: `docker ps`
5. **Review logs**: System and application logs
6. **Document error**: Time, symptoms, actions taken
7. **Escalate if needed**: Follow escalation protocol

### Recovery Procedures
- **WSL2 crash**: Restart WSL2, verify containers
- **Disk full**: Clean temporary files, archive completed clips
- **Network loss**: Wait for restoration, verify connectivity
- **OBS failure**: Restart OBS, verify settings
- **World corruption**: Restore from backup, validate state

---

*Last Updated: [Date]*
*Version: 1.0*
*For: R052 Alpha Test Phase*