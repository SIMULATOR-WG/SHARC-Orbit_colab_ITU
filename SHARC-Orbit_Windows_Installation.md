# SHARC-Orbit — Native Windows installation

The Windows walkthrough now lives in the root [`README.md`](README.md)
(single source of truth — install, run, update, restricted-network domains,
offline install, and troubleshooting).

- [1. Install](README.md#first-installation) · [Windows walkthrough](README.md#windows)
- [2. Run](README.md#executing)
- [3. Update](README.md#updating) — including [update from a GitHub ZIP](README.md#updating-from-zip)
- [Restricted network — domains to enable](README.md#firewall-allowlist)

**Disclaimer.** The Restricted-network table in the README is the documented
allowlist for **Windows 10 and Windows 11**. On **any other Windows version**
(Windows Server, LTSC, Sandbox, and similar images) it may also be necessary
to allow `aka.ms` and `download.microsoft.com` (HTTPS/443) to install WinGet
itself. See the disclaimer under [Restricted network](README.md#firewall-allowlist)
and [portable WinGet](README.md#winget-bootstrap).
- [Offline / air-gapped](README.md#offline-install)
- [Troubleshooting](README.md#troubleshooting)
