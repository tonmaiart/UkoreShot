# UkoreShot bug history

Bugs fixed specifically within this plugin's own code, going forward from
2026-07-21 (see the top-level `README.md`'s "Structure" section for what
each subfolder covers).

## 2026-08-21 — Get Video exported with no audio

`core/video_compress.py`'s `compress_to_fit` (used by `pushButton_get_video`
in `interface/video_library_page.py`) transcoded a video without an
explicit `-map`, so ffmpeg's default "best stream per type" selection
sometimes dropped the source's audio track even though the original
Maya-playblasted `.mov` genuinely had one (captured via `cmds.playblast`'s
own `sound=` option — see `maya-scripts/UkorePlayblast/function.py`).
Fixed by adding `-map 0:v:0 -map 0:a:0?` to the ffmpeg command — explicit
mapping instead of relying on default stream selection, with `?` making
the audio stream optional so a genuinely silent source still encodes fine
with video only. Compression only runs when the source is already over
`_MAX_EXPORT_BYTES`; the "already under the cap, just copy the file"
fast path was never affected (it never re-encodes at all).

## 2026-08-21 — Comment marks on the timeline clumped near the start in UkoreShotPage

`interface/player_widget.py`'s `PlayerWidget.set_comment_frames` forwarded
its `frames` argument (always *frame* indices) straight through to
`TimelineSlider.set_marked_frames` unconverted. That's correct in sequence
mode (`load_sequence`, what `CommentEditor` always uses — the slider's own
range/value are already frame-indexed there), but in **video mode**
(`load_video` — the common case in `UkoreShotPage` for any entry with a
local video file not yet extracted to a sequence), `position_slider`'s
range/value are in *milliseconds* (see `_on_duration_changed`/
`_on_position_changed`). A mark at frame 24 was plotted as if it were 24ms
into a clip that's thousands of milliseconds long, so every mark landed
within a few pixels of the left edge regardless of where the actual
comment was. `jump_to_frame`/`_jump_to_frame` already converted frame
index to ms correctly for video mode — only the mark-drawing path had the
bug, which is why clicking Previous/Next Comment still jumped to the right
place even though the visual marks looked wrong. Fixed by converting
frames to milliseconds (`round(f / self._fps() * 1000)`) in
`set_comment_frames` whenever `self._mode == "video"`, same fps basis
`_on_duration_changed`/`_on_position_changed` already use.

## 2026-08-21 — Get Video failed outright with "Error parsing a filter description"

`core/video_compress.py`'s `burn_frame_numbers` built its `drawtext`
filter's frame-number text as `text='%{n}'` — `n` is not actually a
documented drawtext expansion keyword (the correct one is `frame_num`),
so ffmpeg failed to parse the filtergraph at all and Get Video errored
out every time with "Error parsing a filter description" /
"Error opening output files: Invalid argument". Get Video - Commented was
never affected — it burns the frame number via `player_widget.py`'s
`paint_frame_number` (plain QPainter text, composited per-frame in Python,
not ffmpeg `drawtext` at all — see `video_library_page.py`'s
`_render_commented_video`). Fixed by using `%{frame_num}` instead of
`%{n}`.

## 2026-08-21 — Edit Message showed no dialog at all

`interface/comment_editor.py`'s new `_EditCommentDialog` (the
`QPlainTextEdit`-based replacement for the old single-line
`QInputDialog.getText`) called `cursor.movePosition(cursor.End)` to place
the cursor at the end of any existing text — `QTextCursor.End`'s flat
(unscoped) enum access isn't valid against the PySide6 version installed
here, so the constructor raised inside `_on_edit_message_clicked`'s
connected slot. PySide6 prints an uncaught slot exception to the console
but doesn't propagate it, so clicking pushButton_edit_message just did
nothing visible — the exact "silent failure" class of bug this file's own
`CommentEditor.__init__` docstring already documents for a different past
crash (the `WindowMaximized`/`resizeEvent` one). Fixed by dropping the
cursor-to-end positioning entirely (cosmetic only, not worth the enum
risk) rather than chasing the "correct" scoped-vs-flat spelling.
