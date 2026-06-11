from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle


_ICONS = {
    "apply": QStyle.StandardPixmap.SP_DialogApplyButton,
    "back": QStyle.StandardPixmap.SP_ArrowBack,
    "cancel": QStyle.StandardPixmap.SP_DialogCancelButton,
    "close": QStyle.StandardPixmap.SP_DialogCloseButton,
    "delete": QStyle.StandardPixmap.SP_TrashIcon,
    "file": QStyle.StandardPixmap.SP_FileIcon,
    "folder": QStyle.StandardPixmap.SP_DirOpenIcon,
    "generate": QStyle.StandardPixmap.SP_MediaPlay,
    "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
    "language": QStyle.StandardPixmap.SP_ComputerIcon,
    "open": QStyle.StandardPixmap.SP_DialogOpenButton,
    "preview": QStyle.StandardPixmap.SP_MediaVolume,
    "refresh": QStyle.StandardPixmap.SP_BrowserReload,
    "repository": QStyle.StandardPixmap.SP_DriveNetIcon,
    "save": QStyle.StandardPixmap.SP_DialogSaveButton,
    "settings": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "stop": QStyle.StandardPixmap.SP_MediaStop,
    "voice": QStyle.StandardPixmap.SP_MediaVolume,
}


def ui_icon(name: str) -> QIcon:
    """Return a portable icon supplied by the active Qt platform style."""
    application = QApplication.instance()
    if application is None:
        return QIcon()
    pixmap = _ICONS.get(name, QStyle.StandardPixmap.SP_FileIcon)
    return application.style().standardIcon(pixmap)
