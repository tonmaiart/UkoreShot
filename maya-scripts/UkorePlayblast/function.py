"""Configurable playblast — replaces UkoreMaya's old hardcoded "Quick
Playblast" (plugins/repo_internal/MayaToolkit/maya-scripts/UkoreMaya/core/
function.py's now-removed publish_playblast). Destination folder is a
fixed per-machine folder under PublishApi.repo_paths.find_cache_dir(), keyed
by project/repo (see _resolve_video_root) — not configurable, and never
synced via git/repo sync; the filename itself encodes
shot/variation/index/version (see "Flat naming convention" in
maya-scripts/README.md, and _resolve_filename_stem below) instead of the
folder structure carrying that information. Options come from this tool's
own "Playblast Options..." dialog inside Maya (options_dialog.py — confirmed
with the user 2026-07-19 this belongs in Maya, not a UkoreHub Settings tab)
via options_store.py, resolved the same "construct the store straight off
disk" way every Maya-side module in this codebase uses (Maya's Python has no
PluginAPI instance), via PublishApi for active-repo resolution."""

from __future__ import annotations

import os
import re

import maya.cmds as cmds
from PublishApi import repo_paths
from UkorePlayblast import options_store

_SHOT_CODE_PATTERN = re.compile(r"^([A-Za-z]+)(\d+)")
# SEQ_ShotCode_Variation_index_version — see maya-scripts/README.md's "Flat
# naming convention" section. Every one of the first three tokens is
# sanitized to letters/digits only before a filename is ever built
# (_sanitize_token), so splitting a stem on "_" and expecting exactly 5
# parts is safe; this is also what lets
# cache/plugins/UkoreShot/core/video_naming.py (the desktop-side reader of
# these same filenames) parse them back reliably.
_FILENAME_PATTERN = re.compile(r"^([^_]+)_([^_]+)_([^_]+)_(\d+)_v(\d+)$")


def _sanitize_token(value: str) -> str:
    """Strips anything that isn't a letter/digit — the flat filename
    convention splits its stem on "_" into exactly 5 parts, so a sequence/
    shot/variation containing an underscore (or a space, which is worse to
    have literally in a shared-drive filename anyway) would silently
    corrupt that split. Falls back to "x" for a value that sanitizes down
    to nothing (e.g. an all-symbols scene name)."""
    return re.sub(r"[^A-Za-z0-9]", "", value) or "x"


def _resolve_video_root(project_id: str, repo_id: str):
    """Mirrors cache/plugins/UkoreShot/core/video_path_store.py's
    resolve_video_root exactly — a fixed per-machine folder under
    PublishApi.repo_paths.find_cache_dir(), keyed by project_id/repo_id, so
    playblasts land in the same local-only folder UkoreShot's desktop-side
    library reads from. Duplicated rather than imported directly: Maya's
    Python (mayapy) is a completely separate interpreter from UkoreHub's
    desktop app and has no `api` (PluginAPI) instance to resolve
    `api.cache_dir` from — see core/README.md's naming note. No longer
    takes a repo_path — video storage doesn't live inside the repo checkout
    at all, so it's not tied to git/repo sync across machines."""
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


