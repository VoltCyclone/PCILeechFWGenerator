<!--
Thanks for contributing to PCILeechFWGenerator! A few quick things:

- Keep the PR focused. Unrelated cleanup is easier to review in a separate PR.
- If this touches generated firmware behavior or VFIO/DMA flows, please flag
  it in the "Risk" section below — those changes get extra scrutiny.
- The CI runs the full test suite, lint, and security checks. Please run them
  locally first (`make test lint`) if you can.
-->

## Summary

<!-- One or two sentences: what does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup (no behavior change)
- [ ] Documentation
- [ ] Build / CI / tooling
- [ ] Security fix

## Linked issues

<!-- e.g. "Closes #123" / "Related to #456". Use "Closes" to auto-close. -->

## How was this tested?

<!--
Describe what you ran. Examples:
- `pytest tests/test_foo.py`
- Built firmware for board X, flashed and verified Y
- TUI: walked through the device-select flow with N devices
-->

## Risk / blast radius

- [ ] Touches firmware generation / RTL output
- [ ] Touches VFIO binding or kernel-driver interaction
- [ ] Touches the build pipeline (Vivado, container, release)
- [ ] None of the above — surface change only

<!-- If any of the boxes above are checked, briefly explain what could break. -->

## Checklist

- [ ] Tests added or updated where it made sense
- [ ] Docs / CHANGELOG updated if user-visible behavior changed
- [ ] No sensitive data (UUIDs, real device serials, donor dumps) in the diff
- [ ] CI is green (or I've explained the failures below)
