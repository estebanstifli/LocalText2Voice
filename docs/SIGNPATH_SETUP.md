# SignPath: retired test integration

As of 2026-11-06, the maintainer is switching to a purchased Certum cloud code
signing certificate. The active GitHub Actions SignPath test workflow, artifact
configuration XML and temporary test-trust verifier have been removed locally.
No GitHub Actions build or SignPath approval is required by the replacement flow.
See [local Windows signing](WINDOWS_LOCAL_SIGNING.md).

## Historical evidence

The October 8 test used a self-signed SignPath certificate, not a production one.
[Run 37827227303](https://github.com/estebanstifli/LocalText2Voice/actions/runs/37827227303)
verified the three application EXEs, installer and silent installation. It also
identified Inno's padded ProductName metadata; the generic normalization fix stays.
The old scripts and full setup report remain available in Git history, including
commit `bad640bae68ae6ff299caf0d413c8a998895bfab`.

Removing tracked files does not revoke remote access. After reviewing this change,
the maintainer may revoke the unused SignPath CI token, delete SIGNPATH_API_TOKEN
from GitHub secrets and uninstall the SignPath GitHub App if no other project uses
it. Those account-level operations have not been performed by this change.
Never import the old test certificate into users' trusted roots.
