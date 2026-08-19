from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ukoreshot_plugin.core import discord_client, video_path_store


class RepoVideoSettingsPage(QWidget):
    """Repository Setting > UkoreShot. As of the cache_dir-based video
    storage change, the playblast library root is no longer something a
    studio admin picks (no more Custom Path list) — it's a fixed
    per-machine folder under UkoreHub's own cache_dir, keyed by
    project/repo, so `hint_label` here is purely informational (shows
    where this machine's videos for the active repo actually live) rather
    than a picker. See video_path_store.resolve_video_root.

    Same self-resolving-active-repo `refresh()` pattern every CATEGORY_REPO
    tab in this app uses (e.g. RigPublisherSettingsPage)."""

    def __init__(self, parent=None, *, api):
        super().__init__(parent)
        self._api = api
        self._project_id: str | None = None
        self._repo_id: str | None = None

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)

        # Discord — Channel ID and Bot Token are both per-repo and both
        # shared/git-tracked (same "ukore_shot" PluginConfigStore,
        # shared=True) as of 2026-08-08, so every machine picks up both
        # automatically via git with no per-machine setup. Confirmed with
        # the user this trades away the OS-keyring approach an earlier
        # revision used: the token is committed to the studio repo in
        # plain text and stays in git history forever, so repo access is
        # effectively bot access — see discord_client.py's
        # get_bot_token/set_bot_token docstring.
        self.discord_channel_edit = QLineEdit()
        self.discord_channel_edit.setPlaceholderText("A Forum Channel's id, not a plain text channel")
        self.discord_channel_save_button = QPushButton("Save Channel ID")
        self.discord_channel_save_button.clicked.connect(self._on_save_discord_channel)
        self.discord_token_edit = QLineEdit()
        self.discord_token_edit.setEchoMode(QLineEdit.Password)
        self.discord_token_save_button = QPushButton("Save Bot Token")
        self.discord_token_save_button.clicked.connect(self._on_save_discord_token)

        # Max Upload Size — per-repo, shared, same store as Channel ID/Bot
        # Token above. discord_send_worker.py compresses a video down to
        # fit under this via ffmpeg before uploading, only when it's
        # actually over the limit (no transcode at all otherwise). Default
        # matches Discord's own un-boosted cap — raise it if the studio's
        # server is boosted enough to allow bigger uploads.
        self.discord_max_upload_spin = QSpinBox()
        self.discord_max_upload_spin.setRange(1, 500)
        self.discord_max_upload_spin.setSuffix(" MB")
        self.discord_max_upload_save_button = QPushButton("Save Max Upload Size")
        self.discord_max_upload_save_button.clicked.connect(self._on_save_discord_max_upload)

        # ffmpeg Path — per-machine (shared=False), unlike everything else
        # in this group: it's a real local file path, not something that
        # makes sense synced studio-wide (every machine's ffmpeg install,
        # if any, lives wherever it lives). Blank means "look up ffmpeg on
        # this machine's PATH" — see video_compress.py's resolve_ffmpeg_path.
        self.discord_ffmpeg_edit = QLineEdit()
        self.discord_ffmpeg_edit.setPlaceholderText("Blank = look up ffmpeg on this machine's PATH")
        self.discord_ffmpeg_save_button = QPushButton("Save ffmpeg Path (this machine)")
        self.discord_ffmpeg_save_button.clicked.connect(self._on_save_discord_ffmpeg_path)

        discord_note = QLabel(
            "Channel ID must be a Forum Channel — Send to Discord posts each video into the forum "
            "post whose title matches the video's shot code (creating one if it doesn't exist yet), "
            "not directly into the channel itself. A video over Max Upload Size is compressed with "
            "ffmpeg before sending (requires ffmpeg installed on whichever machine clicks Send to "
            "Discord). Channel ID, Bot Token, and Max Upload Size all apply to this repo and are "
            "shared with the whole studio via git — anyone with access to this repo (now or in its "
            "history) can see the Bot Token and use it, so only use a bot you're fine with the whole "
            "studio effectively controlling."
        )
        discord_note.setWordWrap(True)
        discord_group = QGroupBox("Discord")
        discord_layout = QFormLayout(discord_group)
        discord_layout.addRow(discord_note)
        discord_layout.addRow("Forum Channel ID", self.discord_channel_edit)
        discord_layout.addRow(self.discord_channel_save_button)
        discord_layout.addRow("Bot Token", self.discord_token_edit)
        discord_layout.addRow(self.discord_token_save_button)
        discord_layout.addRow("Max Upload Size", self.discord_max_upload_spin)
        discord_layout.addRow(self.discord_max_upload_save_button)
        discord_layout.addRow("ffmpeg Path", self.discord_ffmpeg_edit)
        discord_layout.addRow(self.discord_ffmpeg_save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint_label)
        layout.addWidget(discord_group)

        # ffmpeg Path is per-machine, not per-repo — load it once here
        # rather than in refresh()/_refresh_discord_fields, which both
        # only run against whatever repo is currently active.
        self.discord_ffmpeg_edit.setText(discord_client.get_ffmpeg_path(self._api) or "")

        self.refresh()

    def refresh(self) -> None:
        """Re-resolves the active project/repo and updates the
        informational video-folder label — called on construction and
        every time this tab becomes active (SettingsTabSpec.on_activated)."""
        project_id = self._api.local_config.active_project_id
        repo_id = self._api.local_config.active_repo_id
        self._project_id = project_id
        self._repo_id = repo_id
        self._refresh_discord_fields()

        if not project_id or not repo_id:
            self.hint_label.setText("Select a repo to see this information.")
            return

        video_root = video_path_store.resolve_video_root(self._api, project_id, repo_id)
        self.hint_label.setText(
            f"Playblast videos for this repo are stored locally on this machine, at:\n{video_root}\n"
            "This folder is per-machine only — it is never synced via git or across repos."
        )

    def _refresh_discord_fields(self) -> None:
        has_repo = self._project_id is not None and self._repo_id is not None
        self.discord_channel_edit.setEnabled(has_repo)
        self.discord_channel_save_button.setEnabled(has_repo)
        self.discord_token_edit.setEnabled(has_repo)
        self.discord_token_save_button.setEnabled(has_repo)
        self.discord_max_upload_spin.setEnabled(has_repo)
        self.discord_max_upload_save_button.setEnabled(has_repo)
        if has_repo:
            channel_id = discord_client.get_channel_id(self._api, self._project_id, self._repo_id)
            self.discord_channel_edit.setText(channel_id or "")
            token = discord_client.get_bot_token(self._api, self._project_id, self._repo_id)
            self.discord_token_edit.setText(token or "")
            self.discord_max_upload_spin.setValue(
                discord_client.get_max_upload_mb(self._api, self._project_id, self._repo_id)
            )
        else:
            self.discord_channel_edit.clear()
            self.discord_token_edit.clear()
            self.discord_max_upload_spin.setValue(discord_client.DEFAULT_MAX_UPLOAD_MB)

    def _on_save_discord_channel(self) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        channel_id = self.discord_channel_edit.text().strip()
        discord_client.set_channel_id(self._api, self._project_id, self._repo_id, channel_id or None)
        QMessageBox.information(self, "Discord", "Channel ID saved.")

    def _on_save_discord_token(self) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        token = self.discord_token_edit.text().strip()
        discord_client.set_bot_token(self._api, self._project_id, self._repo_id, token or None)
        QMessageBox.information(self, "Discord", "Bot Token saved.")

    def _on_save_discord_max_upload(self) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        discord_client.set_max_upload_mb(self._api, self._project_id, self._repo_id, self.discord_max_upload_spin.value())
        QMessageBox.information(self, "Discord", "Max Upload Size saved.")

    def _on_save_discord_ffmpeg_path(self) -> None:
        ffmpeg_path = self.discord_ffmpeg_edit.text().strip()
        discord_client.set_ffmpeg_path(self._api, ffmpeg_path or None)
        QMessageBox.information(self, "Discord", "ffmpeg Path saved.")
