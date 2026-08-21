# cache/plugins/UkoreShot/bin/

`ffmpeg.exe` — bundled 2026-08-21 so a fresh machine never hits "ffmpeg
Required" the first time it tries to Comment/Mark as Share/Send to Discord
(`core/video_sequence.py`'s lazy image-sequence extraction and
`core/video_compress.py`'s Discord-size compression both depend on ffmpeg
unconditionally, ever since Maya stopped writing its own image sequence —
see `../maya-scripts/README.md`). `core/video_compress.py::resolve_ffmpeg_path`
uses this automatically once an explicit Repository Setting > UkoreShot
ffmpeg Path isn't set — see that function's own docstring for the full
"configured, else bundled, else PATH" order.

- **Source**: `github.com/BtbN/FFmpeg-Builds` (an automated build repo
  linked directly from ffmpeg.org's own official Windows-builds page),
  `ffmpeg-master-latest-win64-gpl.zip`, only `bin/ffmpeg.exe` extracted —
  not `ffplay.exe`/`ffprobe.exe`, this plugin never needs either.
- **Version**: `N-126229-gf101fce22d-20260820` (`ffmpeg -version`), built
  2026-08-20.
- **Windows only** — a GPL static build for `win64`. A studio machine
  running anything else needs an explicit ffmpeg path configured under
  Repository Setting > UkoreShot instead (`resolve_ffmpeg_path` falls
  through past this file if it's ever missing/not runnable, down to a
  plain PATH lookup as a last resort — see that function).
- **License**: GPL (this build has `--enable-gpl --enable-libx264`,
  needed for `video_compress.py`'s H.264 Discord-compression output) —
  `ffmpeg-LICENSE.txt` alongside the binary is the exact license text that
  shipped with this specific build.
- **Updating**: re-download the same `win64-gpl` asset from a newer
  BtbN release, replace both files here, update the version string above.
  No code changes needed elsewhere — every caller goes through
  `resolve_ffmpeg_path`, never a hardcoded path.
