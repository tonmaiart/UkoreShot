"""Playblast — fully hardcoded (confirmed with the user 2026-08-21; the old
"Playblast Options..." dialog and options_store.py were removed, along with
its menu item — nothing here is configurable per-repo/per-artist anymore).
Destination folder is a fixed per-machine folder under
PublishApi.repo_paths.find_cache_dir(), keyed by project/repo (see
_resolve_video_root) — not configurable, and never synced via git/repo
sync; the filename itself encodes shot/index/version (see "Flat naming
convention" in maya-scripts/README.md, and _resolve_filename_stem below)
instead of the folder structure carrying that information."""

from __future__ import annotations

import os
import re

import maya.cmds as cmds
from PublishApi import repo_paths

_SHOT_CODE_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)")
# SEQ_ShotCode_index_version — see maya-scripts/README.md's "Flat naming
# convention" section. Both tokens are sanitized to letters/digits only
# before a filename is ever built (_sanitize_token), so splitting a stem on
# "_" and expecting exactly 4 parts is safe; this is also what lets
# cache/plugins/UkoreShot/core/video_naming.py (the desktop-side reader of
# these same filenames) parse them back reliably.
_FILENAME_PATTERN = re.compile(r"^([^_]+)_([^_]+)_(\d+)_v(\d+)$")

# Hardcoded studio-standard playblast settings (2026-08-21) — qt/H.264 at
# 80% quality, 80% viewport scale, HD 1080 resolution driven off (and
# forced onto) the scene's render settings.
_FORMAT = "qt"
_COMPRESSION = "H.264"
_QUALITY = 80
_VIEWPORT_SCALE = 80
_RESOLUTION_WIDTH = 1920
_RESOLUTION_HEIGHT = 1080
_RESOLUTION_DEVICE_ASPECT = 1.778
_RESOLUTION_PIXEL_ASPECT = 1.0
# Resolution Gate + Gate Mask — turned ON for the capture instead of off
# (2026-08-21, reversing the previous hide-the-gates behavior per the
# user's own request), so the render frame is visibly burned into the
# playblast.
_GATE_DISPLAY_ATTRS = ("displayResolution", "displayGateMask")


def _sanitize_token(value: str) -> str:
    """Strips anything that isn't a letter/digit — the flat filename
    convention splits its stem on "_" into exactly 4 parts, so a sequence/
    shot containing an underscore (or a space, which is worse to have
    literally in a shared-drive filename anyway) would silently corrupt
    that split. Falls back to "x" for a value that sanitizes down to
    nothing (e.g. an all-symbols scene name)."""
    return re.sub(r"[^A-Za-z0-9]", "", value) or "x"


def _resolve_video_root(project_id: str, repo_id: str):
    """Mirrors cache/plugins/UkoreShot/core/video_path_store.py's
    resolve_video_root exactly — a fixed per-machine folder under
    PublishApi.repo_paths.find_cache_dir(), keyed by project_id/repo_id, so
    playblasts land in the same local-only folder UkoreShot's desktop-side
    library reads from. Duplicated rather than imported directly: Maya's
    Python (mayapy) is a completely separate interpreter from UkoreHub's
    desktop app and has no `api` (PluginAPI) instance to resolve
    `api.cache_dir` from — see core/README.md's naming note."""
    video_root = repo_paths.find_cache_dir() / "ukore_shot" / project_id / repo_id
    video_root.mkdir(parents=True, exist_ok=True)
    return video_root


def _current_scene_basename() -> str:
    current_file = cmds.file(q=True, sn=True)
    return os.path.splitext(os.path.basename(current_file))[0] if current_file else "untitled"


def _resolve_seq_shot(scene_basename: str):
    """(sequence, shot_code) parsed off the scene name's leading
    letters+digits run, e.g. "KBA140_Anim_Layout_v001" -> ("KBA",
    "KBA140"), both already letters/digits-only so no _sanitize_token
    round-trip is needed for the common case. Falls back to sequence
    "misc" + the whole (sanitized) scene basename as the shot code when
    the scene name doesn't start with that pattern (untitled scene, or a
    name this shot-code rule doesn't apply to) — a deliberate fallback,
    not an error, so an oddly-named scene still produces a playblast
    instead of failing outright."""
    match = _SHOT_CODE_PATTERN.match(scene_basename)
    if match:
        prefix, digits = match.group(1), match.group(2)
        return prefix, prefix + digits
    return "misc", _sanitize_token(scene_basename)


