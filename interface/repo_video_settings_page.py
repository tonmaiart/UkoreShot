from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ukoreshot_plugin.core import discord_client, discord_token_store, video_path_store


class RepoVideoSettingsPage(QWidget):
    """Repository Setting > UkoreShot — lets a studio admin pick which of
    the active repo's own declared Custom Paths (Repository Setting >
    Custom Paths > Create Input Path) UkoreShot treats as this repo's
    playblast video library root. Confirmed with the user: picks from the
    repo's OWN Custom Paths catalog, not a "Connect Input Path" connection
    to a different repo (that's what RigPublisher/ModelPublisher/
    AnimationPublisher's own Repo Studio Setting tabs do instead — see
    plugins/repo_internal/RigPublisher/settings_page.py) — playblasts stay inside
    the same repo they were shot in.

    Same self-resolving-active-repo `refresh()` pattern every CATEGORY_REPO
    tab in this app uses (e.g. RigPublisherSettingsPage), and the same
    "auto-use if there's only one, list + let admin pick if more than one"
    UX — see video_path_store.resolve_video_root for the matching
    resolution order."""

    def __init__(self, parent=None, *, api):
        super().__init__(parent)
        self._api = api
        self._project_id: str | None = None
        self._repo_id: str | None = None
        self._custom_paths: list[dict] = []

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.clear_button = QPushButton("Clear Choice")
        self.clear_button.clicked.connect(self._on_clear)

        # Discord — Channel ID is per-repo, shared/git-tracked (not a
        # secret, see core/discord_client.py). Bot Token is per-machine,
        # saved via the OS keyring (core/discord_token_store.py) — never
        # displayed back once saved, and never tied to which repo is
        # active, so its widgets stay enabled regardless of repo selection.
        self.discord_channel_edit = QLineEdit()
        self.discord_channel_save_button = QPushButton("Save Channel ID")
        self.discord_channel_save_button.clicked.connect(self._on_save_discord_channel)
        self.discord_token_edit = QLineEdit()
        self.discord_token_edit.setEchoMode(QLineEdit.Password)
        self.discord_token_edit.setPlaceholderText("Paste a new token to change it")
        self.discord_token_save_button = QPushButton("Save Bot Token")
        self.discord_token_save_button.clicked.connect(self._on_save_discord_token)
        self.discord_token_status_label = QLabel("")

        discord_note = QLabel(
            "Channel ID applies to this repo and is shared with the whole studio. "
            "Bot Token is saved only on this machine — every machine that should be able "
            "to use Send to Discord needs the same token entered here once."
        )
        discord_note.setWordWrap(True)
        discord_group = QGroupBox("Discord")
        discord_layout = QFormLayout(discord_group)
        discord_layout.addRow(discord_note)
        discord_layout.addRow("Channel ID", self.discord_channel_edit)
        discord_layout.addRow(self.discord_channel_save_button)
        discord_layout.addRow("Bot Token", self.discord_token_edit)
        discord_layout.addRow(self.discord_token_save_button)
        discord_layout.addRow(self.discord_token_status_label)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.clear_button)
        layout.addWidget(discord_group)

        self._update_discord_token_status()
        self.refresh()

    def refresh(self) -> None:
        """Re-resolves the active project/repo and rebuilds the list of
        Custom Path choices — called on construction and every time this
        tab becomes active (SettingsTabSpec.on_activated)."""
        project_id = self._api.local_config.active_project_id
        repo_id = self._api.local_config.active_repo_id
        self._project_id = project_id
        self._repo_id = repo_id
        self.list_widget.clear()
        self._refresh_discord_channel()

        if not project_id or not repo_id:
            self.hint_label.setText("Select a repo to see this information.")
            self.list_widget.setEnabled(False)
            self.clear_button.setEnabled(False)
            return

        self._custom_paths = video_path_store.get_custom_paths(self._api, project_id, repo_id)

        if not self._custom_paths:
            self.hint_label.setText(
                "This repo has no Custom Paths declared yet — UkoreShot has nowhere to look for videos. "
                "Add one under Repository Setting > Custom Paths > Create Input Path first."
            )
            self.list_widget.setEnabled(False)
            self.clear_button.setEnabled(False)
            return

        self.list_widget.setEnabled(True)
        self.clear_button.setEnabled(True)
        chosen_id = video_path_store.get_selected_custom_path_id(self._api, project_id, repo_id)
        for index, custom_path in enumerate(self._custom_paths):
            self.list_widget.addItem(QListWidgetItem(f"{custom_path['label']}  ({custom_path['path']})"))
            if custom_path.get("id") == chosen_id:
                self.list_widget.setCurrentRow(index)

        if len(self._custom_paths) == 1:
            self.hint_label.setText(
                "Only one Custom Path declared — UkoreShot uses it automatically, no choice needed."
            )
        else:
            self.hint_label.setText(
                "This repo has multiple Custom Paths declared — pick which one is the playblast video library."
            )

    def _on_selection_changed(self) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._custom_paths)):
            return
        video_path_store.set_selected_custom_path_id(
            self._api, self._project_id, self._repo_id, self._custom_paths[row]["id"]
        )

    def _on_clear(self) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        video_path_store.set_selected_custom_path_id(self._api, self._project_id, self._repo_id, None)
        self.list_widget.clearSelection()

    def _refresh_discord_channel(self) -> None:
        has_repo = self._project_id is not None and self._repo_id is not None
        self.discord_channel_edit.setEnabled(has_repo)
        self.discord_channel_save_button.setEnabled(has_repo)
        if has_repo:
            channel_id = discord_client.get_channel_id(self._api, self._project_id, self._repo_id)
            self.discord_channel_edit.setText(channel_id or "")
        else:
            self.discord_channel_edit.clear()

    def _on_save_discord_channel(self) -> None:
        if self._project_id is None or self._repo_id is None:
            return
        channel_id = self.discord_channel_edit.text().strip()
        discord_client.set_channel_id(self._api, self._project_id, self._repo_id, channel_id or None)
        QMessageBox.information(self, "Discord", "Channel ID saved.")

    def _on_save_discord_token(self) -> None:
        token = self.discord_token_edit.text().strip()
        if not token:
            return
        try:
            discord_token_store.save_token(token)
        except discord_token_store.DiscordTokenStoreFallbackUsed as exc:
            QMessageBox.warning(self, "Discord Bot Token", str(exc))
        else:
            QMessageBox.information(self, "Discord Bot Token", "Bot token saved on this machine.")
        self.discord_token_edit.clear()
        self._update_discord_token_status()

    def _update_discord_token_status(self) -> None:
        has_token = discord_token_store.load_token() is not None
        self.discord_token_status_label.setText(
            "Bot token: saved on this machine." if has_token else "Bot token: not set on this machine."
        )
