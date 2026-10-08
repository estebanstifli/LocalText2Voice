"""Generate PE metadata for our executables without importing the application."""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLES = ("LocalText2Voice", "LocalText2VoiceEngineHost", "LocalText2VoiceMCP")


def app_version(root: Path = ROOT) -> str:
    tree = ast.parse((root / "app/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            version = ast.literal_eval(node.value)
            if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError("Windows signing requires a numeric major.minor.patch version")
            if any(int(part) > 65535 for part in version.split(".")):
                raise ValueError("Windows version components must fit in 16 bits")
            return version
    raise ValueError("Application version not found")


def version_resource(name: str, version: str) -> str:
    if name not in EXECUTABLES:
        raise ValueError("Only project-owned executable names are allowed")
    numbers = tuple(int(part) for part in version.split(".")) + (0,)
    fields = {
        "CompanyName": "Esteban / AndromedaNova.com",
        "FileDescription": name,
        "FileVersion": version,
        "InternalName": name,
        "OriginalFilename": name + ".exe",
        "ProductName": "LocalText2Voice",
        "ProductVersion": version,
    }
    strings = ",\n".join(f"            StringStruct({key!r}, {value!r})" for key, value in fields.items())
    return f"""VSVersionInfo(
    ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r},
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
{strings}
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""


def main() -> None:
    version = app_version()
    output = ROOT / "build/windows-version-info"
    output.mkdir(parents=True, exist_ok=True)
    for name in EXECUTABLES:
        (output / f"{name}.txt").write_text(version_resource(name, version), encoding="utf-8")
    print(f"Windows executable metadata: LocalText2Voice {version}")


if __name__ == "__main__":
    main()