def _matching_versions(video_root, sequence: str, shot_code: str):
    """{version: [index, index, ...]} for every existing flat file in
    video_root whose stem matches this exact sequence/shot via
    _FILENAME_PATTERN. video_root's top level only (not recursive) — this
    convention has no subfolders of its own; a legacy shot/version-
    subfoldered playblast from before 2026-07-20 sits inside a subfolder
    and is invisible to this scan, left alone per the user's own decision
    (see maya-scripts/README.md) rather than migrated. Also ignores any
    other file in video_root that doesn't happen to match this exact
    pattern — not an error, just not one of this tool's own files."""
    result = {}
    if not video_root.is_dir():
        return result
    for path in video_root.iterdir():
        if not path.is_file():
            continue
        match = _FILENAME_PATTERN.match(path.stem)
        if not match:
            continue
        seq, shot, index_str, version_str = match.groups()
        if seq == sequence and shot == shot_code:
            result.setdefault(int(version_str), []).append(int(index_str))
    return result


def _next_version(video_root, sequence: str, shot_code: str) -> int:
    versions = _matching_versions(video_root, sequence, shot_code)
    return (max(versions) + 1) if versions else 1


def _stem_is_free(video_root, stem: str) -> bool:
    """True if no file in video_root already starts with this exact stem
    followed by "." — the defensive check _resolve_filename_stem's own
    loop uses below. cmds.playblast's own forceOverwrite defaults to False
    and this tool deliberately never sets it (overwriting an existing
    playblast is never correct — only ever picking a new name is, per the
    user's own request), so publish_playblast must never hand it a stem
    some existing file already occupies, or Maya aborts outright with
    "The file ... exists and overwrite flag not set.\""""
    if not video_root.is_dir():
        return True
    prefix = stem + "."
    return not any(p.name.startswith(prefix) for p in video_root.iterdir() if p.is_file())


def _resolve_filename_stem(video_root, sequence: str, shot_code: str) -> str:
    """SEQ_ShotCode_index_version, no extension. Every playblast starts a
    brand-new version (index always "001" — one clip is the whole
    version).

    The initial version picked above only trusts _matching_versions'
    regex scan of existing files, which can miss one whose actual on-disk
    name doesn't cleanly match SEQ_ShotCode_index_version.ext (e.g. a Maya
    output format/compression combo that appends more than a single clean
    extension) — that made the "next" version look free when it wasn't, so
    cmds.playblast aborted with a real "file exists" error instead of ever
    writing (fixed 2026-08-21, a real user report). The while loop below is
    a second, independent check straight against the filesystem
    (_stem_is_free, no regex) — it keeps bumping past an exact collision
    until the candidate stem is genuinely free, regardless of why the
    scan-based pick above missed it."""
    version = _next_version(video_root, sequence, shot_code)
    index = 1
    stem = "{}_{}_{:03d}_v{:03d}".format(sequence, shot_code, index, version)
    while not _stem_is_free(video_root, stem):
        version += 1
        stem = "{}_{}_{:03d}_v{:03d}".format(sequence, shot_code, index, version)
    return stem


def _apply_render_settings():
    """Forces the scene's render settings to Arnold + the HD 1080 image
    size preset before every playblast (2026-08-21, per the user's own
    request) — both the renderer choice and the resolution are studio-wide
    standards now, not something a scene should be able to drift away
    from. width/height/deviceAspectRatio/pixelAspect are exactly what
    Maya's own "HD 1080" Render Settings preset sets."""
    cmds.setAttr("defaultRenderGlobals.currentRenderer", "arnold", type="string")
    cmds.setAttr("defaultResolution.width", _RESOLUTION_WIDTH)
    cmds.setAttr("defaultResolution.height", _RESOLUTION_HEIGHT)
    cmds.setAttr("defaultResolution.deviceAspectRatio", _RESOLUTION_DEVICE_ASPECT)
    cmds.setAttr("defaultResolution.pixelAspect", _RESOLUTION_PIXEL_ASPECT)


def _enable_gate_display():
    """Turns Resolution Gate + Gate Mask on for every camera in the scene
    for the duration of a playblast (2026-08-21, per the user's own
    request — reverses the tool's previous hide-the-gates behavior).
    Records each attribute's prior value first so _restore_gate_display can
    put back exactly what was there before, only for the attributes
    actually flipped on here (an attribute already on is left alone and
    never added to the returned list, so restoring can't accidentally turn
    one off that wasn't off to begin with). Swallows any per-camera/per-
    attribute Maya API error — a camera missing one of these attributes
    just leaves that one attribute untouched rather than failing the whole
    playblast."""
    saved_state = []
    for camera_shape in cmds.ls(type="camera") or []:
        for attr in _GATE_DISPLAY_ATTRS:
            try:
                plug = "{}.{}".format(camera_shape, attr)
                if cmds.getAttr(plug):
                    continue
                cmds.setAttr(plug, True)
                saved_state.append((camera_shape, attr))
            except Exception:
                continue
    return saved_state


def _restore_gate_display(saved_state):
    for camera_shape, attr in saved_state:
        try:
            cmds.setAttr("{}.{}".format(camera_shape, attr), False)
        except Exception:
            pass


def _locate_video_output(export_file_path: str) -> str:
    """cmds.playblast's video formats each append their own extension to
    `filename`, and which exact extension a given format/compression combo
    actually produces is Maya-version-dependent (e.g. "qt"+"H.264" writes
    .mp4 on some Maya versions, .mov on others) — glob for whatever Maya
    actually wrote next to export_file_path rather than assuming. Raises
    RuntimeError if nothing matches (playblast silently produced no
    file)."""
    directory = os.path.dirname(export_file_path)
    base_name = os.path.basename(export_file_path)
    candidates = sorted(name for name in os.listdir(directory) if name.startswith(base_name + "."))
    if not candidates:
        raise RuntimeError("Maya's playblast did not produce an output file at {}".format(export_file_path))
    return os.path.join(directory, candidates[0])


def publish_playblast() -> None:
    print("[UkorePlayblast] Playblast started...")
    try:
        project, repo, _ = repo_paths.get_active_repo()
        if project is None:
            raise RuntimeError("No active repo selected in UkoreHub. Open UkoreHub, pick a project/repo, then try again.")

        video_root = _resolve_video_root(project.id, repo.id)

        sequence, shot_code = _resolve_seq_shot(_current_scene_basename())
        # forceOverwrite is deliberately never passed to cmds.playblast()
        # below (defaults to False) — an existing playblast should never
        # get silently overwritten, only ever get a fresh name instead;
        # _resolve_filename_stem's own collision-avoidance loop
        # (_stem_is_free) is what guarantees this stem is actually free.
        stem = _resolve_filename_stem(video_root, sequence, shot_code)
        export_file_path = os.path.join(str(video_root), stem)
        print("[UkorePlayblast] Destination folder: {}".format(video_root))
        print("[UkorePlayblast] Filename: {}".format(stem))

        _apply_render_settings()
        width = cmds.getAttr("defaultResolution.width")
        height = cmds.getAttr("defaultResolution.height")

        # Resolution Gate + Gate Mask turned on for every camera in the
        # scene for the duration of the capture, restored afterward
        # regardless of success/failure — a playblast shouldn't leave the
        # viewport's display state changed.
        gate_display_state = _enable_gate_display()
        try:
            playblast_kwargs = {
                "filename": export_file_path,
                "format": _FORMAT,
                "compression": _COMPRESSION,
                "qlt": _QUALITY,
                "width": width,
                "height": height,
                "percent": _VIEWPORT_SCALE,
                "showOrnaments": False,
                "offScreen": True,
            }

            sound_nodes = cmds.ls(type="audio")
            sound_node_name = sound_nodes[0] if sound_nodes else ""
            if sound_node_name:
                current_source_start = cmds.getAttr("{}.sourceStart".format(sound_node_name))
                current_offset = cmds.getAttr("{}.offset".format(sound_node_name))
                if current_source_start != 0:
                    cmds.setAttr("{}.sourceStart".format(sound_node_name), 0)
                    cmds.setAttr("{}.offset".format(sound_node_name), current_offset - current_source_start)
                playblast_kwargs["sound"] = sound_node_name

            cmds.playblast(**playblast_kwargs)
            saved_path = _locate_video_output(export_file_path)
        finally:
            _restore_gate_display(gate_display_state)

        print("[UkorePlayblast] Playblast saved: {}".format(saved_path))
        message = "<hl>Playblast saved:</hl> {}".format(saved_path)
        cmds.inViewMessage(amg=message, pos="midCenter", fade=True)
    except Exception as e:
        print("[UkorePlayblast] Playblast failed: {}".format(e))
        cmds.confirmDialog(title="UkorePlayblast", message=str(e))
