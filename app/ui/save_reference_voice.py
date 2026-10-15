"""Resolve designed voice name collisions before writing reference audio."""
from PySide6.QtWidgets import QInputDialog, QMessageBox


def save_designed_voice(parent, gallery, engine, source, *, translate=None, **metadata):
    tr = translate or (lambda key, fallback: fallback)
    name = metadata["name"].strip()
    while True:
        matches = [voice for voice in gallery.list_voices(engine)
                   if voice.is_reference_audio and voice.name.strip().casefold() == name.casefold()]
        if not matches:
            return gallery.import_reference_voice(engine, source, **{**metadata, "name": name})
        if len(matches) == 1 and matches[0].is_user_import:
            answer = QMessageBox.question(
                parent, tr("voice_conflict_title", "Voice already exists"),
                tr("voice_conflict_overwrite", 'A voice named "{name}" already exists.\nOverwrite it with this preview? Choose No to use a different name.').format(name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return None
            if answer == QMessageBox.StandardButton.Yes:
                values = {"gender": "", "age_style": "", **metadata, "name": name}
                return gallery.update_reference_voice(matches[0].voice_id, source=source, **values)
        else:
            QMessageBox.warning(parent, tr("voice_conflict_title", "Voice already exists"),
                tr("voice_conflict_rename", 'The name "{name}" is already in use. Choose a different name to avoid ambiguity.').format(name=name))
        name, accepted = QInputDialog.getText(parent, tr("voice_conflict_save", "Save voice"), tr("voice_conflict_name", "New voice name:"), text=name)
        name = name.strip()
        if not accepted or not name:
            return None
