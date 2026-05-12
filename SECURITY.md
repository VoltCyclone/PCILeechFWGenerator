# Security Policy

## Supported Versions

Only the latest release on the `main` branch receives security fixes. If you're
running an older release, please upgrade before reporting an issue you can't
reproduce on `main`.

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

PCILeechFWGenerator produces firmware that interacts with PCIe DMA, VFIO, and
host kernel drivers. Bugs in this area can have real safety and privacy
consequences, so we handle them privately first.

To report a vulnerability:

1. Go to the **[Security tab][advisories]** of the repository and click
   *"Report a vulnerability"* to open a private security advisory. This is the
   preferred channel.
2. If you cannot use GitHub Security Advisories, email the maintainer at
   `ramseymcgrath@gmail.com` with the subject line
   `[PCILeechFWGenerator security]`. PGP is not required, but if you'd like to
   encrypt the report ask for a key first.

Please include:

- A description of the issue and the impact you believe it has.
- Steps to reproduce, or a minimal proof of concept.
- The version / commit SHA you tested against.
- Any suggested mitigations, if you have them.

## What to Expect

- Acknowledgement of your report within **3 business days**.
- An initial assessment (severity, whether we can reproduce it, rough timeline)
  within **10 business days**.
- Coordinated disclosure: we'll work with you on a fix and a disclosure date.
  Credit is given in the release notes unless you'd prefer to remain anonymous.

## Scope

In scope:

- Code in this repository (firmware generation, build pipeline, TUI, helper
  scripts).
- Default configurations and example flows in the documentation.

Out of scope:

- Vulnerabilities in upstream dependencies (please report those upstream; we'll
  bump the version once a fix is available).
- Issues that require an already-compromised host or physical access beyond
  what the tool itself assumes.
- Misuse of generated firmware against systems you don't own or have permission
  to test — that's a policy issue, not a vulnerability.

[advisories]: https://github.com/VoltCyclone/PCILeechFWGenerator/security/advisories/new
