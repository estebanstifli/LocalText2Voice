# Code signing policy

## Current status

The maintainer uses a purchased Certum Code Signing certificate through SimplySign
Desktop and Microsoft SignTool. The private key remains with the cloud provider;
a public .cer/.pem file alone cannot sign. Local production signing is available.
Every candidate must pass signature and package verification before publication.
Existing releases have not been retroactively signed. Check each release's status.

The 3.0.0 Windows installer, uninstaller, GUI, Engine Host and MCP executable have
verified production signatures. The certificate publisher is ESTEBAN GARCIA PEREZ.

The former SignPath Foundation test integration is retired. No GitHub Actions or
SignPath approval is required by the new local workflow. Historical test artifacts
must not be distributed as production-signed installers or trusted on user PCs.

## Responsibilities

- Committer, reviewer and signing approver: [Esteban](https://github.com/estebanstifli).
- Publisher identity is the legal name on the purchased certificate.
- Maintainer review is required for release inputs and changes to signing scripts.
- Use MFA for the provider and GitHub. Never commit certificates, keys, PINs, tokens
  or personal signing configuration; `.sign/` is excluded from Git and packaging.

## Build and signing scope

Build a clean candidate locally from reviewed sources. Sign only the project's
three EXEs (application, Engine Host and MCP), then the Inno uninstaller and Setup.
Third-party executables retain their own signatures and licenses.

Use SHA-256 and RFC 3161 timestamping. Verify Windows Authenticode trust, the exact
configured signer and the timestamp. Normalize installer metadata before signing;
never alter signed binaries. Calculate checksums only after verification. A failed
signature or missing timestamp aborts the signed build, with no unsigned fallback.
Signing does not publish releases. Publication requires a separate review and
explicit approval; signatures do not guarantee SmartScreen reputation or AV results.

See [local Windows signing](docs/WINDOWS_LOCAL_SIGNING.md) for setup, commands and
verification limits, and [retired SignPath notes](docs/SIGNPATH_SETUP.md) for history.

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
