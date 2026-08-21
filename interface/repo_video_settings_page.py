from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ukoreshot_plugin.core import video_compress, video_path_store


class RepoVideoSettingsPage(QWidget):
    """Repository Setting > UkoreShot. As of the cache_dir-based video
    storage change, the playblast library root is no longer something a
    studio admin picks (no more Custom Path list) — it's a fixed
    per-machine folder under UkoreHub's own cache_dir, keyed by
    project/repo, so `hint_label` here is purely informational (shows
    where this machine's videos for the active repo actually live) rather
    than a picker. See video_path_store.resolve_video_root.

    Discord support removed entirely 2026-08-21 (this plugin is
    standalone now) — the "Discord" group (Channel ID/Bot Token/Max Upload
    Size/ffmpeg Path) is gone; only the ffmpeg Path field remains, still
    used by Comment/Mark as Share's sequence extraction (core/video_sequence.py)
    and Get Video/Get Video - Commented's compression
    (core/video_compress.py).

    Same self-resolving-active-repo `refresh()` pattern every CATEGORY_REPO
    tab in this app uses (e.g. RigPublisherSettingsPage)."""

    def __init__(self, parent=None, *, api):
        super().__init__(parent)
        self._api = api
        self._project_id: str | None = None
        self._repo_id: str | None = None

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)

        # ffmpeg Path — per-machine (shared=False): a real local file path,
        # not something that makes sense synced studio-wide (every
        # machine's ffmpeg install, if any, lives wherever it lives).
        # Blank means "look up ffmpeg automatically" — see
        # video_compress.py's resolve_ffmpeg_path (an explicit path here
        # wins, else a previously cache-downloaded copy, else a fresh
        # on-demand download, else a PATH lookup).
        self.ffmpeg_edit = QLineEdit()
        self.ffmpeg_edit.setPlaceholderText("Blank = look up/download ffmpeg automatically")
        self.ffmpeg_save_button = QPushButton("Save ffmpeg Path (this machine)")
        self.ffmpeg_save_button.clicked.connect(self._on_save_ffmpeg_path)

        ffmpeg_note = QLabel(
            "ffmpeg is used to extract frame sequences for Comment/Mark as Share, and to compress the "
            "videos produced by Get Video/Get Video - Commented. Leave this blank to let UkoreShot "
            "download a copy automatically the first time it's needed on this machine."
        )
        ffmpeg_note.setWordWrap(True)
        ffmpeg_group = QGroupBox("ffmpeg")
        ffmpeg_layout = QFormLayout(ffmpeg_group)
        ffmpeg_layout.addRow(ffmpeg_note)
        ffmpeg_layout.addRow("ffmpeg Path", self.ffmpeg_edit)
        ffmpeg_layout.addRow(self.ffmpeg_save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint_label)
        layout.addWidget(ffmpeg_group)

        # ffmpeg Path is per-machine, not per-repo — load it once here
        # rather than in refresh(), which only runs against whatever repo
        # is currently active.
        self.ffmpeg_edit.setText(video_compress.get_ffmpeg_path(self._api) or "")

        self.refresh()

    def refresh(self) -> None:
        """Re-resolves the active project/repo and updates the
        informational video-folder label — called on construction and
        every time this tab becomes active (SettingsTabSpec.on_activated)."""
        project_id = self._api.local_config.active_project_id
        repo_id = self._api.local_config.active_repo_id
        self._project_id = project_id
        self._repo_id = repo_id

        if not project_id or not repo_id:
            self.hint_label.setText("Select a repo to see this information.")
            return

        video_root = video_path_store.resolve_video_root(self._api, project_id, repo_id)
        self.hint_label.setText(
            f"Playblast videos for this repo are stored locally on this machine, at:\n{video_root}\n"
            "This folder is per-machine only — it is never synced via git or across repos."
        )

    def _on_save_ffmpeg_path(self) -> None:
        ffmpeg_path = self.ffmpeg_edit.text().strip()
        video_compress.set_ffmpeg_path(self._api, ffmpeg_path or None)
        QMessageBox.information(self, "ffmpeg", "ffmpeg Path saved.")