def _matching_versions(video_root, sequence: str, shot_code: str, variation: str):
    """{version: [index, index, ...]} for every existing flat file in
    video_root whose stem matches this exact sequence/shot/variation via
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
        seq, shot, var, index_str, version_str = match.groups()
        if seq == sequence and shot == shot_code and var == variation:
            result.setdefault(int(version_str), []).append(int(index_str))
    return result


def _next_version(video_root, sequence: str, shot_code: str, variation: str) -> int:
    versions = _matching_versions(video_root, sequence, shot_code, variation)
    return (max(versions) + 1) if versions else 1


def _latest_version(video_root, sequence: str, shot_code: str, variation: str):
    versions = _matching_versions(video_root, sequence, shot_code, variation)
    return max(versions) if versions else None


def _next_index(video_root, sequence: str, shot_code: str, variation: str, version: int) -> int:
    indices = _matching_versions(video_root, sequence, shot_code, variation).get(version, [])
    return (max(indices) + 1) if indices else 1


def _resolve_filename_stem(video_root, sequence: str, shot_code: str, variation: str, *, is_image: bool) -> str:
    """SEQ_ShotCode_Variation_index_version, no extension. A video
    playblast always starts a brand-new version (index always "001" — one
    clip is the whole version). A current-frame image playblast instead
    reuses whichever version for this exact sequence/shot/variation
    already exists (creating v001 if this is the first playblast for it
    at all) and takes the next index within *that* version — confirmed
    with the user 2026-07-20: an image playblast "adds an index into the
    same version" rather than starting a new one, so a set of stills for
    one take ends up as v001 index 001, 002, 003... instead of each being
    its own version."""
    if is_image:
        version = _latest_version(video_root, sequence, shot_code, variation)
        if version is None:
            version = 1
        index = _next_index(video_root, sequence, shot_code, variation, version)
    else:
        version = _next_version(video_root, sequence, shot_code, variation)
        index = 1
    return "{}_{}_{}_{:03d}_v{:03d}".format(sequence, shot_code, variation, index, version)


def _finalize_single_frame_image(export_file_path: str, image_format: str) -> str:
    """cmds.playblast's format="image" always appends its own frame-number
    suffix to `filename` (e.g. "<export_file_path>.0001.<ext>"), even for
    a single-frame capture — there is no Maya flag to suppress it. Renamed
    to the exact target name afterward so this tool's own naming
    convention (SEQ_Shot_variation_index_version.ext) still holds for
    image output too. The produced file is located by globbing the
    directory for anything starting with the same base name rather than
    assuming an exact zero-padding width, since that padding isn't
    documented as stable across Maya versions."""
    directory = os.path.dirname(export_file_path)
    base_name = os.path.basename(export_file_path)
    target_path = "{}.{}".format(export_file_path, image_format)
    target_name = os.path.basename(target_path)
    candidates = sorted(
        name
        for name in os.listdir(directory)
        if name.startswith(base_name + ".") and name.endswith("." + image_format) and name != target_name
    )
    if candidates:
        os.replace(os.path.join(directory, candidates[0]), target_path)
    return target_path


def _resolve_camera_shape(panel):
    """Camera shape node currently assigned to `panel`, or None — used to
    toggle displayFilmGate off for the exact camera a playblast will
    actually capture through. Best-effort: any failure here just means the
    Film Gate auto-disable below no-ops, never blocks the playblast
    itself."""
    if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
        return None
    cam = cmds.modelPanel(panel, query=True, camera=True)
    if not cam or not cmds.objExists(cam):
        return None
    shapes = cmds.listRelatives(cam, shapes=True, fullPath=True) or []
    return shapes[0] if shapes else cam


def _disable_film_gate(camera_shape):
    """Turns displayFilmGate off for camera_shape if it's currently on,
    returning camera_shape (so the caller knows to restore it afterward)
    or None if there was nothing to restore. Swallows any Maya API error —
    a camera without the attribute, or any other resolution hiccup, just
    means Film Gate is left exactly as it was."""
    if not camera_shape:
        return None
    try:
        if not cmds.attributeQuery("displayFilmGate", node=camera_shape, exists=True):
            return None
        if not cmds.getAttr("{}.displayFilmGate".format(camera_shape)):
            return None
        cmds.setAttr("{}.displayFilmGate".format(camera_shape), False)
        return camera_shape
    except Exception:
        return None


def _restore_film_gate(camera_shape):
    if not camera_shape:
        return
    try:
        cmds.setAttr("{}.displayFilmGate".format(camera_shape), True)
    except Exception:
        pass


def _locate_video_output(export_file_path: str) -> str:
    """cmds.playblast's video formats each append their own extension to
    `filename`, and which exact extension a given format/compression combo
    actually produces is Maya-version-dependent (e.g. "qt"+"H.264" writes
    .mp4 on some Maya versions, .mov on others) — glob for whatever Maya
    actually wrote next to export_file_path rather than assuming, the same
    don't-assume-the-extension approach _finalize_single_frame_image already
    uses for image mode. Raises RuntimeError if nothing matches (playblast
    silently produced no file)."""
    directory = os.path.dirname(export_file_path)
    base_name = os.path.basename(export_file_path)
    candidates = sorted(name for name in os.listdir(directory) if name.startswith(base_name + "."))
    if not candidates:
        raise RuntimeError("Maya's playblast did not produce an output file at {}".format(export_file_path))
    return os.path.join(directory, candidates[0])


def resolve_destination_path():
    """Full file path (including extension) the next publish_playblast()
    call would write, without creating anything or running Maya's
    playblast — used by options_dialog.py's destination_label to preview
    exactly what a playblast right now would produce. Meaningful as a full
    *file* preview (not just a folder, like before 2026-07-20) now that
    the video root is flat and the filename itself is where all the
    shot/variation/index/version information lives. Video mode's extension
    here is a best-effort guess from options["format"] alone (there's no
    file on disk yet to glob, unlike _locate_video_output — Maya's actual
    written extension for "qt"+"H.264" can differ by Maya version), so the
    label may show ".qt" where the real saved file ends up ".mp4"/".mov";
    publish_playblast()'s own printed/in-view message always reflects the
    real path. Raises RuntimeError with the same human-readable reasons
    publish_playblast() itself would
    hit."""
    project, repo, _ = repo_paths.get_active_repo()
    if project is None:
        raise RuntimeError("No active repo selected in UkoreHub. Open UkoreHub, pick a project/repo, then try again.")
    video_root = _resolve_video_root(project.id, repo.id)
    options = options_store.get_options(project.id, repo.id)
    sequence, shot_code = _resolve_seq_shot(_current_scene_basename())
    variation = _sanitize_token(options.get("variation") or "layout")
    is_image = options.get("output_mode") == "current_frame_image"
    stem = _resolve_filename_stem(video_root, sequence, shot_code, variation, is_image=is_image)
    extension = (options.get("image_format") or "png") if is_image else options["format"]
    return video_root / "{}.{}".format(stem, extension)


def publish_playblast() -> None:
    print("[UkorePlayblast] Playblast started...")
    try:
        project, repo, _ = repo_paths.get_active_repo()
        if project is None:
            raise RuntimeError("No active repo selected in UkoreHub. Open UkoreHub, pick a project/repo, then try again.")

        video_root = _resolve_video_root(project.id, repo.id)

        options = options_store.get_options(project.id, repo.id)
        print("[UkorePlayblast] Options: {}".format(options))

        sequence, shot_code = _resolve_seq_shot(_current_scene_basename())
        variation = _sanitize_token(options.get("variation") or "layout")
        is_image = options.get("output_mode") == "current_frame_image"
        stem = _resolve_filename_stem(video_root, sequence, shot_code, variation, is_image=is_image)
        export_file_path = os.path.join(str(video_root), stem)
        print("[UkorePlayblast] Destination folder: {}".format(video_root))
        print("[UkorePlayblast] Filename: {}".format(stem))

        if options["resolution_mode"] == "custom":
            width = options["width"]
            height = options["height"]
        else:
            width = cmds.getAttr("defaultResolution.width")
            height = cmds.getAttr("defaultResolution.height")

        panel = cmds.getPanel(withFocus=True)
        if options["camera"] and panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            cmds.modelPanel(panel, edit=True, camera=options["camera"])

        # Auto-disable Film Gate for whichever camera is actually being
        # captured, restored afterward regardless of success/failure — a
        # playblast shouldn't leave the viewport's display state changed.
        film_gate_camera = _disable_film_gate(_resolve_camera_shape(panel))
        try:
            if is_image:
                # Current-frame-only capture — deliberately not the whole
                # timeline turned into an image sequence (confirmed with the
                # user 2026-07-20): startTime/endTime/frame all pinned to
                # cmds.currentTime so exactly one still comes out, no matter
                # what the saved frame_range_mode/start_frame/end_frame options
                # say (those only apply to the video output mode).
                current_time = cmds.currentTime(query=True)
                image_format = options.get("image_format") or "png"
                playblast_kwargs = {
                    "filename": export_file_path,
                    "format": "image",
                    "compression": image_format,
                    "qlt": options["quality"],
                    "width": width,
                    "height": height,
                    "percent": options["percent"],
                    "showOrnaments": options["show_ornaments"],
                    "offScreen": True,
                    "startTime": current_time,
                    "endTime": current_time,
                    "frame": [current_time],
                }
                cmds.playblast(**playblast_kwargs)
                saved_path = _finalize_single_frame_image(export_file_path, image_format)
            else:
                playblast_kwargs = {
                    "filename": export_file_path,
                    "format": options["format"],
                    "compression": options["compression"],
                    "qlt": options["quality"],
                    "width": width,
                    "height": height,
                    "percent": options["percent"],
                    "showOrnaments": options["show_ornaments"],
                    "offScreen": True,
                }

                if options["frame_range_mode"] == "custom":
                    playblast_kwargs["startTime"] = options["start_frame"]
                    playblast_kwargs["endTime"] = options["end_frame"]

                sound_node_name = ""
                if options["sound"]:
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
            _restore_film_gate(film_gate_camera)

        print("[UkorePlayblast] Playblast saved: {}".format(saved_path))
        message = "<hl>Playblast saved:</hl> {}".format(saved_path)
        cmds.inViewMessage(amg=message, pos="midCenter", fade=True)
    except Exception as e:
        print("[UkorePlayblast] Playblast failed: {}".format(e))
        cmds.confirmDialog(title="UkorePlayblast", message=str(e))
