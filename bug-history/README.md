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
