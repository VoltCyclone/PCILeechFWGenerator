---
name: Bug Report
about: Create a detailed bug report to help us improve PCILeech FW Generator
title: '[BUG] '
labels: ['bug', 'needs-triage']
assignees: ''

---

<!-- 
IMPORTANT: Please no UUIDs or personally identifiable information in this report
Before submitting, please search existing issues to avoid duplicates
Consider checking our documentation and wiki first
-->

## Bug Summary

**Brief Description:**
<!-- Provide a clear and concise description of the bug -->

**Severity Level:**
<!-- Select one: Critical | High | Medium | Low -->
- [ ] **Critical** - System crashes, data loss, or complete failure
- [ ] **High** - Major functionality broken, significant impact
- [ ] **Medium** - Minor functionality issues, workarounds available
- [ ] **Low** - Cosmetic issues, minor inconveniences

## Environment Details

Run help_ticket.sh and give the output

## Problem Description

**What happened?**
<!-- Describe the issue in detail. Include exact error messages, unexpected behaviors, or failures -->

**Error Messages/Logs:**
```
<!-- Paste relevant error messages, stack traces, or log outputs here -->
<!-- Use generate.log, synthesis logs, or crash dumps -->
```

**Steps to Reproduce:**
1. <!-- First step -->
2. <!-- Second step -->
3. <!-- Third step -->
4. <!-- etc. -->

**Command Line Used:**
```bash
# Paste the exact command(s) that triggered the issue
```

**Configuration Details:**
<!-- Describe any donor template, profile, or custom configuration used -->

## Expected Behavior

**What should happen instead?**
<!-- Describe what you expected to happen -->

**Reference Implementation:**
<!-- If applicable, mention if this worked in a previous version or similar setup -->

## Additional Context

**Frequency:**
- [ ] Always reproducible
- [ ] Intermittent (occurs sometimes)
- [ ] Rare (occurred once or twice)

**Impact Assessment:**
- [ ] Blocks development/testing
- [ ] Affects firmware functionality
- [ ] Synthesis/build failures
- [ ] Runtime errors
- [ ] Performance issues

**Workaround Available:**
- [ ] Yes (please describe below)
- [ ] No

**Workaround Description:**
<!-- If you found a way to work around this issue, please describe it -->

## Attachments

**Required Files:**
- [ ] Output of `help_ticket.sh`
- [ ] `generate.log` or relevant log files
- [ ] Error screenshots or terminal output

**Optional Files:**
- [ ] Custom `.tcl` files
- [ ] Custom `.sv` files
- [ ] Synthesis reports
- [ ] Waveform captures
- [ ] Core dump files

<!-- 
Attach files by dragging and dropping them here or clicking to select
Please remove any sensitive information before attaching
-->

## Debugging Information

**Commands Run for Debugging:**
```bash
# List any debugging commands you tried
# e.g., python pcileech.py --verbose, vivado -version, etc.
```

**System Information:**
```bash
# Output of system info commands (optional)
# e.g., uname -a, python --version, pip list | grep -i pci
```

## Pre-submission Checklist

**I have:**
- [ ] Checked that I'm using the latest version from the main branch
- [ ] Searched for existing similar issues
- [ ] Read the relevant documentation/wiki pages
- [ ] Included all required information above
- [ ] Removed any sensitive/personal information
- [ ] Tested with minimal reproduction case
- [ ] Verified this isn't a configuration issue

**Module-specific checks (if applicable):**
- [ ] `host_collect` - Checked Stage 1 collection, VFIO binding, and permissions
- [ ] `device_clone` / `pci_capability` - Checked device configuration and capability parsing
- [ ] `templating` - Checked SystemVerilog and TCL generation
- [ ] `vivado_handling` - Checked Stage 3 (`build_all.tcl`) logs and gates
- [ ] `flash` - Verified flash operations and board connectivity

## Labels

**Issue Type:**
- [ ] Synthesis Error
- [ ] Runtime Error
- [ ] Configuration Issue
- [ ] Documentation Issue
- [ ] Feature Request
- [ ] Performance Issue

**Component:**
- [ ] Core Generator
- [ ] Device Cloning
- [ ] Capability Parsing
- [ ] Templating (SystemVerilog and TCL)
- [ ] Flash Operations
- [ ] VFIO Integration
- [ ] TUI Interface
- [ ] Build System
