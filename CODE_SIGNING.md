# Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

## Current status

The project has been accepted into the SignPath Foundation program. Application
and installer signing have been validated with a **self-signed test certificate**,
including Windows signature verification and a CI installation smoke test.
Production certificate issuance is pending SignPath's review. Previously published releases
remain unsigned; this page does not claim that they have been signed retroactively.
Test-signed files are CI artifacts, not public releases, and must not be distributed
as trusted Windows installers. Do not install the test certificate as a trusted
root on user machines.

## Responsibilities

- Committer and reviewer: [Esteban](https://github.com/estebanstifli).
- Signing approver: [Esteban](https://github.com/estebanstifli).
- CI submitter: the dedicated SignPath **CI builds** user, limited to the relevant
  signing policies; its token is stored in GitHub Actions secrets.

Maintainers must use multi-factor authentication for GitHub and SignPath. External
contributions and changes to build/signing workflows require maintainer review.
Production signing requires explicit approval by the designated approver.

## Build and signing scope

Signing candidates must be built from this repository on GitHub-hosted runners.
We sign only our own application, EngineHost and MCP executables and the installer;
third-party runtimes and libraries retain their original signatures and licenses.
Artifact configurations restrict filenames, product name and product version.
Installers are packaged after application signing; checksums are generated after
the final signature. Production publishing must verify signatures and must not
fall back silently to an unsigned or test-signed package.

See [SignPath setup](docs/SIGNPATH_SETUP.md) for the current test workflow and the
remaining production requirements, including Inno Setup uninstaller signing.

## Privacy and network activity

Local generation and review process user text and audio on the user's computer.
The app uses network access for model and dependency installation, voice catalogs
and samples, and update checks (including automatic checks in packaged builds).
Selecting a remote TTS, AI or media provider can send text, audio, images, prompts
or references to that provider. Configured local servers/MCP clients can request
operations using the user's settings. Provider terms and privacy policies apply.
Optional downloads and remote-provider credentials are not bundled in signing
artifacts. Never submit personal configuration, API keys, projects or user media
for signing. See [third-party notices](THIRD_PARTY_NOTICES.md) before redistribution.
