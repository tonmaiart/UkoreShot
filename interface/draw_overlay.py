from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

# stdlib logging, not the retired core.extensibility.debug_log bus (gone
# entirely as of 2026-08-21 — see core-api.md's events/ note and
# DebugConsole.md) — logging.getLogger(name) needs no registration at all,
# DebugConsole picks up every record automatically via the QtLogHandler
# launcher.py attaches to the root logger. Revived 2026-08-20 from git
# history (commit f9b3505 in UkoreHubDev, before this plugin's draw/comment
# editor was extracted to BananaSketch on 2026-08-08) — see ../README.md
# for the full story.
_logger = logging.getLogger("UkoreShot.Draw")

_UNDO_STACK_LIMIT = 20
_MIN_TOOL_SIZE = 1
_MAX_TOOL_SIZE = 60
_RESIZE_PIXELS_PER_UNIT = 4  # mouse-drag sensitivity for the "F" resize gesture
_WHEEL_STEP_PER_NOTCH = 1  # brush-size change per standard wheel notch (120 angleDelta units)
_SELECT_HIT_RADIUS_PX = 8  # minimum pixel hit-test radius for selecting a thin stroke via the Select tool
_SELECTION_COLOR = "#5865f2"

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


class DrawOverlay(QWidget):
    """Transparent freehand-drawing canvas stacked on top of the video
    surface (see player_widget.py's _VideoStack) — one stroke list per
    frame index, deliberately simple ("แบบโง่", per the user's own
    description of the feature): fixed-shape freehand pen only, no vector
    editing after the fact beyond a whole-stroke eraser, a Select tool
    to move/delete an existing stroke, and a snapshot-based per-frame
    undo/redo stack pair (undo() pushes the current state onto
    _redo_stack before popping _undo_stack, and vice versa for redo();
    any new action that mutates _strokes — a new stroke, clear_frame, a
    Select-tool move, a Select-tool delete — clears _redo_stack via
    _push_undo, the same "new edit invalidates redo history" rule every
    undo/redo system uses). Undo/redo history is NOT persisted — only the
    final stroke list for a frame is ever saved (see comment_store.py) —
    and resets whenever a different frame is loaded, since undo/redo only
    ever makes sense within one frame's own editing session. Strokes are
    only ever shown for the exact frame index currently loaded (see
    load_frame) so scrubbing between frames doesn't smear one frame's
    drawing onto another.

    **Interaction model simplified 2026-08-21** per the user's own
    request: brush and eraser are no longer separate selectable tools —
    left mouse button always draws, right mouse button always erases,
    whenever Select mode (self._select_mode, toggled by the toolbar's one
    remaining Select button) is off. Select mode is still an explicit
    toggle since it's a genuinely different interaction (click an existing
    stroke to select+drag it) that can't be conflated with plain
    erasing — while it's on, left-click hit-tests/drags a stroke instead
    of drawing, and right-click does nothing. The old Text tool
    (_TextBoxItem, current_text_boxes, textBoxesChanged) is removed
    entirely per the same request — "text_boxes" may still appear in
    older saved comments.json data (harmless, just never displayed or
    added to anymore) and comment_editor.py's persistence still passes an
    empty list through for it, but nothing here creates one again. The
    old "1"/"2"/"3"/"4" tool-switch keyboard shortcuts were never
    reimplemented in this revival (only the toolbar buttons existed) so
    there was nothing left to remove for those. Mouse-wheel over the
    canvas now adjusts brush/eraser size directly (wheelEvent) instead of
    needing the "F"-drag gesture for every size change — the "F" gesture
    itself is unchanged, both work.

    Selecting (Select tool only) shows a highlight — a dashed bounding box
    around a selected stroke (_paint_stroke_selection) — and Delete/
    Backspace removes whichever stroke is currently selected."""

    strokesChanged = Signal()
    # Emitted whenever brush_width changes from any source (the "F" resize
    # gesture, the mouse wheel, or set_brush_width() called from the
    # toolbar's Size slider itself) so PlayerWidget can keep that slider's
    # displayed value in sync no matter which of the three changed it.
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
        # "F" resize gesture and mouse wheel both work as soon as the
        # cursor is over the canvas, without needing an explicit click
        # first — mirrors how a 3D viewport typically reacts to hover.
        self.setFocusPolicy(Qt.StrongFocus)
        self._strokes: list[Stroke] = []
        self._undo_stack: list[list[Stroke]] = []
        self._redo_stack: list[list[Stroke]] = []
        self._active_points: list[tuple[float, float]] = []
        self._color = "#ff3b30"
        # One "tool size" (pixel radius/width) shared by both the brush
        # (stroke width) and the eraser (hit-test radius) — the "F" resize
        # gesture, the mouse wheel, and the toolbar's Size slider all just
        # adjust this one value.
        self._brush_width = 4
        self._select_mode = False
        self._drawing_enabled = True
        self._hover_pos = None
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_value: int | None = None
        # Select-tool state — a stroke is selected by object identity (the
        # exact Stroke instance clicked), not an index, since the list can
        # be mutated (undo/redo/erase) while a selection is live.
        self._selected_stroke: Stroke | None = None
        self._stroke_drag_last_point: tuple[float, float] | None = None
        self._stroke_move_dirty = False  # True once a drag has actually moved the selected stroke this press

    # -- toolbar-facing API -------------------------------------------------

    def set_color(self, color: QColor) -> None:
        self._color = color.name()

    def set_brush_width(self, width: int) -> None:
        self._brush_width = max(_MIN_TOOL_SIZE, min(_MAX_TOOL_SIZE, width))
        self.update()

    def set_select_mode(self, enabled: bool) -> None:
        """Toggled by the toolbar's Select button (see player_widget.py).
        Abandons any in-progress stroke so toggling mid-drag can't leave a
        half-drawn stroke dangling, and leaving Select mode clears whatever
        stroke was selected so the highlight doesn't linger."""
        self._select_mode = enabled
        self._active_points = []
        if not enabled:
            self._deselect_stroke()
        self.update()

    def set_drawing_enabled(self, enabled: bool) -> None:
        """Drawing only makes sense while playback is paused on one exact
        frame (see PlayerWidget._set_paused_state) — disabled during
        playback so a stray click while the video is moving doesn't start
        a stroke against a frame index that's about to change."""
        self._drawing_enabled = enabled

    def load_frame(self, strokes: list[Stroke], text_boxes: list[dict] | None = None) -> None:
        """Swaps in the persisted stroke list for whichever frame index is
        now current. text_boxes is accepted (and ignored) only so
        comment_editor.py's call site — which still reads whatever an
        older comments.json happened to save under that key — doesn't need
        a special case; nothing here creates new text box data anymore."""
        self._strokes = list(strokes)
        self._undo_stack = []
        self._redo_stack = []
        self._active_points = []
        self._selected_stroke = None
        self._stroke_drag_last_point = None
        self._stroke_move_dirty = False
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

    # -- "F" resize gesture + mouse wheel ------------------------------------

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
        if event.key() == Qt.Key_F and self._drawing_enabled and self._hover_pos is not None and not self._select_mode:
            if self._resizing:
                self._end_resize(commit=True)
            else:
                self._start_resize()
            return
        if event.key() == Qt.Key_Escape and self._resizing:
            self._end_resize(commit=False)
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Scroll to adjust brush/eraser size directly — added 2026-08-21
        per the user's own request, alongside (not replacing) the existing
        "F"-drag gesture. Only while drawing is enabled and Select mode is
        off — size doesn't apply to selecting."""
        if not self._drawing_enabled or self._select_mode:
            super().wheelEvent(event)
            return
        notches = event.angleDelta().y() / 120
        if notches == 0:
            super().wheelEvent(event)
            return
        self.set_brush_width(self._brush_width + round(notches) * _WHEEL_STEP_PER_NOTCH)
        self.toolSizeChanged.emit(self._brush_width)
        event.accept()

    def _start_resize(self) -> None:
        self._resizing = True
        self._resize_start_pos = self._hover_pos
        self._resize_start_value = self._brush_width
        _logger.debug("resize gesture started at size=%s", self._brush_width)

    def _end_resize(self, *, commit: bool) -> None:
        if not commit and self._resize_start_value is not None:
            self._brush_width = self._resize_start_value
            self.toolSizeChanged.emit(self._brush_width)
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_value = None
        self.update()
        _logger.debug("resize gesture ended, commit=%s, size=%s", commit, self._brush_width)

    # -- stroke capture -------------------------------------------------

    def _push_undo(self) -> None:
        self._undo_stack.append(list(self._strokes))
        if len(self._undo_stack) > _UNDO_STACK_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()  # a new action invalidates any redo history, standard undo/redo semantics

    def _normalized(self, pos) -> tuple[float, float]:
        w = max(1, self.width())
        h = max(1, self.height())
        return pos.x() / w, pos.y() / h

    def _deselect_stroke(self) -> None:
        self._selected_stroke = None
        self._stroke_drag_last_point = None
        self._stroke_move_dirty = False

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
        _logger.debug(
            "mousePressEvent at %s, drawing_enabled=%s, button=%s, select_mode=%s",
            event.position(), self._drawing_enabled, event.button(), self._select_mode,
        )
        if self._resizing:
            if event.button() == Qt.LeftButton:
                self._end_resize(commit=True)
            return
        if not self._drawing_enabled or event.button() not in (Qt.LeftButton, Qt.RightButton):
            return
        if self._select_mode:
            if event.button() == Qt.LeftButton:
                self._handle_select_press(event)
            return
        self._push_undo()
        point = self._normalized(event.position())
        if event.button() == Qt.RightButton:
            self._erase_near(point)
            self.update()
            return
        self._active_points = [point]
        self.update()

    def _handle_select_press(self, event: QMouseEvent) -> None:
        """Select tool, left-click. Clicking a stroke selects+arms it for
        dragging (see mouseMoveEvent); clicking empty canvas clears
        whatever was selected."""
        point = self._normalized(event.position())
        stroke = self._find_stroke_near(point)
        self._selected_stroke = stroke
        self._stroke_drag_last_point = point if stroke is not None else None
        self._stroke_move_dirty = False
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover_pos = event.position()
        if self._resizing:
            delta = event.position().x() - self._resize_start_pos.x()
            new_size = round(self._resize_start_value + delta / _RESIZE_PIXELS_PER_UNIT)
            self._brush_width = max(_MIN_TOOL_SIZE, min(_MAX_TOOL_SIZE, new_size))
            self.toolSizeChanged.emit(self._brush_width)
            self.update()
            return
        if self._select_mode:
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
        if not self._drawing_enabled:
            self.update()
            return
        point = self._normalized(event.position())
        if event.buttons() & Qt.RightButton:
            self._erase_near(point)
            self.update()
            return
        if event.buttons() & Qt.LeftButton:
            self._active_points.append(point)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._resizing:
            return
        if self._select_mode:
            if event.button() == Qt.LeftButton and self._stroke_move_dirty:
                self.strokesChanged.emit()
            self._stroke_move_dirty = False
            return
        if not self._drawing_enabled or event.button() not in (Qt.LeftButton, Qt.RightButton):
            return
        if event.button() == Qt.LeftButton and len(self._active_points) > 1:
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
        if self._active_points:
            self._paint_points(painter, self._active_points, QColor(self._color), self._brush_width)
        if self._selected_stroke is not None:
            self._paint_stroke_selection(painter, self._selected_stroke)
        self._paint_hover_indicator(painter)
        painter.end()

    def _paint_stroke_selection(self, painter: QPainter, stroke: Stroke) -> None:
        """Dashed bounding-box "frame" around the selected stroke, added
        2026-08-03 per the user's own request that a selected stroke show
        some highlight indicating what's currently selected."""
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
        size before a stroke is even started — hidden while drawing is
        disabled (video playing), the cursor isn't over the canvas at all
        (leaveEvent), or Select mode is on (size doesn't apply to
        selecting). Shown in the brush color regardless of which mouse
        button would actually be used (left=draw, right=erase share the
        same size now) — simplest single preview rather than needing to
        track which button is about to be pressed."""
        if self._hover_pos is None or not self._drawing_enabled or self._select_mode:
            return
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
    """Module-level, not a DrawOverlay method — kept separate so any future
    read-only stroke renderer can reuse the exact same drawing math without
    depending on DrawOverlay itself."""
    if len(points) < 2:
        return
    path = QPainterPath()
    path.moveTo(points[0][0] * w, points[0][1] * h)
    for x, y in points[1:]:
        path.lineTo(x * w, y * h)
    painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(path)
