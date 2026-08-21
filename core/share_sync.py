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

_logger = logging.getLogger("UkoreShot.ShareSync")

_POINTER_PREFIX = "ukore_shot/share_codes"


def _blob_prefix(project_id: str, repo_id: str, stem: str) -> str:
    return "ukore_shot/{}/{}/{}".format(project_id, repo_id, stem)


def _write_temp_json(payload: dict) -> Path:
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(payload, handle)
    finally:
        handle.close()
    return Path(handle.name)


def push_pointer(cloud_sync, code: str, *, project_id: str, repo_id: str, video_stem: str, frame_count: int, image_format: str, fps: float) -> None:
    payload = {
        "project_id": project_id,
        "repo_id": repo_id,
        "video_stem": video_stem,
        "frame_count": frame_count,
        "image_format": image_format,
        "fps": fps,
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


class ShareUploadWorker(QThread):
    """One-shot upload of an already-extracted sequence + its comments.json
    to R2, then the pointer blob that makes the resulting code resolvable.
    Same one-shot-QThread shape as discord_send_worker.py's
    DiscordSendWorker. Doesn't generate/persist the share code itself —
    the caller (video_library_page.py's _on_mark_as_share_clicked) does that
    only after `succeeded` fires, then calls push_pointer separately once it
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
        _logger.info("ShareUploadWorker: uploading %s to %s", self._sequence_dir, prefix)
        try:
            pushed = 0
            for local_path in sorted(self._sequence_dir.iterdir()):
                if not local_path.is_file():
                    continue
                blob_name = "{}/{}".format(prefix, local_path.name)
                try:
                    self._cloud_sync.push(blob_name, local_path)
                    pushed += 1
                except ConflictError:
                    # Last-write-wins is correct for a shared asset, not a
                    # real conflict to surface — same reasoning
                    # launcher.py::_push_asset already documents for
                    # thumbnails/program_icons.
                    _logger.debug("ConflictError pushing %s — ignored (last-write-wins)", blob_name)
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
            for index in range(1, frame_count + 1):
                filename = "{}.{:05d}.{}".format(stem, index, image_format)
                self._cloud_sync.pull("{}/{}".format(prefix, filename), sequence_dir / filename)
            self._cloud_sync.pull(
                "{}/comments.json".format(prefix), comment_store.metadata_path(sequence_dir)
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception("PullByCodeWorker: pull failed for code %s", self._code)
            self.failed.emit(str(exc))
            return
        _logger.info("PullByCodeWorker: pull finished for %s", stem)
        self.succeeded.emit(stem)
