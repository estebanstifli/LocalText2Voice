from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap
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
ICON_ACTIVE_DARK = "#79a8ff"
ICON_NEUTRAL_DARK = "#cbd5e1"
ICON_MUTED_DARK = "#7f8da3"
ICON_DANGER_DARK = "#f87171"


_QTAWESOME_ICONS = {
    "add": "fa5s.plus",
    "apply": "fa5s.check",
    "back": "fa5s.arrow-left",
    "bolt": "fa5s.bolt",
    "bulk": "fa5s.layer-group",
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
    "location": "fa5s.map-marker-alt",
    "music": "fa5s.music",
    "mute": "fa5s.volume-mute",
    "offset": "fa5s.step-forward",
    "open": "fa5s.folder-open",
    "copy": "fa5s.copy",
    "crop": "fa5s.crop-alt",
    "cut": "fa5s.cut",
    "convert_video": "fa5s.video",
    "hide": "fa5s.eye-slash",
    "show": "fa5s.eye",
    "window_minimize": "fa5s.minus",
    "window_maximize": "fa5s.square",
    "window_restore": "fa5s.window-restore",
    "pause": "fa5s.pause",
    "person": "fa5s.user",
    "play": "fa5s.play",
    "preview": "fa5s.volume-up",
    "refresh": "fa5s.sync-alt",
    "replace_image": "fa5s.file-image",
    "regenerate": "fa5s.redo-alt",
    "render": "fa5s.magic",
    "repository": "fa5s.code-branch",
    "review": "fa5s.check-circle",
    "save": "fa5s.download",
    "settings": "fa5s.cog",
    "moon": "fa5s.moon",
    "sun": "fa5s.sun",
    "server": "fa5s.network-wired",
    "stop": "fa5s.stop",
    "storyboard": "fa5s.photo-video",
    "video_track": "fa5s.film",
    "audiobook": "fa5s.book-open",
    "tail": "fa5s.forward",
    "timeline": "fa5s.clock",
    "undo": "fa5s.undo-alt",
    "redo": "fa5s.redo-alt",
    "voice": "fa5s.user",
    "volume": "fa5s.volume-up",
    "warning": "fa5s.exclamation-circle",
    "waveform": "fa5s.wave-square",
    "zoom_in": "fa5s.search-plus",
    "zoom_out": "fa5s.search-minus",
}


_FALLBACK_ICONS = {
    "add": QStyle.StandardPixmap.SP_FileDialogNewFolder,
    "apply": QStyle.StandardPixmap.SP_DialogApplyButton,
    "back": QStyle.StandardPixmap.SP_ArrowBack,
    "bolt": QStyle.StandardPixmap.SP_MediaPlay,
    "bulk": QStyle.StandardPixmap.SP_FileDialogListView,
    "cancel": QStyle.StandardPixmap.SP_DialogCancelButton,
    "close": QStyle.StandardPixmap.SP_DialogCloseButton,
    "delete": QStyle.StandardPixmap.SP_TrashIcon,
    "edit": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "file": QStyle.StandardPixmap.SP_FileIcon,
    "folder": QStyle.StandardPixmap.SP_DirOpenIcon,
    "generate": QStyle.StandardPixmap.SP_MediaPlay,
    "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
    "language": QStyle.StandardPixmap.SP_ComputerIcon,
    "location": QStyle.StandardPixmap.SP_DirIcon,
    "music": QStyle.StandardPixmap.SP_MediaVolume,
    "mute": QStyle.StandardPixmap.SP_MediaVolume,
    "open": QStyle.StandardPixmap.SP_DialogOpenButton,
    "copy": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "crop": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "convert_video": QStyle.StandardPixmap.SP_MediaPlay,
    "window_minimize": QStyle.StandardPixmap.SP_TitleBarMinButton,
    "window_maximize": QStyle.StandardPixmap.SP_TitleBarMaxButton,
    "window_restore": QStyle.StandardPixmap.SP_TitleBarNormalButton,
    "pause": QStyle.StandardPixmap.SP_MediaPause,
    "person": QStyle.StandardPixmap.SP_ComputerIcon,
    "play": QStyle.StandardPixmap.SP_MediaPlay,
    "preview": QStyle.StandardPixmap.SP_MediaVolume,
    "refresh": QStyle.StandardPixmap.SP_BrowserReload,
    "replace_image": QStyle.StandardPixmap.SP_DialogOpenButton,
    "regenerate": QStyle.StandardPixmap.SP_BrowserReload,
    "review": QStyle.StandardPixmap.SP_DialogApplyButton,
    "save": QStyle.StandardPixmap.SP_DialogSaveButton,
    "settings": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "moon": QStyle.StandardPixmap.SP_TitleBarShadeButton,
    "sun": QStyle.StandardPixmap.SP_TitleBarUnshadeButton,
    "server": QStyle.StandardPixmap.SP_ComputerIcon,
    "stop": QStyle.StandardPixmap.SP_MediaStop,
    "storyboard": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "split": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "video_track": QStyle.StandardPixmap.SP_MediaPlay,
    "audiobook": QStyle.StandardPixmap.SP_FileIcon,
    "voice": QStyle.StandardPixmap.SP_MediaVolume,
    "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
    "undo": QStyle.StandardPixmap.SP_ArrowBack,
    "redo": QStyle.StandardPixmap.SP_ArrowForward,
}


