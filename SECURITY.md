# Security policy

## Reporting a vulnerability

Open a GitHub security advisory at [`Security → Advisories → New draft`](https://github.com/klh-homes/ha-goodlifetaiwan-packages/security/advisories/new). This keeps the report private until we publish a fix.

Please include:

- A concise description of the issue.
- Steps to reproduce, ideally with a minimal reproducer.
- The affected integration version (`manifest.json`).
- Impact assessment as you see it.

Please do **not**:

- File a public GitHub issue for an unpatched vulnerability.
- Include real phone numbers, tokens, or personally identifiable data in the report. Mask or redact — our own logs show only the last 4 digits of phone numbers.

## Scope

In scope:

- Auth token handling (refresh, rotation, storage).
- Injection or data leakage via service responses or event payloads.
- Any path that could let a third party drive pickup QR generation for a community they don't own.

Out of scope:

- Vulnerabilities in the upstream `life-spi.glf.tw` / `auth.epictech.com.tw` APIs themselves. We consume an undocumented, third-party API and cannot fix its implementation. Report those to the upstream vendor.
- Home Assistant core issues — report those to [home-assistant/core](https://github.com/home-assistant/core/security).

## Response expectations

This is a volunteer-maintained hobby project. We aim to acknowledge reports within 7 days and ship fixes within a reasonable window based on severity, but make no formal SLA.
