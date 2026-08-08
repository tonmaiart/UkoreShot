from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem

_WIDTH = 120  # widened from 64 2026-08-03 per the user's own request, to fit the avatar icon below
_AVATAR_SIZE = 20
# A small fixed palette for the generated avatars below — deterministic
# per-username (see _avatar_icon), not meant to look like a real design
# system, just enough to tell commenters apart at a glance.
_AVATAR_COLORS = ["#5865f2", "#eb459e", "#57f287", "#fee75c", "#ed4245", "#3ba55d", "#faa61a", "#9b59b6"]


def _avatar_icon(username: str) -> QIcon:
    """A small deterministic colored-circle "avatar" for a commenter's
    username — this app has no real profile pictures, so a generated
    initial badge (the same idea GitHub/Slack fall back to for a user
    without an uploaded photo) is the simple stand-in, added 2026-08-03
    per the user's own request ("การ์ดแสดง icon username"). Deterministic
    via a plain character-sum hash (not Python's built-in hash(), which is
    randomized per-process for str and would give a different color every
    app run) so a given username always gets the same color."""
    initial = (username or "?")[0].upper()
    color_index = sum(ord(c) for c in username) % len(_AVATAR_COLORS) if username else 0
    color = QColor(_AVATAR_COLORS[color_index])
    pixmap = QPixmap(_AVATAR_SIZE, _AVATAR_SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(0, 0, _AVATAR_SIZE, _AVATAR_SIZE)
    painter.setPen(Qt.white)
    font = QFont()
    font.setBold(True)
    font.setPointSize(9)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, initial)
    painter.end()
    return QIcon(pixmap)


def _first_commenter(entry: dict) -> str:
    """The author to show as this row's avatar — the first (oldest)
    comment's author, if any. A legacy pre-2026-07-20 single "note" string
    (see player_widget.py's _migrate_comments) has no author on file, so a
    frame with only a legacy note gets no avatar rather than a guessed
    one."""
    comments = entry.get("comments")
    if comments:
        return comments[0].get("author", "")
    return ""


class CommentSidebar(QListWidget):
    """Right-hand sidebar — used by PlayerWidget in both show_edit_tools
    modes — listing every frame with a saved comment, top-to-bottom by
    frame index. A plain QListWidget matching
    interface/settings/settings_view.py's SettingsView.tab_list style
    (fixed-width, flat rows, click-or-drag-through to select) rather than
    a card grid — the user's own 2026-07-20 request. Each row shows the
    frame number plus, since 2026-08-03, a small generated avatar icon for
    whoever left the first comment on that frame (see _avatar_icon) —
    widened from the original 64px (_WIDTH) to fit both. currentRowChanged
    (not itemClicked) is what drives frameSelected, so pressing on one row
    and dragging across others scrubs through commented frames live, the
    same native QListWidget click-drag-select behavior Settings' tab_list
    gets for free."""

    frameSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(_WIDTH)
        self._suppress_selection_signal = False
        self.currentRowChanged.connect(self._on_row_changed)

    def set_frames(self, frames: dict) -> None:
        """frames is PlayerWidget._frames["frames"] — {"<index>": {...}}.
        Rebuilds every row from scratch — called whenever the underlying
        comment data changes (video load/clear, a stroke/note/text box
        saved), not on every frame navigation."""
        self._suppress_selection_signal = True
        self.clear()
        indices = sorted(int(key) for key, entry in frames.items() if entry)
        for index in indices:
            item = QListWidgetItem(str(index))
            item.setData(Qt.UserRole, index)
            item.setTextAlignment(Qt.AlignCenter)
            author = _first_commenter(frames[str(index)])
            if author:
                item.setIcon(_avatar_icon(author))
            self.addItem(item)
        self._suppress_selection_signal = False

    def set_current_frame(self, frame_index: int) -> None:
        """Highlights whichever row matches the frame currently playing,
        without emitting frameSelected — that signal is only for the user
        actively picking a row, not PlayerWidget echoing its own state
        back in."""
        self._suppress_selection_signal = True
        for row in range(self.count()):
            if self.item(row).data(Qt.UserRole) == frame_index:
                self.setCurrentRow(row)
                self._suppress_selection_signal = False
                return
        self.setCurrentRow(-1)
        self._suppress_selection_signal = False

    def _on_row_changed(self, row: int) -> None:
        if self._suppress_selection_signal or row < 0:
            return
        item = self.item(row)
        if item is not None:
            self.frameSelected.emit(item.data(Qt.UserRole))
