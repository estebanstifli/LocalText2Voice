# SignPath: first Windows test build

Status (2026-10-08): **application and installer test signing, Windows signature
verification and silent installation passed**. The production certificate is pending.
This is not a production publishing workflow.

Local checks: 33 focused packaging/localization tests passed. A minimal real
PyInstaller executable was built and run with ProductName `LocalText2Voice` and
ProductVersion `2.2.0`. PowerShell parsing passed. The complete unsigned build,
Inno Setup compilation and installation of the test-signed installer passed on
GitHub-hosted Windows. This checks installation and executable integrity, not an
end-to-end GUI/TTS test of every feature.

## Successful end-to-end test

- [Workflow 37827227303](https://github.com/estebanstifli/LocalText2Voice/actions/runs/37827227303), commit `8521931c6c4e1cb45523aa37dc365ead47f12a0d`: **success**.
- [Application signing request](https://app.signpath.io/Web/f18d1304-1b63-4ca0-afd7-847be5f5d3be/SigningRequests/3d5b321b-c414-4d38-8687-a312349e466b): completed. Windows verified all three EXEs and their timestamps against the pinned test certificate.
- [Installer signing request](https://app.signpath.io/Web/f18d1304-1b63-4ca0-afd7-847be5f5d3be/SigningRequests/a4372e44-87b7-4787-a209-68a9178d8131): completed. Windows verified the installer signature and timestamp.
- [Test-only installer artifact](https://github.com/estebanstifli/LocalText2Voice/actions/runs/37827227303/artifacts/11573700330): installer, post-signing SHA-256 and `TEST-ONLY.txt`. Artifacts expire after seven days.
- Silent CPU-profile installation completed on the disposable runner. SHA-256
  of each installed application EXE matched its signed build input. The app was
  not launched and no optional engine downloads were requested.

This resolves the installer metadata rejection below. Do not rerun failed old
commits: use `main`, which includes normalization and the installation smoke test.

## Earlier CI evidence and fixes

- [Unsigned build 37816237453](https://github.com/estebanstifli/LocalText2Voice/actions/runs/37816237453): successful application and installer build.
- [Signing build 37821098825](https://github.com/estebanstifli/LocalText2Voice/actions/runs/37821098825): all three application EXEs signed and verified by Windows, then installer built successfully. The installer signing request returned `Failed`.
- [Successful application signing request](https://app.signpath.io/Web/f18d1304-1b63-4ca0-afd7-847be5f5d3be/SigningRequests/01b13947-8064-47fd-b2f8-20dfc51c4c3b).
- [Earlier failed installer request](https://app.signpath.io/Web/f18d1304-1b63-4ca0-afd7-847be5f5d3be/SigningRequests/c168d1ad-c83a-4299-858d-078f90a186b9), now diagnosed and resolved.

The initial signing run 37818551155 also signed the EXEs successfully (request
`5ec3d19c-dc68-4d51-951c-2d66b61fd5f0`) but was cancelled when its CurrentUser
test-root trust step hung. The verifier now uses temporary LocalMachine trust
on the disposable hosted runner, logs each stage, and has a five-minute step
timeout. That correction passed in run 37821098825. No trust was added locally.

The request log confirmed that Inno Setup's space-padded `ProductName` caused
the installer rejection (error reference `cc254e75-512b-434c-a516-5818b2c34fa0`).
The installer build now runs `tools/normalize_installer_metadata.py` before
calculating its checksum or uploading it. This removes only trailing padding
from the two version-resource strings and updates the PE checksum. It rejects
wrong product/version values and already signed files, and does not resize
resources or move/repack the Inno payload. On the actual 534,962,171-byte CI
installer, the payload offset, size and SHA-256 were unchanged after this fix.
Both SignPath XML configurations retain exact product/version restrictions;
no wildcard or manual configuration change is needed.
The signing token and both configuration slugs were accepted. Public v2.2.0 and
its release assets have not been replaced.

## Account configuration

Confirmed by the maintainer and GitHub secret-name inspection:

- Organization ID: `f18d1304-1b63-4ca0-afd7-847be5f5d3be`.
- Project slug: `LocalText2Voice`.
- Test policy: `test-signing`.
- CI user: `CI builds`, email confirmed, Submitter role visible in the policy.
- GitHub secret: `SIGNPATH_API_TOKEN` (value is never read or stored locally).
- The maintainer installed the SignPath GitHub App. Verify that its repository
  selection includes `estebanstifli/LocalText2Voice` and the project's GitHub.com
  Trusted Build System is configured. Installation alone does not prove access.
- Test certificate SHA-1 fingerprint: `15C8F90D4432F333EF31C8C5C1DB02E4A91F1FE6`.
  Subject: `Test certificate for 'LocalText2Voice [OSS]'`; expires 2029-10-08.
  This fingerprint identifies a public certificate, not a signing credential.

## Required action in SignPath

Open the project's **Artifact Configurations** tab. Keep the existing single-PE
configuration; create two additional XML configurations with these exact slugs:

| Slug | XML to paste/import | ZIP content |
| --- | --- | --- |
| `application-zip-v1` | [application-zip-v1.xml](../installer/signpath/application-zip-v1.xml) | Three project-owned EXEs at ZIP root |
| `installer-zip-v1` | [installer-zip-v1.xml](../installer/signpath/installer-zip-v1.xml) | `LocalText2Voice-Setup.exe` at ZIP root |

The XML requires a `version` parameter matching the PE ProductVersion and checks
ProductName `LocalText2Voice`. Do not replace these restrictions with `**/*.exe`
or `**/*.dll`, which could request signatures on third-party binaries.
The workflow selects configurations explicitly, so neither needs to be default.
If the UI does not allow these operations, ask the organization's configurator
or SignPath support; do not weaken the signing policy as a workaround.

## Running the workflow

After reviewing and committing only the signing changes, push the workflow to
`main`. Do not include unrelated uncommitted development changes by accident.

1. GitHub Actions > **Windows SignPath test (never publishes releases)**.
2. First run with `sign_test=false`: build from the checked-out source and verify
   that all upstream downloads, PE metadata and Inno Setup packaging work on CI.
3. After both XML configurations are valid, run with `sign_test=true`.
4. Approve the two signing requests in SignPath if required by the test policy:
   project executables first, installer second. Each can wait up to one hour.
5. Inspect the `TEST-ONLY-signed-installer-...` workflow artifact and its checksum.
   Send the successful workflow/signing-request links to SignPath for review.

There is no push/release trigger, release upload, production policy selector or
repository-write permission. Execution is limited to `main` in this repository
on GitHub-hosted `windows-2022`. Third-party actions are pinned by commit SHA.
GitHub's upload-artifact creates the ZIP; do not wrap a prebuilt ZIP inside it.

## Build inputs and verification

- Windows build Python: 3.13.1; embedded engine Python: existing manager's 3.11.9.
- [Upstream inputs](../tools/ci/windows-assets.json): pinned URLs and SHA-256 for
  Piper 2023.11.14-2, FFmpeg 8.0 essentials and Inno Setup 6.7.1.
- The workflow downloads tools on the runner, never an existing app release.
- Python dependencies follow the existing requirements and runtime manager.
  The build environment's resolved versions are retained as evidence. This is
  source-provenance verification, not a claim of bit-for-bit reproducibility.
- No developer config, optional AI weights or engine-deps caches are copied.
  This first clean CI package has no preinstalled voice models; download a Piper
  voice from the app when testing. It is not a replacement for the public installer.
- Product/version resources are generated for all three own EXEs. Existing Qt
  ICU and EngineHost-resource checks still run before packaging.
- Inno's padded product/version strings are normalized before signing; never
  run metadata normalization on signed output. CI also silently installs the
  resulting installer on its disposable runner and compares all three installed
  EXEs with the build. It does not launch the GUI or download optional engines.
- Test signatures must match the maintainer's exact certificate fingerprint.
  Only on the disposable GitHub runner, the verifier temporarily trusts that
  specific certificate in LocalMachine Root (avoiding CurrentUser consent UI),
  checks Windows Authenticode integrity and timestamp,
  then removes the trust entry it added. It refuses to run on ordinary local PCs.
- Returned files must exactly match the expected file list. Only the three own
  EXEs are copied back before packaging; no upstream binaries are signed.
- The installer SHA-256 is recalculated after signing. Test artifacts expire
  after seven days and do not go to GitHub Releases or the app updater.

## Before production signing

1. Send the successful workflow and both signing-request links above to SignPath
   for review. Unsigned CI, both test signing requests, signature verification and
   a silent installer smoke test have passed.
2. Add/test Inno Setup `SignedUninstaller` support. The first test workflow signs
   the application and outer installer only, not `unins*.exe` or Setup self-copies.
   Do not describe the current test as full installer/uninstaller coverage.
3. Confirm third-party license compliance with SignPath, particularly optional
   non-OSI model/engine downloads; approval of this application does not change
   upstream licenses or authorize signing third-party executables.
4. Review the [Code signing policy](../CODE_SIGNING.md), link it on the official
   download page and release notes, and configure manual production approval.
5. Ask SignPath to validate the setup and issue/import the production certificate.
6. Implement a separate production workflow with production signer verification,
   timestamp checks, smoke/install tests and publication approval. Do not reuse
   test trust settings or enable unsigned fallback for a production run.

## References

- https://docs.signpath.io/trusted-build-systems/github
- https://docs.signpath.io/artifact-configuration/syntax
- https://signpath.org/terms
- https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm
