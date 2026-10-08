# SignPath: first Windows test build

Status: local integration prepared; **not yet validated by a SignPath signing run**.
The production certificate is pending. This is not a production publishing workflow.

Local checks: 28 focused packaging/localization tests passed. A minimal real
PyInstaller executable was built and run with ProductName `LocalText2Voice` and
ProductVersion `2.2.0`. PowerShell parsing passed. A full GitHub-hosted build,
SignPath authorization and signed-installer installation still need verification.

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
- Test signatures must match the maintainer's exact certificate fingerprint.
  Only on the disposable GitHub runner, the verifier temporarily trusts that
  specific certificate, checks Windows Authenticode integrity and timestamp,
  then removes the trust entry it added. It refuses to run on ordinary local PCs.
- Returned files must exactly match the expected file list. Only the three own
  EXEs are copied back before packaging; no upstream binaries are signed.
- The installer SHA-256 is recalculated after signing. Test artifacts expire
  after seven days and do not go to GitHub Releases or the app updater.

## Before production signing

1. Finish a successful unsigned CI build and both test signing requests.
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
