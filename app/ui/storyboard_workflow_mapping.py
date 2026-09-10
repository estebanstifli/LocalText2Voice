"""Map workflow inputs by node title instead of typing node IDs."""
import json
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget


def map_workflow(parent, picker, bindings, output_edit, tr):
    try:
        workflow = json.loads(Path(picker.path_edit.text().strip()).read_text(encoding="utf-8"))
        if not isinstance(workflow, dict) or not workflow or any(not isinstance(n, dict) or not isinstance(n.get("inputs"), dict) for n in workflow.values()):
            raise ValueError("Export the workflow in ComfyUI API format.")
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, "ComfyUI", str(exc))
        return
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("storyboard_mapping_title", "Map workflow inputs"))
    dialog.resize(720, 560)
    layout = QVBoxLayout(dialog)
    hint = QLabel(tr("storyboard_mapping_help", "Choose the node input for each value. Suggested matches are filled only when unambiguous. Review them before applying. Empty optional inputs keep the workflow value."))
    hint.setWordWrap(True)
    layout.addWidget(hint)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    form = QFormLayout(content)
    scroll.setWidget(content)
    layout.addWidget(scroll)
    controls = {}
    aliases = {"prompt": {"text", "prompt"}, "negative_prompt": {"negative_prompt"}, "output_prefix": {"filename_prefix"}, "frame_count": {"length", "num_frames"}, "duration": {"duration", "duration_seconds"}, "seed": {"seed", "noise_seed"}}
    inputs = []
    for node_id, node in workflow.items():
        title = node.get("_meta", {}).get("title") or node.get("class_type", "")
        for field, value in node["inputs"].items():
            if not isinstance(value, (list, dict)):
                inputs.append((f"{node_id} · {title} → {field}", f"{node_id}.{field}", field))
    for name, edit in bindings.items():
        combo = QComboBox()
        combo.setMinimumContentsLength(30)
        combo.addItem(tr("storyboard_keep_workflow", "Keep workflow value / placeholder"), "")
        for label, locator, _ in inputs:
            combo.addItem(label, locator)
        current = edit.text().strip()
        if current:
            if combo.findData(current) < 0:
                combo.addItem(f"⚠ {current}", current)
            combo.setCurrentIndex(combo.findData(current))
        else:
            matches = [locator for _, locator, field in inputs if field in aliases.get(name, {name})]
            if len(matches) == 1:
                combo.setCurrentIndex(combo.findData(matches[0]))
        controls[name] = combo
        form.addRow(name.replace("_", " ").capitalize(), combo)
    output = QComboBox()
    output.addItem(tr("storyboard_auto_output", "First available output"), "")
    for node_id, node in workflow.items():
        output.addItem(f"{node_id} · {node.get('_meta', {}).get('title') or node.get('class_type', '')}", node_id)
    output.setCurrentIndex(max(0, output.findData(output_edit.text().strip())))
    form.addRow(tr("storyboard_output_node", "Output node"), output)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        for name, combo in controls.items():
            bindings[name].setText(combo.currentData())
        output_edit.setText(output.currentData())
