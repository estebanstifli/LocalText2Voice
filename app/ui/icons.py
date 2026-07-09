from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

try:
    import qtawesome as qta
except Exception:  # pragma: no cover - exercised only if optional dependency is absent
    qta = None


ICON_ACTIVE = "#1769ff"
ICON_NEUTRAL = "#374151"
ICON_MUTED = "#6b7280"
ICON_DANGER = "#dc2626"
ICON_LIGHT = "#ffffff"


_QTAWESOME_ICONS = {
    "apply": "fa5s.check",
    "back": "fa5s.arrow-left",
    "cancel": "fa5s.times",
    "close": "fa5s.times",
    "delete": "fa5s.trash-alt",
    "ducking": "fa5s.sliders-h",
    "edit": "fa5s.edit",
    "export": "fa5s.download",
    "fade_in": "fa5s.level-up-alt",
    "fade_out": "fa5s.level-down-alt",
    "file": "fa5s.file-import",
    "folder": "fa5s.folder-open",
    "generate": "fa5s.microphone",
    "info": "fa5s.info-circle",
    "language": "fa5s.globe",
    "music": "fa5s.music",
    "offset": "fa5s.step-forward",
    "open": "fa5s.folder-open",
    "window_minimize": "fa5s.minus",
    "window_maximize": "fa5s.square",
    "window_restore": "fa5s.window-restore",
    "pause": "fa5s.pause",
    "play": "fa5s.play",
    "preview": "fa5s.volume-up",
    "refresh": "fa5s.sync-alt",
    "regenerate": "fa5s.redo-alt",
    "render": "fa5s.magic",
    "repository": "fa5s.code-branch",
    "review": "fa5s.check-circle",
    "save": "fa5s.download",
    "settings": "fa5s.cog",
    "stop": "fa5s.stop",
    "tail": "fa5s.forward",
    "timeline": "fa5s.clock",
    "voice": "fa5s.user",
    "volume": "fa5s.volume-up",
    "warning": "fa5s.exclamation-circle",
    "waveform": "fa5s.wave-square",
    "zoom_in": "fa5s.search-plus",
    "zoom_out": "fa5s.search-minus",
}


_FALLBACK_ICONS = {
    "apply": QStyle.StandardPixmap.SP_DialogApplyButton,
    "back": QStyle.StandardPixmap.SP_ArrowBack,
    "cancel": QStyle.StandardPixmap.SP_DialogCancelButton,
    "close": QStyle.StandardPixmap.SP_DialogCloseButton,
    "delete": QStyle.StandardPixmap.SP_TrashIcon,
    "edit": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "file": QStyle.StandardPixmap.SP_FileIcon,
    "folder": QStyle.StandardPixmap.SP_DirOpenIcon,
    "generate": QStyle.StandardPixmap.SP_MediaPlay,
    "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
    "language": QStyle.StandardPixmap.SP_ComputerIcon,
    "music": QStyle.StandardPixmap.SP_MediaVolume,
    "open": QStyle.StandardPixmap.SP_DialogOpenButton,
    "window_minimize": QStyle.StandardPixmap.SP_TitleBarMinButton,
    "window_maximize": QStyle.StandardPixmap.SP_TitleBarMaxButton,
    "window_restore": QStyle.StandardPixmap.SP_TitleBarNormalButton,
    "pause": QStyle.StandardPixmap.SP_MediaPause,
    "play": QStyle.StandardPixmap.SP_MediaPlay,
    "preview": QStyle.StandardPixmap.SP_MediaVolume,
    "refresh": QStyle.StandardPixmap.SP_BrowserReload,
    "regenerate": QStyle.StandardPixmap.SP_BrowserReload,
    "review": QStyle.StandardPixmap.SP_DialogApplyButton,
    "save": QStyle.StandardPixmap.SP_DialogSaveButton,
    "settings": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "stop": QStyle.StandardPixmap.SP_MediaStop,
    "voice": QStyle.StandardPixmap.SP_MediaVolume,
    "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
}


def ui_icon(
    name: str,
    *,
    color: str | None = None,
    active: bool = False,
    danger: bool = False,
) -> QIcon:
    """Return a scalable app icon using QtAwesome, with a Qt fallback."""
    resolved_color = color or _icon_color(name, active=active, danger=danger)
    if qta is not None:
        icon_name = _QTAWESOME_ICONS.get(name, _QTAWESOME_ICONS["file"])
        try:
            return qta.icon(icon_name, color=resolved_color, color_disabled=ICON_MUTED)
        except Exception:
            pass
    return _fallback_icon(name)


def _icon_color(name: str, *, active: bool, danger: bool) -> str:
    if danger or name in {"cancel", "close", "delete", "stop"}:
        return ICON_DANGER
    if active:
        return ICON_ACTIVE
    return ICON_NEUTRAL


def _fallback_icon(name: str) -> QIcon:
    application = QApplication.instance()
    if application is None:
        return QIcon()
    pixmap = _FALLBACK_ICONS.get(name, QStyle.StandardPixmap.SP_FileIcon)
    return application.style().standardIcon(pixmap)
