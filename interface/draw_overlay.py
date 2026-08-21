from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

# The app's own top-level core/ package, NOT this plugin's sibling
# ukoreshot_plugin.core package (same name, different package, resolved
# unambiguously since this is an absolute import) — see ../core/README.md's
# naming note. Revived 2026-08-20 from git history (commit f9b3505 in
# UkoreHubDev, before this plugin's draw/comment editor was extracted to
# BananaSketch on 2026-08-08) — see ../README.md for the full story.
from core.extensibility import debug_log

_DEBUG_SOURCE = "UkoreShot.Draw"
debug_log.register_source(_DEBUG_SOURCE)

_UNDO_STACK_LIMIT = 20
_MIN_TOOL_SIZE = 1
_MAX_TOOL_SIZE = 60
_RESIZE_PIXELS_PER_UNIT = 4  # mouse-drag sensitivity for the "F" resize gesture
_TEXT_BOX_WIDTH = 160
_TEXT_BOX_HEIGHT = 80
_DEFAULT_TEXT_BOX_POS = (0.1, 0.1)  # fallback only, for a saved text box missing x/y
_DEFAULT_BG_COLOR = "#2b2d31"
_DEFAULT_BG_OPACITY = 220
_SELECT_HIT_RADIUS_PX = 8  # minimum pixel hit-test radius for selecting a thin stroke via the Select tool
_SELECTION_COLOR = "#5865f2"

TOOL_BRUSH = "brush"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_SELECT = "select"


@dataclass
class Stroke:
    color: str  # "#rrggbb"
    width: int
    points: list[tuple[float, float]] = field(default_factory=list)  # normalized 0-1 widget space

    def to_dict(self) -> dict:
        return {"color": self.color, "width": self.width, "points": [list(p) for p in self.points]}

    @classmethod
    def from_dict(cls, data: dict) -> "Stroke":
        return cls(
            color=data.get("color", "#ff3b30"),
            width=int(data.get("width", 4)),
            points=[tuple(p) for p in data.get("points", [])],
        )


