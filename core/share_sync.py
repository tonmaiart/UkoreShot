"""Background R2 push/pull for a shared video's image sequence + comment
metadata, plus the share-code lookup that lets a *different* machine pull a
shared video down again by pasting its code into the search bar.

api.cloud_sync (an already-built R2JsonSync | None) is the only sanctioned
way a plugin touches R2 — see plugin-api.md's "What's deliberately not
re-exported" section; core.vcs.cloud_sync.R2JsonSync itself is never
imported directly from here. R2JsonSync has no "list objects" operation
(confirmed via the ukorehub-cloud-sync skill — only get_object/put_object by
exact key), which is why a share needs an explicit pointer blob
(ukore_shot/share_codes/<code>.json) recording exactly which project/repo/
stem/frame_count/image_format a code maps to: without it, nothing could ever
resolve a pasted code back to the right blobs to pull, since there is no way
to enumerate what a bucket contains."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

# ConflictError via plugin_api (developer/app/docs/plugin-api.md's
# "Exceptions" re-export row) — this file is new, unlike comment_store.py's/
# draw_overlay.py's ported-from-history direct `core.*` imports (see those
# files' own naming notes for why those specific ones are grandfathered),
# so it has no reason to bypass the sanctioned plugin_api surface.
from plugin_api import ConflictError
from ukoreshot_plugin.core import comment_store
from ukoreshot_plugin.core.video_sequence import audio_path_for

_logger = logging.getLogger("UkoreShot.ShareSync")

_POINTER_PREFIX = "ukore_shot/share_codes"

# Every frame push/pull is its own R2 (S3-compatible) HTTP round-trip —
# boto3/botocore clients are documented thread-safe for concurrent calls,
# so running several at once (instead of one file at a time, sequentially)
# is a pure wall-clock win for a shot with hundreds of frames, bounded by
# network bandwidth rather than by waiting on each round-trip in turn.
# Kept modest rather than unbounded to avoid hammering R2 with a huge burst
# of simultaneous connections for a very long shot.
_MAX_CONCURRENT_TRANSFERS = 6


def _blob_prefix(project_id: str, repo_id: str, stem: str) -> str:
    return "ukore_shot/{}/{}/{}".format(project_id, repo_id, stem)


def _write_temp_json(payload: dict) -> Path:
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(payload, handle)
    finally:
        handle.close()
    return Path(handle.name)


def push_pointer(cloud_sync, code: str, *, project_id: str, repo_id: str, video_stem: str, frame_count: int, image_format: str, fps: float, audio_format: str | None = None) -> None:
    payload = {
        "project_id": project_id,
        "repo_id": repo_id,
        "video_stem": video_stem,
        "frame_count": frame_count,
        "image_format": image_format,
        "fps": fps,
        "audio_format": audio_format,
    }
    temp_path = _write_temp_json(payload)
    try:
        cloud_sync.push("{}/{}.json".format(_POINTER_PREFIX, code), temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def pull_pointer(cloud_sync, code: str) -> dict | None:
    """None for an unknown code (a 404 from R2JsonSync.pull is non-fatal —
    see the cloud-sync skill) — callers treat that as "not found", not an
    error."""
    temp_path = Path(tempfile.mktemp(suffix=".json"))
    try:
        cloud_sync.pull("{}/{}.json".format(_POINTER_PREFIX, code), temp_path)
        if not temp_path.is_file():
            return None
        return json.loads(temp_path.read_text(encoding="utf-8"))
    finally:
        temp_path.unlink(missing_ok=True)


_MAX_CODE_GENERATION_ATTEMPTS = 10


def generate_unique_share_code(cloud_sync, shot_code: str, version: int) -> str:
    """comment_store.generate_share_code's own 4-hex-char random suffix
    makes an accidental collision with a code already pushed to the cloud
    astronomically unlikely (1 in 65536 for the same shot_code+version
    alone) but not impossible, and R2JsonSync has no way to *enumerate*
    what's already in the bucket to rule collisions out up front (see this
    module's own docstring) — so this checks each freshly-generated
    candidate directly against the cloud (pull_pointer, which returns None
    for a code nobody's pushed a pointer for yet) before accepting it,
    retrying with a fresh random suffix on an actual collision. Called
    synchronously on the UI thread from video_library_page.py's
    _on_mark_as_share_clicked, same as every other blocking cloud_sync
    call already made there before the async ShareUploadWorker starts.
    Raises RuntimeError if _MAX_CODE_GENERATION_ATTEMPTS genuinely unlucky
    retries in a row all collided — practically unreachable, but better
    than looping forever if it somehow ever did."""
    for _ in range(_MAX_CODE_GENERATION_ATTEMPTS):
        code = comment_store.generate_share_code(shot_code, version)
        if pull_pointer(cloud_sync, code) is None:
            return code
        _logger.warning("generate_unique_share_code: collision on %s, retrying", code)
    raise RuntimeError(
        "Could not generate a unique share code after {} attempts — this should never happen.".format(
            _MAX_CODE_GENERATION_ATTEMPTS
        )
    )


class ShareUploadWorker(QThread):
    """One-shot upload of an already-extracted sequence + its comments.json
    to R2, then the pointer blob that makes the resulting code resolvable.
    Same one-shot-QThread shape as CommentSyncWorker/PullByCodeWorker below.
    Doesn't generate/persist the share code itself — the caller
    (video_library_page.py's _on_mark_as_share_clicked) does that only
    after `succeeded` fires, then calls push_pointer separately once it
    knows the code."""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, cloud_sync, *, project_id: str, repo_id: str, sequence_dir: Path, parent=None):
        super().__init__(parent)
        self._cloud_sync = cloud_sync
        self._project_id = project_id
        self._repo_id = repo_id
        self._sequence_dir = sequence_dir

    def run(self) -> None:
        prefix = _blob_prefix(self._project_id, self._repo_id, self._sequence_dir.name)
        local_paths = [p for p in sorted(self._sequence_dir.iterdir()) if p.is_file()]
        _logger.info(
            "ShareUploadWorker: uploading %d file(s) from %s to %s", len(local_paths), self._sequence_dir, prefix
        )

        def _push_one(local_path: Path) -> bool:
            """True on a real push, False for a ConflictError (last-write-
            wins is correct for a shared asset, not a real conflict to
            surface — same reasoning launcher.py::_push_asset already
            documents for thumbnails/program_icons) — either way, not an
            error."""
            blob_name = "{}/{}".format(prefix, local_path.name)
            try:
                self._cloud_sync.push(blob_name, local_path)
                return True
            except ConflictError:
                _logger.debug("ConflictError pushing %s — ignored (last-write-wins)", blob_name)
                return False

        try:
            pushed = 0
            # Concurrent, not one file at a time — see _MAX_CONCURRENT_
            # TRANSFERS. Every upload still runs to completion even if one
            # fails (a deliberate change from the old sequential version's
            # "stop at the first failure" — cancelling in-flight uploads
            # here would just leave a partially-uploaded share on R2 for no
            # real benefit); the first real error seen is what actually
            # gets raised/reported below.
            first_error: Exception | None = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_TRANSFERS) as pool:
                futures = {pool.submit(_push_one, local_path): local_path for local_path in local_paths}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        if future.result():
                            pushed += 1
                    except Exception as exc:  # noqa: BLE001 - collected below, every other in-flight upload still finishes
                        _logger.exception("ShareUploadWorker: failed to push %s", futures[future])
                        if first_error is None:
                            first_error = exc
            if first_error is not None:
                raise first_error
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a message, never crashes
            _logger.exception("ShareUploadWorker failed for %s", self._sequence_dir)
            self.failed.emit(str(exc))
            return
        _logger.info("ShareUploadWorker: pushed %d file(s) for %s", pushed, self._sequence_dir)
        self.succeeded.emit()


class CommentSyncWorker(QThread):
    """Single-file variant — pushes just the current comments.json for a
    video that's already shared. Used by comment_editor.py's Save handler
    (confirmed with the user this round: saving a comment on an
    already-shared video should incrementally sync the comment data, not
    require a fresh Mark as Share) — no need to re-walk frame files, they
    didn't change."""

    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, cloud_sync, *, project_id: str, repo_id: str, sequence_dir: Path, parent=None):
        super().__init__(parent)
        self._cloud_sync = cloud_sync
        self._project_id = project_id
        self._repo_id = repo_id
        self._sequence_dir = sequence_dir

    def run(self) -> None:
        blob_name = "{}/{}".format(
            _blob_prefix(self._project_id, self._repo_id, self._sequence_dir.name),
            comment_store.metadata_path(self._sequence_dir).name,
        )
        _logger.info("CommentSyncWorker: pushing %s", blob_name)
        try:
            try:
                self._cloud_sync.push(blob_name, comment_store.metadata_path(self._sequence_dir))
            except ConflictError:
                _logger.debug("ConflictError pushing %s — ignored (last-write-wins)", blob_name)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("CommentSyncWorker failed for %s", self._sequence_dir)
            self.failed.emit(str(exc))
            return
        self.succeeded.emit()


class PullByCodeWorker(QThread):
    """Resolves a pasted share code to its pointer, then pulls every frame
    (reconstructed by exact filename from frame_count/image_format — no
    "list" call needed, see the module docstring) plus comments.json into
    video_root/<stem>/ locally."""

    succeeded = Signal(str)  # video_stem
    not_found = Signal()
    failed = Signal(str)

    def __init__(self, cloud_sync, code: str, *, video_root: Path, parent=None):
        super().__init__(parent)
        self._cloud_sync = cloud_sync
        self._code = code
        self._video_root = video_root

    def run(self) -> None:
        _logger.info("PullByCodeWorker: resolving code %s", self._code)
        try:
            pointer = pull_pointer(self._cloud_sync, self._code)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("PullByCodeWorker: pull_pointer failed for code %s", self._code)
            self.failed.emit(str(exc))
            return
        if pointer is None:
            _logger.info("PullByCodeWorker: no pointer blob found for code %s", self._code)
            self.not_found.emit()
            return
        try:
            stem = pointer["video_stem"]
            image_format = pointer["image_format"]
            frame_count = pointer["frame_count"]
            prefix = _blob_prefix(pointer["project_id"], pointer["repo_id"], stem)
            _logger.info("PullByCodeWorker: pulling %s (%d frame(s)) from %s", stem, frame_count, prefix)
            sequence_dir = self._video_root / stem
            sequence_dir.mkdir(parents=True, exist_ok=True)

            def _pull_frame(index: int) -> None:
                filename = "{}.{:05d}.{}".format(stem, index, image_format)
                self._cloud_sync.pull("{}/{}".format(prefix, filename), sequence_dir / filename)

            # Concurrent, not one frame at a time — same reasoning as
            # ShareUploadWorker's own _MAX_CONCURRENT_TRANSFERS above; every
            # frame still gets attempted even if one fails, and the first
            # real error seen is what actually gets raised below.
            first_error: Exception | None = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_TRANSFERS) as pool:
                futures = {pool.submit(_pull_frame, index): index for index in range(1, frame_count + 1)}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001 - collected below, every other in-flight pull still finishes
                        _logger.exception("PullByCodeWorker: failed to pull frame %d for %s", futures[future], stem)
                        if first_error is None:
                            first_error = exc
            if first_error is not None:
                raise first_error
            self._cloud_sync.pull(
                "{}/comments.json".format(prefix), comment_store.metadata_path(sequence_dir)
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("PullByCodeWorker: pull failed for code %s", self._code)
            self.failed.emit(str(exc))
            return
        # Best-effort, separate from the try/except above: an older
        # pointer pushed before audio support existed simply has no
        # "audio_format" key (pointer.get returns None, nothing to pull),
        # and a failed audio pull shouldn't fail the whole sync — the
        # frames + comments already landed, which is what actually matters.
        audio_format = pointer.get("audio_format")
        if audio_format:
            try:
                audio_path = audio_path_for(sequence_dir, stem)
                self._cloud_sync.pull("{}/{}".format(prefix, audio_path.name), audio_path)
            except Exception:  # noqa: BLE001
                _logger.warning("PullByCodeWorker: audio pull failed for code %s", self._code, exc_info=True)
        _logger.info("PullByCodeWorker: pull finished for %s", stem)
        self.succeeded.emit(stem)
