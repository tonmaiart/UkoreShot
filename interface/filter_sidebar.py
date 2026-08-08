from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

_COLUMN_WIDTH = 140
_LIST_HEIGHT = 80

# (category key, display label) — category keys match the dict keys
# video_library_page.py's _collect_filter_values/_video_matches_filters
# use, and video_naming.parse_video_filename's own field names for the
# first five ("sequence" maps to parse_video_filename's "sequence" key,
# etc.) — the last, "commenter", isn't a video_naming field at all, see
# comment_store.list_commenters.
_CATEGORIES = [
    ("sequence", "Sequence"),
    ("shot_code", "Shot Name"),
    ("variation", "Variation"),
    ("index", "Index"),
    ("version", "Version"),
    ("commenter", "Commented By"),
]


class FilterSidebar(QWidget):
    """Library filter panel — one multi-select list per category
    (sequence/shot name/variation/index/version/commenter) plus a
    free-text search box, added 2026-07-20 per the user's own request.
    Selecting multiple values within one category is OR ("this sequence
    OR that one"); selecting across different categories is AND ("this
    sequence AND this variation") — video_library_page.py's
    `_video_matches_filters` implements that combination, this widget
    just exposes the raw selection state (`selected_values`/
    `search_text`) plus a single `filtersChanged` signal so the page
    doesn't need to wire up six separate list widgets' selection-changed
    signals itself. A video that doesn't parse under UkorePlayblast's
    naming convention (a pre-2026-07-20 shot/version-subfoldered
    playblast, left alone per the user's own decision — see that
    plugin's README) shows up as "Unknown" in every video_naming-derived
    category rather than being excluded from filtering entirely.

    Laid out horizontally (changed 2026-08-03 per the user's own
    request — "ตัว filter enum ให้มันเรียงเป็นแนวนอน ขึ้น row ใหม่ ให้อยู่บน
    row sort file") instead of the original fixed-width left sidebar: one
    narrow column per category, packed left-to-right, placed as its own
    row above video_library_page.py's controls_row (the sort/view-mode
    buttons). No longer a scroll area — six ~140px columns comfortably
    fit the library panel's full width, which used to be shared with this
    widget's own left-hand sidebar strip."""

    filtersChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search...")
        self.search_edit.textChanged.connect(self.filtersChanged)

        categories_row = QHBoxLayout()
        categories_row.setContentsMargins(0, 0, 0, 0)

        self._lists: dict[str, QListWidget] = {}
        for key, label in _CATEGORIES:
            column = QWidget()
            column.setFixedWidth(_COLUMN_WIDTH)
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            section_label = QLabel(label)
            section_label.setProperty("secondary", True)
            list_widget = QListWidget()
            list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
            list_widget.setFixedHeight(_LIST_HEIGHT)
            list_widget.itemSelectionChanged.connect(self.filtersChanged)
            column_layout.addWidget(section_label)
            column_layout.addWidget(list_widget)
            categories_row.addWidget(column)
            self._lists[key] = list_widget
        categories_row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search_edit)
        layout.addLayout(categories_row)

    def set_available_values(self, values_by_category: dict) -> None:
        """Rebuilds every category's list items from scratch (called from
        video_library_page.py's `_reload_videos` after every rescan) —
        preserves selection for values that still exist, so re-scanning
        after Refresh doesn't silently clear an active filter, but drops
        selection for a value that's disappeared (e.g. the last video
        with that variation was deleted)."""
        for key, list_widget in self._lists.items():
            values = values_by_category.get(key, [])
            previously_selected = {item.text() for item in list_widget.selectedItems()}
            list_widget.blockSignals(True)
            list_widget.clear()
            for value in values:
                item = QListWidgetItem(value)
                list_widget.addItem(item)
                if value in previously_selected:
                    item.setSelected(True)
            list_widget.blockSignals(False)

    def search_text(self) -> str:
        return self.search_edit.text().strip().lower()

    def selected_values(self, category: str) -> set:
        list_widget = self._lists.get(category)
        if list_widget is None:
            return set()
        return {item.text() for item in list_widget.selectedItems()}