class _TextBoxItem(QFrame):
    """A draggable-only-via-Select-tool, editable text annotation pinned
    to a frame — however many an Animator wants (DrawOverlay._add_text_box,
    one per Text-tool click on empty canvas; clicking an *existing* box
    with the Text tool active just edits it in place instead of creating a
    new one, since a real QPlainTextEdit already gives that for free once
    it's the thing actually receiving the click — see set_mode).
    Redesigned 2026-08-03 per the user's own request: the old
    always-visible drag handle ("⋮⋮") and delete "×" button are gone —
    moving is Select-tool only (the whole box, click-and-drag, not a
    dedicated handle), and deleting is the Delete/Backspace key while
    selected, matching how the Select tool now also selects/moves/deletes
    strokes (DrawOverlay's own _selected_stroke). A small style
    mini-toolbar (bg color/opacity, Bold, Stroke) only appears while
    selected, so plain viewing/typing isn't cluttered with controls the
    rest of the time. `norm_pos` (top-left corner, 0-1) is the same
    widget-space normalization Stroke.points already uses, so a box tracks
    correctly if the player is resized (see DrawOverlay.resizeEvent)."""

    changed = Signal()
    deleteRequested = Signal(object)
    selected = Signal(object)

    def __init__(
        self,
        text: str,
        norm_pos: tuple[float, float],
        parent=None,
        *,
        bg_color: str = _DEFAULT_BG_COLOR,
        bg_opacity: int = _DEFAULT_BG_OPACITY,
        stroke: bool = False,
        bold: bool = False,
    ):
        super().__init__(parent)
        self.norm_pos = norm_pos
        self.setObjectName("textBoxItem")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(_TEXT_BOX_WIDTH, _TEXT_BOX_HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)

        self._mode = TOOL_TEXT
        self._is_selected = False
        self._drag_start: QPoint | None = None
        self.bg_color = QColor(bg_color)
        self.bg_opacity = bg_opacity
        self.stroke_enabled = stroke
        self.bold = bold

        self.text_edit = QPlainTextEdit(text)
        self.text_edit.setFrameShape(QFrame.NoFrame)
        self.text_edit.textChanged.connect(self.changed.emit)

        # Style mini-toolbar — only shown while selected (set_selected),
        # not always-on chrome like the old handle/delete-button row.
        self.color_button = QPushButton()
        self.color_button.setFixedSize(16, 16)
        self.color_button.setToolTip("Background color")
        self.color_button.clicked.connect(self._on_pick_color)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setMaximumWidth(44)
        self.opacity_slider.setToolTip("Background opacity")
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.bold_button = QPushButton("B")
        self.bold_button.setCheckable(True)
        self.bold_button.setFixedSize(18, 18)
        self.bold_button.setToolTip("Bold")
        self.bold_button.toggled.connect(self._on_bold_toggled)
        self.stroke_button = QPushButton("S")
        self.stroke_button.setCheckable(True)
        self.stroke_button.setFixedSize(18, 18)
        self.stroke_button.setToolTip("Stroke outline")
        self.stroke_button.toggled.connect(self._on_stroke_toggled)

        style_row = QHBoxLayout()
        style_row.setContentsMargins(0, 0, 0, 0)
        style_row.setSpacing(2)
        style_row.addWidget(self.color_button)
        style_row.addWidget(self.opacity_slider, stretch=1)
        style_row.addWidget(self.bold_button)
        style_row.addWidget(self.stroke_button)
        self.style_row_widget = QWidget()
        self.style_row_widget.setLayout(style_row)
        self.style_row_widget.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self.style_row_widget)
        layout.addWidget(self.text_edit, stretch=1)

        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(self.bg_opacity)
        self.opacity_slider.blockSignals(False)
        self.bold_button.blockSignals(True)
        self.bold_button.setChecked(self.bold)
        self.bold_button.blockSignals(False)
        self.stroke_button.blockSignals(True)
        self.stroke_button.setChecked(self.stroke_enabled)
        self.stroke_button.blockSignals(False)
        self._apply_style()

    def text(self) -> str:
        return self.text_edit.toPlainText()

    # -- mode (driven by DrawOverlay.set_tool) ---------------------------

    def set_mode(self, tool: str) -> None:
        """TOOL_TEXT: text_edit itself is interactive (click to place a
        cursor and type — that IS "rename", no separate rename action
        needed since the click landed on a real text field). TOOL_SELECT:
        text_edit goes mouse-transparent so a click anywhere on the box
        reaches this QFrame instead, for select+drag — see
        mousePressEvent. Any other tool (Brush/Eraser): neither is
        interactive, so a brush/eraser click over a text box's area
        doesn't accidentally land in the text field instead of drawing."""
        self._mode = tool
        self.text_edit.setReadOnly(tool != TOOL_TEXT)
        self.text_edit.setAttribute(Qt.WA_TransparentForMouseEvents, tool != TOOL_TEXT)
        if tool != TOOL_SELECT:
            self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        self.style_row_widget.setVisible(selected)
        self._apply_style()

    # -- select-tool drag ---------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._mode == TOOL_SELECT and event.button() == Qt.LeftButton:
            self.selected.emit(self)
            self.set_selected(True)
            self.setFocus()
            self._drag_start = event.globalPosition().toPoint() - self.pos()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_start
            container = self.parentWidget()
            if container is not None:
                max_x = max(0, container.width() - self.width())
                max_y = max(0, container.height() - self.height())
                new_pos.setX(min(max(0, new_pos.x()), max_x))
                new_pos.setY(min(max(0, new_pos.y()), max_y))
            self.move(new_pos)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is not None and event.button() == Qt.LeftButton:
            self._drag_start = None
            self.changed.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._is_selected and event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.deleteRequested.emit(self)
            return
        super().keyPressEvent(event)

    # -- style controls (only reachable while selected) --------------------

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(self.bg_color, self, "Background Color")
        if color.isValid():
            self.bg_color = color
            self._apply_style()
            self.changed.emit()

    def _on_opacity_changed(self, value: int) -> None:
        self.bg_opacity = value
        self._apply_style()
        self.changed.emit()

    def _on_bold_toggled(self, checked: bool) -> None:
        self.bold = checked
        self._apply_style()
        self.changed.emit()

    def _on_stroke_toggled(self, checked: bool) -> None:
        self.stroke_enabled = checked
        self._apply_style()
        self.changed.emit()

    def _apply_style(self) -> None:
        """"มี bg ทึบโง่ๆ ที่ปรับสี ปรับความทึบ ปรับ stroke, bold ได้" — a
        deliberately simple/"dumb" solid background (no gradients or
        shadows), color+opacity adjustable. "Stroke" is implemented as a
        colored box border rather than true per-glyph text outline (which
        QPlainTextEdit has no support for) — matches the same "แบบโง่"
        simplicity the rest of this feature already commits to. The
        selected-state border (accent color, thicker) doubles as this
        box's own selection highlight frame, same idea as
        DrawOverlay._paint_stroke_selection for a selected stroke."""
        fill = QColor(self.bg_color)
        fill.setAlpha(self.bg_opacity)
        if self._is_selected:
            border = f"2px solid {_SELECTION_COLOR}"
        elif self.stroke_enabled:
            border = "2px solid " + QColor(self.bg_color).darker(150).name()
        else:
            border = "1px solid #444444"
        self.color_button.setStyleSheet(f"background-color: {self.bg_color.name()}; border-radius: 2px;")
        self.setStyleSheet(
            "QFrame#textBoxItem { "
            f"background-color: rgba({fill.red()}, {fill.green()}, {fill.blue()}, {fill.alpha()}); "
            f"border: {border}; border-radius: 4px; }}"
        )
        font = self.text_edit.font()
        font.setBold(self.bold)
        self.text_edit.setFont(font)


