from PySide6.QtWidgets import QMessageBox

from app.core.installation_identity import accept_storage, storage_consent
from app.core.storyboard_temporary_storage import service_url, storage_mode


def ensure_storage_consent(config, parent, tr):
    if storage_mode(config) != "managed":
        return True
    origin = service_url(config)
    if storage_consent(origin):
        return True
    text = tr("runpod_storage_consent", "To edit an image or generate a video, LocalText2Voice may temporarily upload reference images to Cloudflare so Runpod can read them. They are deleted when the job finishes and expire after at most 24 hours. A random installation identifier manages access and usage limits. Allow temporary uploads to {service}?", service=origin)
    answer = QMessageBox.question(parent, tr("runpod_storage_consent_title", "Temporary image storage"), text,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
    if answer != QMessageBox.StandardButton.Yes:
        return False
    accept_storage(origin)
    return True
