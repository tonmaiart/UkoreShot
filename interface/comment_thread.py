from __future__ import annotations

import datetime
import uuid

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from interface.shared.widget_helpers import wrap_scrollable
from ukoreshot_plugin.core import comment_store


class _CommentBubble(QFrame):
    """One comment in the thread — author + timestamp, the text, and a
    delete button. No permission check on who can delete what (this app
    has no per-comment ownership/auth model beyond the cached GitHub
    username) — "มีปุ่มลบ comment" was the whole ask, not "only your own"."""

    deleteRequested = Signal(str)  # comment id

    def __init__(self, comment: dict, parent=None):
        super().__init__(parent)
        self.comment_id = comment.get("id", "")
        self.setObjectName("commentBubble")
        self.setFrameShape(QFrame.StyledPanel)

        author_label = QLabel(comment.get("author", ""))
        author_label.setProperty("cardTitle", True)
        timestamp_label = QLabel(comment.get("timestamp", ""))
        timestamp_label.setProperty("secondary", True)
        delete_button = QPushButton("×")
        delete_button.setFixedSize(18, 18)
        delete_button.setToolTip("Delete comment")
        delete_button.clicked.connect(lambda: self.deleteRequested.emit(self.comment_id))

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(author_label)
        header_row.addWidget(timestamp_label, stretch=1)
        header_row.addWidget(delete_button)

        text_label = QLabel(comment.get("text", ""))
        text_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addLayout(header_row)
        layout.addWidget(text_label)


class CommentThread(QWidget):
    """Facebook-style multi-user comment thread for whichever frame is
    currently loaded — any number of comments, each tagged with an author
    (comment_store.current_username()) and timestamp, each individually
    deletable. Added 2026-07-20 per the user's own request ("เหมือนกด
    comment fb"), replacing the single note_edit QTextEdit a frame used to
    get. Only ever embedded by player_widget.py's edit-mode PlayerWidget —
    view mode still doesn't get an editable comment surface, same as
    note_edit's original scope."""

    commentsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._comments: list[dict] = []

        self.thread_container = QWidget()
        self.thread_layout = QVBoxLayout(self.thread_container)
        self.thread_layout.setContentsMargins(0, 0, 0, 0)
        self.thread_layout.setSpacing(4)
        self.thread_layout.addStretch()
        self.thread_scroll = wrap_scrollable(self.thread_container)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Write a comment...")
        self.input_edit.returnPressed.connect(self._on_post_clicked)
        self.post_button = QPushButton("Post")
        self.post_button.clicked.connect(self._on_post_clicked)

        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit, stretch=1)
        input_row.addWidget(self.post_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.thread_scroll, stretch=1)
        layout.addLayout(input_row)

    def set_comments(self, comments: list[dict]) -> None:
        self._comments = list(comments)
        self._rebuild()

    def current_comments(self) -> list[dict]:
        return list(self._comments)

    def _rebuild(self) -> None:
        for i in reversed(range(self.thread_layout.count() - 1)):
            item = self.thread_layout.takeAt(i)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for comment in self._comments:
            bubble = _CommentBubble(comment, parent=self.thread_container)
            bubble.deleteRequested.connect(self._on_delete_requested)
            self.thread_layout.insertWidget(self.thread_layout.count() - 1, bubble)

    def _on_post_clicked(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        self._comments.append(
            {
                "id": uuid.uuid4().hex[:8],
                "author": comment_store.current_username(),
                "text": text,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        self.input_edit.clear()
        self._rebuild()
        self.commentsChanged.emit()

    def _on_delete_requested(self, comment_id: str) -> None:
        self._comments = [c for c in self._comments if c.get("id") != comment_id]
        self._rebuild()
        self.commentsChanged.emit()