class DrawOverlay(QWidget):
    """Transparent freehand-drawing canvas stacked on top of the video
    surface (see player_widget.py's _VideoStack) — one stroke list per
    frame index, deliberately simple ("แบบโง่", per the user's own
    description of the feature): fixed-shape freehand pen only, no vector
    editing after the fact beyond a whole-stroke eraser, a Select tool
    (see below) to move/delete an existing stroke or text box, and a
    snapshot-based per-frame undo/redo stack pair (undo() pushes the
    current state onto _redo_stack before popping _undo_stack, and vice
    versa for redo(); any new action that mutates _strokes — a new stroke,
    clear_frame, a Select-tool move, a Select-tool delete — clears
    _redo_stack via _push_undo, the same "new edit invalidates redo
    history" rule every undo/redo system uses). Undo/redo history is NOT
    persisted — only the final stroke list for a frame is ever saved (see
    comment_store.py) — and resets whenever a different frame is loaded,
    since undo/redo only ever makes sense within one frame's own editing
    session. Strokes are only ever shown for the exact frame index
    currently loaded (see load_frame) so scrubbing between frames doesn't
    smear one frame's drawing onto another. Text boxes are not part of
    this undo/redo stack — only strokes are (matches the original undo
    feature's scope, not expanded here) — so a Select-tool text-box move
    isn't undoable, only a stroke move is.

    Exactly one of TOOL_BRUSH/TOOL_ERASER/TOOL_TEXT/TOOL_SELECT is active
    at a time (set_tool) — added TOOL_SELECT 2026-08-03 per the user's own
    request specifically for moving/deleting an existing stroke or text
    box, since Text-tool clicks are reserved for
    creating-or-editing-in-place a text box (see _TextBoxItem.set_mode)
    and Brush/Eraser clicks are reserved for drawing. Selecting (Select
    tool only) shows a highlight — a dashed bounding box around a
    selected stroke (_paint_stroke_selection), or _TextBoxItem's own
    accent border for a selected text box — and Delete/Backspace removes
    whichever is currently selected.

    _TextBoxItem children ride along on the same per-frame swap (load_frame
    tears down and rebuilds them same as strokes) but are real interactive
    QWidgets, not painted like strokes — see _TextBoxItem above."""

    strokesChanged = Signal()
    textBoxesChanged = Signal()
    # Emitted only when the "F" resize gesture (below) changes the size —
    # NOT when set_brush_width() is called from the toolbar's Size slider,
    # which would just be an echo of what the slider already knows. Lets
    # PlayerWidget keep that slider's displayed value in sync when a size
    # change instead comes from the keyboard gesture.
    toolSizeChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Belt-and-suspenders: player_widget.py's _VideoSurface (2026-07-20)
        # already isn't a native window, so this shouldn't be load-bearing
        # anymore — but it was the first (insufficient on its own) attempt
        # at fixing "brush doesn't respond to mouse input at all" when the
        # sibling below this overlay was still QVideoWidget (a real native
        # window, which OS-level Z-ordering/hit-testing always won
        # regardless of any attribute set here). Left on since it's
        # harmless and still correct in spirit for a translucent overlay.
        self.setAttribute(Qt.WA_AlwaysStackOnTop)
        self.setMouseTracking(True)  # so mouseMoveEvent fires without a button held, for the hover-size indicator
        # StrongFocus + grabbing focus on hover (enterEvent below) so the
        # "F" resize gesture works as soon as the cursor is over the
        # canvas, without needing an explicit click first — mirrors how a
        # 3D viewport typically reacts to hover + a single keypress.
        self.setFocusPolicy(Qt.StrongFocus)
        self._strokes: list[Stroke] = []
        self._undo_stack: list[list[Stroke]] = []
        self._redo_stack: list[list[Stroke]] = []
        self._active_points: list[tuple[float, float]] = []
        self._color = "#ff3b30"
        # One "tool size" (pixel radius/width) shared by both the brush
        # (stroke width) and the eraser (hit-test radius) — the "F"
        # resize gesture and the toolbar's Size slider both just adjust
        # this one value, whichever tool happens to be active.
        self._brush_width = 4
        # Exactly one of TOOL_BRUSH/TOOL_ERASER/TOOL_TEXT/TOOL_SELECT at a
        # time — the single source of truth every mouse handler branches
        # on, so tools never act simultaneously (fixed 2026-07-20: Text
        # used to be a one-shot action that left Brush's drawing active
        # underneath, so repositioning a text box could also draw a
        # stroke at the same time).
        self._tool = TOOL_BRUSH
        self._drawing_enabled = True
        self._text_boxes: list[_TextBoxItem] = []
        self._hover_pos: QPointF | None = None
        self._resizing = False
        self._resize_start_pos: QPointF | None = None
        self._resize_start_value: int | None = None
        # Select-tool state — a stroke is selected by object identity (the
        # exact Stroke instance clicked), not an index, since the list can
        # be mutated (undo/redo/erase) while a selection is live.
        self._selected_stroke: Stroke | None = None
        self._stroke_drag_last_point: tuple[float, float] | None = None
        self._stroke_move_dirty = False  # True once a drag has actually moved the selected stroke this press
        self._selected_text_box: _TextBoxItem | None = None

    # -- toolbar-facing API -------------------------------------------------

    def set_color(self, color: QColor) -> None:
        self._color = color.name()

    def set_brush_width(self, width: int) -> None:
        self._brush_width = max(_MIN_TOOL_SIZE, min(_MAX_TOOL_SIZE, width))
        self.update()

    def set_tool(self, tool: str) -> None:
        """tool is one of TOOL_BRUSH/TOOL_ERASER/TOOL_TEXT/TOOL_SELECT —
        exclusive, called from whichever toolbox button just became
        checked (see player_widget.py). Abandons any in-progress stroke so
        switching tools mid-drag can't leave a half-drawn stroke dangling,
        and leaving TOOL_SELECT clears whatever was selected (a stroke or
        a text box) so the highlight doesn't linger under a different
        tool. Propagates the new tool to every text box so each one's own
        interactivity (see _TextBoxItem.set_mode) stays in sync."""
        self._tool = tool
        self._active_points = []
        if tool != TOOL_SELECT:
            self._deselect_stroke()
            self._deselect_text_box()
        for box in self._text_boxes:
            box.set_mode(tool)
        self.update()

    def set_drawing_enabled(self, enabled: bool) -> None:
        """Drawing only makes sense while playback is paused on one exact
        frame (see PlayerWidget._set_paused_state) — disabled during
        playback so a stray click while the video is moving doesn't start
        a stroke against a frame index that's about to change."""
        self._drawing_enabled = enabled

    def load_frame(self, strokes: list[Stroke], text_boxes: list[dict] | None = None) -> None:
        """Swaps in the persisted stroke list and text boxes for whichever
        frame index is now current."""
        self._strokes = list(strokes)
        self._undo_stack = []
        self._redo_stack = []
        self._active_points = []
        self._selected_stroke = None
        self._stroke_drag_last_point = None
        self._stroke_move_dirty = False
        for box in self._text_boxes:
            box.setParent(None)
            box.deleteLater()
        self._text_boxes = []
        self._selected_text_box = None
        for data in text_boxes or []:
            pos = (data.get("x", _DEFAULT_TEXT_BOX_POS[0]), data.get("y", _DEFAULT_TEXT_BOX_POS[1]))
            self._add_text_box(
                data.get("text", ""),
                pos,
                bg_color=data.get("bg_color", _DEFAULT_BG_COLOR),
                bg_opacity=data.get("bg_opacity", _DEFAULT_BG_OPACITY),
                stroke=data.get("stroke", False),
                bold=data.get("bold", False),
            )
        self.update()

    def current_strokes(self) -> list[Stroke]:
        return list(self._strokes)

    def clear_frame(self) -> None:
        if not self._strokes:
            return
        self._push_undo()
        self._strokes = []
        self._deselect_stroke()
        self.update()
        self.strokesChanged.emit()

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(list(self._strokes))
        self._strokes = self._undo_stack.pop()
        self._deselect_stroke()
        self.update()
        self.strokesChanged.emit()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(list(self._strokes))
        self._strokes = self._redo_stack.pop()
        self._deselect_stroke()
        self.update()
        self.strokesChanged.emit()

    # -- text boxes -------------------------------------------------------

    def _add_text_box(
        self,
        text: str,
        norm_pos: tuple[float, float],
        *,
        bg_color: str = _DEFAULT_BG_COLOR,
        bg_opacity: int = _DEFAULT_BG_OPACITY,
        stroke: bool = False,
        bold: bool = False,
    ) -> None:
        box = _TextBoxItem(
            text, norm_pos, parent=self, bg_color=bg_color, bg_opacity=bg_opacity, stroke=stroke, bold=bold
        )
        box.changed.connect(lambda b=box: self._on_text_box_changed(b))
        box.deleteRequested.connect(self._remove_text_box)
        box.selected.connect(self._on_text_box_selected)
        box.set_mode(self._tool)
        box.move(round(norm_pos[0] * self.width()), round(norm_pos[1] * self.height()))
        box.show()
        box.raise_()
        self._text_boxes.append(box)

    def _remove_text_box(self, box: "_TextBoxItem") -> None:
        if box in self._text_boxes:
            self._text_boxes.remove(box)
        if self._selected_text_box is box:
            self._selected_text_box = None
        box.setParent(None)
        box.deleteLater()
        self.textBoxesChanged.emit()

    def _on_text_box_changed(self, box: "_TextBoxItem") -> None:
        box.norm_pos = self._normalized(QPointF(box.pos()))
        self.textBoxesChanged.emit()

    def _on_text_box_selected(self, box: "_TextBoxItem") -> None:
        """A text box selected itself (see _TextBoxItem.mousePressEvent,
        Select-tool only) — enforce "only one selected item at a time"
        across both text boxes and strokes."""
        if self._selected_text_box is not None and self._selected_text_box is not box:
            self._selected_text_box.set_selected(False)
        self._selected_text_box = box
        self._deselect_stroke()
        self.update()

    def current_text_boxes(self) -> list[dict]:
        return [
            {
                "text": b.text(),
                "x": b.norm_pos[0],
                "y": b.norm_pos[1],
                "bg_color": b.bg_color.name(),
                "bg_opacity": b.bg_opacity,
                "stroke": b.stroke_enabled,
                "bold": b.bold,
            }
            for b in self._text_boxes
        ]

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        debug_log.log(_DEBUG_SOURCE, f"resizeEvent, new size={self.size()}, visible={self.isVisible()}")
        for box in self._text_boxes:
            box.move(round(box.norm_pos[0] * self.width()), round(box.norm_pos[1] * self.height()))

    # -- "F" resize gesture -------------------------------------------------

    def enterEvent(self, event) -> None:
        self.setFocus()
        super().enterEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._selected_stroke is not None:
            self._push_undo()
            self._strokes.remove(self._selected_stroke)
            self._deselect_stroke()
            self.update()
            self.strokesChanged.emit()
            return
        if (
            event.key() == Qt.Key_F
            and self._drawing_enabled
            and self._hover_pos is not None
            and self._tool not in (TOOL_TEXT, TOOL_SELECT)
        ):
            if self._resizing:
                self._end_resize(commit=True)
            else:
                self._start_resize()
            return
        if event.key() == Qt.Key_Escape and self._resizing:
            self._end_resize(commit=False)
            return
        super().keyPressEvent(event)

    def _start_resize(self) -> None:
        self._resizing = True
        self._resize_start_pos = self._hover_pos
        self._resize_start_value = self._brush_width
        debug_log.log(_DEBUG_SOURCE, f"resize gesture started at size={self._brush_width}")

    def _end_resize(self, *, commit: bool) -> None:
        if not commit and self._resize_start_value is not None:
            self._brush_width = self._resize_start_value
            self.toolSizeChanged.emit(self._brush_width)
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_value = None
        self.update()
        debug_log.log(_DEBUG_SOURCE, f"resize gesture ended, commit={commit}, size={self._brush_width}")

    # -- stroke capture -------------------------------------------------

    def _push_undo(self) -> None:
        self._undo_stack.append(list(self._strokes))
        if len(self._undo_stack) > _UNDO_STACK_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()  # a new action invalidates any redo history, standard undo/redo semantics

    def _normalized(self, pos: QPointF) -> tuple[float, float]:
        w = max(1, self.width())
        h = max(1, self.height())
        return pos.x() / w, pos.y() / h

    def _deselect_stroke(self) -> None:
        self._selected_stroke = None
        self._stroke_drag_last_point = None
        self._stroke_move_dirty = False

    def _deselect_text_box(self) -> None:
        if self._selected_text_box is not None:
            self._selected_text_box.set_selected(False)
        self._selected_text_box = None

    def _find_stroke_near(self, point: tuple[float, float]) -> Stroke | None:
        """Select-tool hit test — topmost (last-drawn) stroke first, same
        per-point distance test _erase_near already uses, just returning
        the stroke instead of removing it. A fixed minimum pixel radius
        (_SELECT_HIT_RADIUS_PX) keeps thin strokes selectable even at
        brush_width=1, where the eraser's own hit radius would otherwise
        be too small to reliably click."""
        px, py = point
        radius = max(self._brush_width, _SELECT_HIT_RADIUS_PX) / max(1, self.width())
        for stroke in reversed(self._strokes):
            if any((qx - px) ** 2 + (qy - py) ** 2 <= radius * radius for qx, qy in stroke.points):
                return stroke
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        debug_log.log(
            _DEBUG_SOURCE,
            f"mousePressEvent at {event.position()}, drawing_enabled={self._drawing_enabled}, button={event.button()}",
        )
        if self._resizing:
            if event.button() == Qt.LeftButton:
                self._end_resize(commit=True)
            return
        if not self._drawing_enabled or event.button() != Qt.LeftButton:
            return
        if self._tool == TOOL_SELECT:
            self._handle_select_press(event)
            return
        if self._tool == TOOL_TEXT:
            self._add_text_box("", self._normalized(event.position()))
            self.textBoxesChanged.emit()
            return
        self._push_undo()
        point = self._normalized(event.position())
        if self._tool == TOOL_ERASER:
            self._erase_near(point)
        else:
            self._active_points = [point]
        self.update()

    def _handle_select_press(self, event: QMouseEvent) -> None:
        """Select tool, click landed on DrawOverlay itself (not a text
        box — those handle their own selection directly, see
        _TextBoxItem.mousePressEvent, since they're real widgets on top).
        Clicking a stroke selects+arms it for dragging (see
        mouseMoveEvent); clicking empty canvas clears whatever was
        selected, stroke or text box."""
        point = self._normalized(event.position())
        stroke = self._find_stroke_near(point)
        self._deselect_text_box()
        self._selected_stroke = stroke
        self._stroke_drag_last_point = point if stroke is not None else None
        self._stroke_move_dirty = False
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        debug_log.log(_DEBUG_SOURCE, f"mouseMoveEvent at {event.position()}, buttons={event.buttons()}")
        self._hover_pos = event.position()
        if self._resizing:
            delta = event.position().x() - self._resize_start_pos.x()
            new_size = round(self._resize_start_value + delta / _RESIZE_PIXELS_PER_UNIT)
            self._brush_width = max(_MIN_TOOL_SIZE, min(_MAX_TOOL_SIZE, new_size))
            self.toolSizeChanged.emit(self._brush_width)
            self.update()
            return
        if self._tool == TOOL_TEXT:
            self.update()
            return
        if self._tool == TOOL_SELECT:
            if (
                self._selected_stroke is not None
                and (event.buttons() & Qt.LeftButton)
                and self._stroke_drag_last_point is not None
            ):
                if not self._stroke_move_dirty:
                    self._push_undo()
                    self._stroke_move_dirty = True
                point = self._normalized(event.position())
                dx = point[0] - self._stroke_drag_last_point[0]
                dy = point[1] - self._stroke_drag_last_point[1]
                self._selected_stroke.points = [(x + dx, y + dy) for x, y in self._selected_stroke.points]
                self._stroke_drag_last_point = point
            self.update()
            return
        if not self._drawing_enabled or not (event.buttons() & Qt.LeftButton):
            self.update()
            return
        point = self._normalized(event.position())
        if self._tool == TOOL_ERASER:
            self._erase_near(point)
        else:
            self._active_points.append(point)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing or self._tool == TOOL_TEXT:
            return
        if self._tool == TOOL_SELECT:
            if event.button() == Qt.LeftButton and self._stroke_move_dirty:
                self.strokesChanged.emit()
            self._stroke_move_dirty = False
            return
        if not self._drawing_enabled or event.button() != Qt.LeftButton:
            return
        if self._tool == TOOL_BRUSH and len(self._active_points) > 1:
            self._strokes.append(Stroke(color=self._color, width=self._brush_width, points=self._active_points))
        self._active_points = []
        self.update()
        self.strokesChanged.emit()

    def leaveEvent(self, event) -> None:
        self._hover_pos = None
        self.update()
        super().leaveEvent(event)

    def _erase_near(self, point: tuple[float, float]) -> None:
        """Whole-stroke eraser: removes any stroke with at least one point
        within the current tool size (converted to normalized widget-space
        distance) of the cursor — simple by design, matching the "dumb"
        freehand tool this is, not a pixel-level eraser."""
        px, py = point
        radius = self._brush_width / max(1, self.width())
        remaining = [
            s
            for s in self._strokes
            if not any((qx - px) ** 2 + (qy - py) ** 2 <= radius * radius for qx, qy in s.points)
        ]
        if len(remaining) != len(self._strokes):
            self._strokes = remaining

    # -- painting -------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for stroke in self._strokes:
            self._paint_points(painter, stroke.points, QColor(stroke.color), stroke.width)
        if self._active_points and self._tool == TOOL_BRUSH:
            self._paint_points(painter, self._active_points, QColor(self._color), self._brush_width)
        if self._selected_stroke is not None:
            self._paint_stroke_selection(painter, self._selected_stroke)
        self._paint_hover_indicator(painter)
        painter.end()

    def _paint_stroke_selection(self, painter: QPainter, stroke: Stroke) -> None:
        """Dashed bounding-box "frame" around the selected stroke — the
        Select-tool equivalent of _TextBoxItem's own accent border, added
        2026-08-03 per the user's own request that a selected stroke or
        text box show some highlight indicating what's currently
        selected."""
        if not stroke.points:
            return
        w, h = self.width(), self.height()
        xs = [p[0] * w for p in stroke.points]
        ys = [p[1] * h for p in stroke.points]
        pad = max(6.0, float(stroke.width))
        rect = QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)
        painter.setPen(QPen(QColor(_SELECTION_COLOR), 2, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

    def _paint_hover_indicator(self, painter: QPainter) -> None:
        """A hollow circle following the cursor showing the exact brush
        (or eraser) size before a stroke is even started — hidden while
        drawing is disabled (video playing), the cursor isn't over the
        canvas at all (leaveEvent), or the Text/Select tool is active
        (size doesn't apply to placing a text box or to selecting)."""
        if self._hover_pos is None or not self._drawing_enabled or self._tool in (TOOL_TEXT, TOOL_SELECT):
            debug_log.log(
                _DEBUG_SOURCE,
                f"hover indicator skipped: hover_pos={self._hover_pos}, "
                f"drawing_enabled={self._drawing_enabled}, tool={self._tool}",
            )
            return
        if self._tool == TOOL_ERASER:
            # _erase_near's hit-test radius (normalized) is
            # self._brush_width / self.width() — converting that back to
            # pixels for painting is just self._brush_width again.
            radius = max(1.0, float(self._brush_width))
            color = QColor(255, 255, 255, 200)
        else:
            radius = max(1.0, self._brush_width / 2)
            color = QColor(self._color)
            color.setAlpha(200)
        painter.setPen(QPen(color, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(self._hover_pos, radius, radius)

    def _paint_points(self, painter: QPainter, points: list[tuple[float, float]], color: QColor, width: int) -> None:
        paint_stroke_points(painter, points, color, width, self.width(), self.height())


def paint_stroke_points(
    painter: QPainter, points: list[tuple[float, float]], color: QColor, width: int, w: int, h: int
) -> None:
    """Module-level so ReadOnlyCommentOverlay (below) can paint a stroke
    pixel-identically to how DrawOverlay._paint_points draws it, without
    either duplicating the math or depending on the other class."""
    if len(points) < 2:
        return
    path = QPainterPath()
    path.moveTo(points[0][0] * w, points[0][1] * h)
    for x, y in points[1:]:
        path.lineTo(x * w, y * h)
    painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(path)


class ReadOnlyCommentOverlay(QWidget):
    """View-mode-only, read-only rendering of a frame's saved strokes and
    text boxes on top of the video — no mouse handling, no editing, no
    toolbox. Added 2026-07-20 so the plain viewing page can see what was
    commented without opening the full Edit Video dialog; visibility is
    toggled by player_widget.py's own button.
    WA_TransparentForMouseEvents so it can never itself become a second
    thing standing between the mouse and anything else on the video (the
    exact class of bug developer/bug-history/2026-07-20-draw-overlay-native-video-widget.md
    and 2026-07-20-text-tool-drew-strokes-simultaneously.md were about —
    even though this widget has nothing interactive of its own, the habit
    is worth keeping)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._strokes: list[Stroke] = []
        self._text_boxes: list[dict] = []

    def set_frame(self, strokes: list[Stroke], text_boxes: list[dict]) -> None:
        self._strokes = strokes
        self._text_boxes = text_boxes
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        for stroke in self._strokes:
            paint_stroke_points(painter, stroke.points, QColor(stroke.color), stroke.width, w, h)
        for box in self._text_boxes:
            self._paint_text_box(painter, box, w, h)
        painter.end()

    def _paint_text_box(self, painter: QPainter, box: dict, w: int, h: int) -> None:
        text = box.get("text", "")
        if not text:
            return
        rect = QRectF(box.get("x", 0.0) * w, box.get("y", 0.0) * h, _TEXT_BOX_WIDTH, _TEXT_BOX_HEIGHT)
        # Mirrors _TextBoxItem._apply_style's rendering (bg color/opacity,
        # stroke-as-border, bold) so the read-only preview looks the same
        # as the editable box did.
        bg = QColor(box.get("bg_color", _DEFAULT_BG_COLOR))
        bg.setAlpha(box.get("bg_opacity", _DEFAULT_BG_OPACITY))
        stroke_on = box.get("stroke", False)
        border_color = QColor(_SELECTION_COLOR) if stroke_on else QColor("#444444")
        painter.setPen(QPen(border_color, 2 if stroke_on else 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 4, 4)
        font = painter.font()
        font.setBold(box.get("bold", False))
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(rect.adjusted(6, 4, -6, -4), Qt.TextWordWrap, text)
