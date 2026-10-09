"""Output preferences are independent of their resolved filesystem location."""
from pathlib import Path
from app.utils.paths import resolve_app_path


def output_preferences(settings: dict, project_dir: Path | None = None,
                       legacy_output: Path | None = None) -> dict:
    mode = settings.get("output_dir_mode")
    value = str(settings.get("output_dir") or legacy_output or "exports").strip()
    if mode in {"relative", "absolute"}:
        return {"output_dir_mode": mode, "output_dir": value}
    path = Path(value).expanduser()
    if path.is_absolute():
        if project_dir is not None:
            try:
                return {"output_dir_mode": "relative", "output_dir": path.relative_to(project_dir).as_posix()}
            except ValueError:
                pass
        return {"output_dir_mode": "absolute", "output_dir": str(path)}
    if value not in {"output", "exports", ""}:
        return {"output_dir_mode": "absolute", "output_dir": str(resolve_app_path(path))}
    return {"output_dir_mode": "relative", "output_dir": "exports"}


def resolve_project_output(settings: dict, project_dir: Path) -> Path:
    preferences = output_preferences(settings, project_dir)
    value = Path(preferences["output_dir"]).expanduser()
    if preferences["output_dir_mode"] == "absolute":
        if not value.is_absolute():
            raise ValueError("Choose an absolute output folder, or select relative-to-project mode.")
        return value
    if value.is_absolute() or value.drive or ".." in value.parts:
        raise ValueError("Use a relative output folder inside the project, such as exports or audio/final.")
    return project_dir / value