def ui_icon(
    name: str,
    *,
    color: str | None = None,
    active: bool = False,
    danger: bool = False,
) -> QIcon:
    """Return a scalable icon that follows live application theme changes."""

    return QIcon(
        _ThemeAwareIconEngine(
            name,
            color=color,
            active=active,
            danger=danger,
        )
    )


class _ThemeAwareIconEngine(QIconEngine):
    """Resolve icon colors when Qt paints, rather than only at creation time."""

    def __init__(
        self,
        name: str,
        *,
        color: str | None = None,
        active: bool = False,
        danger: bool = False,
    ) -> None:
        super().__init__()
        self.name = name
        self.color = color
        self.active = active
        self.danger = danger
        self._icons: dict[bool, QIcon] = {}

    def clone(self) -> _ThemeAwareIconEngine:
        return _ThemeAwareIconEngine(
            self.name,
            color=self.color,
            active=self.active,
            danger=self.danger,
        )

    def paint(
        self,
        painter: QPainter,
        rect: QRect,
        mode: QIcon.Mode,
        state: QIcon.State,
    ) -> None:
        if self.name in {"copy", "paste", "split"}:
            color = self.color or _icon_color(
                self.name, active=self.active, danger=self.danger
            )
            if mode == QIcon.Mode.Disabled:
                color = ICON_MUTED_DARK if _dark_theme_active() else ICON_MUTED
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            side = min(rect.width(), rect.height())
            painter.translate(rect.x() + (rect.width() - side) / 2,
                              rect.y() + (rect.height() - side) / 2)
            painter.scale(side / 24, side / 24)
            painter.setPen(QPen(QColor(color), 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self.name == "copy":
                path = QPainterPath()
                path.moveTo(7, 17)
                path.lineTo(4, 17)
                path.lineTo(4, 4)
                path.lineTo(15, 4)
                path.lineTo(15, 7)
                painter.drawPath(path)
                painter.drawRoundedRect(QRectF(8, 8, 12, 13), 1, 1)
            elif self.name == "paste":
                path = QPainterPath()
                path.moveTo(8, 5)
                path.lineTo(5, 5)
                path.lineTo(5, 21)
                path.lineTo(19, 21)
                path.lineTo(19, 5)
                path.lineTo(16, 5)
                painter.drawPath(path)
                painter.drawRoundedRect(QRectF(8, 3, 8, 4), 1, 1)
            else:
                # Two separated clip edges, matching the timeline split symbol.
                path = QPainterPath()
                for outer, inner in ((3, 9), (21, 15)):
                    path.moveTo(outer, 6)
                    path.lineTo(inner, 6)
                    path.lineTo(inner, 18)
                    path.lineTo(outer, 18)
                painter.setPen(QPen(QColor(color), 2.4))
                painter.drawPath(path)
            painter.restore()
            return
        self._resolved_icon().paint(
            painter,
            rect,
            Qt.AlignmentFlag.AlignCenter,
            mode,
            state,
        )

    def pixmap(
        self,
        size: QSize,
        mode: QIcon.Mode,
        state: QIcon.State,
    ) -> QPixmap:
        if self.name in {"copy", "paste", "split"}:
            pixmap = QPixmap(size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
            painter.end()
            return pixmap
        return self._resolved_icon().pixmap(size, mode, state)

    def _resolved_icon(self) -> QIcon:
        dark = _dark_theme_active()
        cached = self._icons.get(dark)
        if cached is not None:
            return cached
        resolved_color = self.color or _icon_color(
            self.name,
            active=self.active,
            danger=self.danger,
        )
        if qta is not None:
            icon_name = _QTAWESOME_ICONS.get(
                self.name,
                _QTAWESOME_ICONS["file"],
            )
            try:
                icon = qta.icon(
                    icon_name,
                    color=resolved_color,
                    color_disabled=(ICON_MUTED_DARK if dark else ICON_MUTED),
                )
                self._icons[dark] = icon
                return icon
            except Exception:
                pass
        icon = _fallback_icon(self.name)
        self._icons[dark] = icon
        return icon


def _dark_theme_active() -> bool:
    application = QApplication.instance()
    return bool(
        application is not None
        and application.property("uiTheme") == "dark"
    )


def _icon_color(name: str, *, active: bool, danger: bool) -> str:
    dark = _dark_theme_active()
    if danger or name in {"cancel", "close", "delete", "stop"}:
        return ICON_DANGER_DARK if dark else ICON_DANGER
    if active:
        return ICON_ACTIVE_DARK if dark else ICON_ACTIVE
    return ICON_NEUTRAL_DARK if dark else ICON_NEUTRAL


def _fallback_icon(name: str) -> QIcon:
    application = QApplication.instance()
    if application is None:
        return QIcon()
    pixmap = _FALLBACK_ICONS.get(name, QStyle.StandardPixmap.SP_FileIcon)
    return application.style().standardIcon(pixmap)
