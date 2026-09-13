# Windows Qt startup failure in v2.0.1 (issue #24)

## Confirmed cause

The installed application in `D:\localtext201` reproduced the reported
`ImportError: DLL load failed while importing QtGui: The specified procedure
could not be found` on 2026-09-13. Its Qt files and executable matched the
release build byte for byte.

`build/LocalText2Voice/Analysis-00.toc` recorded that PyInstaller collected
`icuuc.dll` and `icudt78.dll` from an unrelated Poppler distribution in the
Codex workspace runtime, via the inherited build `PATH`.

Qt6Core imports 20 unsuffixed ICU symbols, including `ucnv_open` and
`ucnv_close`. The collected ICU 78 library exports suffixed names such as
`ucnv_open_78` instead, and lacks all 20 names Qt needs. The Windows System32
ICU library exports all 20. The preserved v1.4.3 distribution contains the
same Qt6Core/Qt6Gui binaries as v2.0.1, but does not bundle `icuuc.dll`.

Adding the affected `_internal` directory to the DLL search path reproduced
the import failure in a separate Python process. Renaming only its
`icuuc.dll` to `icuuc.dll.issue24-backup` made that import pass and allowed
the actual installed `LocalText2Voice.exe` to open its responsive main window.
No Qt downgrade, driver change, or Visual C++ reinstall was needed.

## Repair an affected installation

Close the application and its error dialog. In the application directory,
rename `_internal\icuuc.dll` to `_internal\icuuc.dll.issue24-backup`, then
start `LocalText2Voice.exe` again. Preserve the backup until the repaired
installer has been verified. This does not modify projects, settings or models.
Reinstalling the original v2.0.1 installer restores the bad DLL.

## Build prevention

`tools/run_pyinstaller.py` invokes PyInstaller with only the active Python
installation and Windows directories on PATH, with Python isolated mode and
without inherited Qt search-path overrides. All three Windows executables use
this helper. It does not change the calling shell or machine environment.

`tools/check_windows_qt_bundle.py` rejects an ICU replacement in the GUI's
DLL directories. Both the application build and installer packaging run this
check, including `-SkipAppBuild`. The installer removes the two obsolete ICU
files during upgrades so a previous v2.0.1 installation cannot retain them.

For build validation, package a minimal QtGui/QtWidgets program through the
helper while the parent PATH still contains the conflicting Poppler directory.
Check the resulting folder, then run its executable from outside the source
tree and verify successful widget creation. Test an installer upgrade over a
folder containing the old ICU files as well as a fresh installation.

## Validation performed

- The bundle check rejected the original release folder and accepted the
  repaired installation in `D:\localtext201`.
- Built a minimal QtGui/QtWidgets executable through the isolated helper while
  Poppler and libheif remained on the parent PATH. It created a QApplication
  and widget successfully and reported ICU loaded from Windows System32.
- Compiled the updated Inno script with a separate test AppId and a minimal
  source bundle. Installed over a fixture containing both bad ICU DLLs and
  existing configuration, project and model files. Both DLLs were removed;
  all three user-data sentinels survived unchanged; the installed Qt test ran.
  The temporary test installer registration was then uninstalled.
- Existing installer tests: 3 passed. Python compilation and `git diff --check`
  passed. Logs and the minimal test build are under `build/qt_issue24_smoke`.
- The clean `v2.0.2` portable bundle has now been rebuilt locally and passes
  the ICU check. The installer also retains the upgrade cleanup rules
  described above. GitHub publication is intentionally pending while the
  Chatterbox automatic-review fix is being validated.
