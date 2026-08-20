"""Playblast Options... dialog — the studio-facing configuration UI for
UkorePlayblast, living entirely inside Maya (confirmed with the user
2026-07-19: this tool's options belong in Maya, not a UkoreHub Repository
Setting tab — the desktop-side settings_page.py that used to be here was
removed). Opened via the "Playblast Options..." item under Ukore Tools >
Anim, registered directly into ukore_menu's central registry by this
package's own __init__.py (merged into UkoreShot 2026-08-20 — previously
wired through MayaToolkit's Animation menu option-box). Same field set the
old desktop settings page had, same PluginConfigStore-backed persistence
(options_store.py) — only the UI moved."""

from __future__ import annotations

import maya.cmds as cmds
from PublishApi import repo_paths
from tmlib.module.PySide import QtWidgets
from tmlib.ui.interface_template import get_maya_window
from UkorePlayblast import function, options_store

# "image" used to be a third choice here, letting a video playblast turn
# into a full-timeline image sequence via Maya's own per-frame numbering —
# removed 2026-07-20 in favor of the dedicated Video/Image (Current Frame)
# output-mode radios below, since that's a materially different capture
# (one specific frame, added as a new index onto an existing version) not
# just a different container format.
_FORMATS = ["avi", "qt"]
_IMAGE_FORMATS = ["png", "jpg", "tif"]


class PlayblastOptionsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent or get_maya_window())
        self.setWindowTitle("Playblast Options")
        self.setMinimumWidth(360)

        project, repo, _repo_path = repo_paths.get_active_repo()
        self._project_id = project.id if project is not None else None
        self._repo_id = repo.id if repo is not None else None

        options = (
            options_store.get_options(self._project_id, self._repo_id)
            if self._project_id and self._repo_id
            else dict(options_store.DEFAULT_OPTIONS)
        )

        # -- Naming / Output ---------------------------------------------------
        # Variation feeds the naming convention's "variation" token
        # (SEQ_ShotCode_Variation_index_version — see maya-scripts/README.md) —
        # options_store.get_variations returns BUILTIN_VARIATIONS
        # (layout/blocking/spline) plus whatever this repo has added.
        self.variation_combo = QtWidgets.QComboBox()
        self.add_variation_button = QtWidgets.QPushButton("Add...")
        self.add_variation_button.clicked.connect(self._on_add_variation)
        variation_row = QtWidgets.QHBoxLayout()
        variation_row.addWidget(self.variation_combo, 1)
        variation_row.addWidget(self.add_variation_button)

        # Video (existing behavior, a new version every playblast) vs.
        # Image - Current Frame (added 2026-07-20: captures only whatever
        # frame the playhead is on right now, not the timeline range, and
        # adds a new index onto the *existing* version for this shot/
        # variation instead of starting a fresh one — see function.py's
        # _resolve_filename_stem). image_format_combo used to only matter
        # in the latter — Video mode now also writes a full-range image
        # sequence alongside the video (function.py's still-image capture
        # pass), using this same format, so it stays enabled in both modes.
        self.output_video_radio = QtWidgets.QRadioButton("Video")
        self.output_image_radio = QtWidgets.QRadioButton("Image (Current Frame)")
        output_mode_group = QtWidgets.QButtonGroup(self)
        output_mode_group.addButton(self.output_video_radio)
        output_mode_group.addButton(self.output_image_radio)
        self.image_format_combo = QtWidgets.QComboBox()
        self.image_format_combo.addItems(_IMAGE_FORMATS)
        self.output_image_radio.toggled.connect(self._on_output_mode_changed)

        naming_box = QtWidgets.QGroupBox("Naming / Output")
        naming_form = QtWidgets.QFormLayout(naming_box)
        naming_form.addRow("Variation", variation_row)
        naming_form.addRow(self.output_video_radio)
        naming_form.addRow(self.output_image_radio)
        naming_form.addRow("Image Format", self.image_format_combo)

        # -- Resolution ----------------------------------------------------
        self.resolution_render_radio = QtWidgets.QRadioButton("Use render settings")
        self.resolution_custom_radio = QtWidgets.QRadioButton("Custom")
        resolution_group = QtWidgets.QButtonGroup(self)
        resolution_group.addButton(self.resolution_render_radio)
        resolution_group.addButton(self.resolution_custom_radio)
        self.width_spin = QtWidgets.QSpinBox()
        self.width_spin.setRange(16, 8192)
        self.height_spin = QtWidgets.QSpinBox()
        self.height_spin.setRange(16, 8192)
        self.resolution_custom_radio.toggled.connect(self.width_spin.setEnabled)
        self.resolution_custom_radio.toggled.connect(self.height_spin.setEnabled)

        resolution_box = QtWidgets.QGroupBox("Resolution")
        resolution_form = QtWidgets.QFormLayout(resolution_box)
        resolution_form.addRow(self.resolution_render_radio)
        resolution_form.addRow(self.resolution_custom_radio)
        resolution_form.addRow("Width", self.width_spin)
        resolution_form.addRow("Height", self.height_spin)

        # -- Format / quality ------------------------------------------------
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(_FORMATS)
        self.compression_edit = QtWidgets.QLineEdit()
        self.compression_edit.setPlaceholderText("blank = uncompressed")
        self.quality_spin = QtWidgets.QSpinBox()
        self.quality_spin.setRange(0, 100)
        self.quality_spin.setSuffix("%")
        self.percent_spin = QtWidgets.QSpinBox()
        self.percent_spin.setRange(1, 100)
        self.percent_spin.setSuffix("%")

        format_box = QtWidgets.QGroupBox("Format / Quality")
        format_form = QtWidgets.QFormLayout(format_box)
        format_form.addRow("Format", self.format_combo)
        format_form.addRow("Compression", self.compression_edit)
        format_form.addRow("Quality", self.quality_spin)
        format_form.addRow("Viewport Scale", self.percent_spin)

        # -- Frame range -----------------------------------------------------
        self.frame_range_current_radio = QtWidgets.QRadioButton("Current timeline")
        self.frame_range_custom_radio = QtWidgets.QRadioButton("Custom")
        frame_range_group = QtWidgets.QButtonGroup(self)
        frame_range_group.addButton(self.frame_range_current_radio)
        frame_range_group.addButton(self.frame_range_custom_radio)
        self.start_frame_spin = QtWidgets.QSpinBox()
        self.start_frame_spin.setRange(-100000, 100000)
        self.end_frame_spin = QtWidgets.QSpinBox()
        self.end_frame_spin.setRange(-100000, 100000)
        self.frame_range_custom_radio.toggled.connect(self.start_frame_spin.setEnabled)
        self.frame_range_custom_radio.toggled.connect(self.end_frame_spin.setEnabled)

        frame_range_box = QtWidgets.QGroupBox("Frame Range")
        frame_range_form = QtWidgets.QFormLayout(frame_range_box)
        frame_range_form.addRow(self.frame_range_current_radio)
        frame_range_form.addRow(self.frame_range_custom_radio)
        frame_range_form.addRow("Start", self.start_frame_spin)
        frame_range_form.addRow("End", self.end_frame_spin)

        # -- Other ------------------------------------------------------------
        self.camera_edit = QtWidgets.QLineEdit()
        self.camera_edit.setPlaceholderText("Active viewport camera")
        self.sound_check = QtWidgets.QCheckBox("Include sound")
        self.ornaments_check = QtWidgets.QCheckBox("Show ornaments (HUD)")

        other_box = QtWidgets.QGroupBox("Other")
        other_form = QtWidgets.QFormLayout(other_box)
        other_form.addRow("Camera", self.camera_edit)
        other_form.addRow(self.sound_check)
        other_form.addRow(self.ornaments_check)

        self.destination_label = QtWidgets.QLabel()
        self.destination_label.setWordWrap(True)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.playblast_button = buttons.addButton("Playblast", QtWidgets.QDialogButtonBox.ActionRole)
        self.playblast_button.clicked.connect(self._on_playblast)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        if not (self._project_id and self._repo_id):
            warning = QtWidgets.QLabel(
                "No active repo selected in UkoreHub — showing defaults, changes won't be saved."
            )
            warning.setWordWrap(True)
            layout.addWidget(warning)
        layout.addWidget(self.destination_label)
        layout.addWidget(naming_box)
        layout.addWidget(resolution_box)
        layout.addWidget(format_box)
        layout.addWidget(frame_range_box)
        layout.addWidget(other_box)
        layout.addWidget(buttons)

        self._format_box = format_box
        self._frame_range_box = frame_range_box

        self._load(options)
        self._refresh_destination_label()

        # Live-updates the destination preview as the exact fields that
        # feed the filename change, not just on open/after a playblast
        # like before 2026-07-20 — the filename is now the main carrier of
        # shot/variation/index/version information, so seeing it update
        # immediately matters more than it used to when only the folder
        # was being previewed.
        self.variation_combo.currentTextChanged.connect(self._refresh_destination_label)
        self.output_video_radio.toggled.connect(self._refresh_destination_label)
        self.format_combo.currentTextChanged.connect(self._refresh_destination_label)
        self.image_format_combo.currentTextChanged.connect(self._refresh_destination_label)

    def _load(self, options: dict) -> None:
        self._reload_variation_combo(options.get("variation", "layout"))
        self.output_video_radio.setChecked(options.get("output_mode", "video") != "current_frame_image")
        self.output_image_radio.setChecked(options.get("output_mode", "video") == "current_frame_image")
        self.image_format_combo.setCurrentText(options.get("image_format", "png"))
        self._on_output_mode_changed(self.output_image_radio.isChecked())

        self.resolution_render_radio.setChecked(options["resolution_mode"] == "render_settings")
        self.resolution_custom_radio.setChecked(options["resolution_mode"] == "custom")
        self.width_spin.setValue(options["width"])
        self.height_spin.setValue(options["height"])
        self.width_spin.setEnabled(options["resolution_mode"] == "custom")
        self.height_spin.setEnabled(options["resolution_mode"] == "custom")
        self.format_combo.setCurrentText(options["format"])
        self.compression_edit.setText(options["compression"])
        self.quality_spin.setValue(options["quality"])
        self.percent_spin.setValue(options["percent"])
        self.frame_range_current_radio.setChecked(options["frame_range_mode"] == "current_timeline")
        self.frame_range_custom_radio.setChecked(options["frame_range_mode"] == "custom")
        self.start_frame_spin.setValue(options["start_frame"])
        self.end_frame_spin.setValue(options["end_frame"])
        self.start_frame_spin.setEnabled(options["frame_range_mode"] == "custom")
        self.end_frame_spin.setEnabled(options["frame_range_mode"] == "custom")
        self.camera_edit.setText(options["camera"])
        self.sound_check.setChecked(options["sound"])
        self.ornaments_check.setChecked(options["show_ornaments"])

    def _collect_options(self) -> dict:
        return {
            "variation": self.variation_combo.currentText() or "layout",
            "output_mode": "current_frame_image" if self.output_image_radio.isChecked() else "video",
            "image_format": self.image_format_combo.currentText(),
            "resolution_mode": "custom" if self.resolution_custom_radio.isChecked() else "render_settings",
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "format": self.format_combo.currentText(),
            "compression": self.compression_edit.text().strip(),
            "quality": self.quality_spin.value(),
            "percent": self.percent_spin.value(),
            "frame_range_mode": "custom" if self.frame_range_custom_radio.isChecked() else "current_timeline",
            "start_frame": self.start_frame_spin.value(),
            "end_frame": self.end_frame_spin.value(),
            "camera": self.camera_edit.text().strip(),
            "sound": self.sound_check.isChecked(),
            "show_ornaments": self.ornaments_check.isChecked(),
        }

    def _refresh_destination_label(self) -> None:
        if not (self._project_id and self._repo_id):
            self.destination_label.setText("Playblast destination: no active repo selected in UkoreHub.")
            return
        try:
            destination = function.resolve_destination_path()
            self.destination_label.setText("Playblast destination: {}".format(destination))
        except Exception as e:
            self.destination_label.setText("Playblast destination: {}".format(e))

    def _on_output_mode_changed(self, image_checked: bool) -> None:
        # Frame range and sound don't apply to a single-current-frame
        # image capture; format/compression are the video container's own
        # settings, only relevant in Video mode. image_format_combo stays
        # enabled in both modes now — Video mode uses it for its own
        # image-sequence capture, not just Image (Current Frame)'s still.
        self._format_box.setEnabled(not image_checked)
        self._frame_range_box.setEnabled(not image_checked)
        self.sound_check.setEnabled(not image_checked)

    def _reload_variation_combo(self, select=None) -> None:
        current = select if select is not None else self.variation_combo.currentText()
        self.variation_combo.blockSignals(True)
        self.variation_combo.clear()
        self.variation_combo.addItems(options_store.get_variations(self._project_id, self._repo_id))
        index = self.variation_combo.findText(current)
        self.variation_combo.setCurrentIndex(index if index >= 0 else 0)
        self.variation_combo.blockSignals(False)

    def _on_add_variation(self) -> None:
        if not (self._project_id and self._repo_id):
            cmds.confirmDialog(
                title="Playblast Options",
                message="No active repo selected in UkoreHub — nothing to save the new variation to.",
            )
            return
        text, ok = QtWidgets.QInputDialog.getText(self, "Add Variation", "Variation name:")
        if not ok or not text.strip():
            return
        sanitized = options_store.add_variation(self._project_id, self._repo_id, text.strip())
        self._reload_variation_combo(sanitized)
        self._refresh_destination_label()

    def _on_accept(self) -> None:
        if not (self._project_id and self._repo_id):
            cmds.confirmDialog(
                title="Playblast Options", message="No active repo selected in UkoreHub — nothing to save to."
            )
            self.reject()
            return
        options_store.set_options(self._project_id, self._repo_id, self._collect_options())
        self.accept()

    def _on_playblast(self) -> None:
        if not (self._project_id and self._repo_id):
            cmds.confirmDialog(
                title="Playblast Options", message="No active repo selected in UkoreHub — nothing to playblast against."
            )
            return
        options_store.set_options(self._project_id, self._repo_id, self._collect_options())
        function.publish_playblast()
        self._refresh_destination_label()


def show() -> None:
    dialog = PlayblastOptionsDialog()
    dialog.exec_()
