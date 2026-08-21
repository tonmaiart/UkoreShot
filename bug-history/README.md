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
