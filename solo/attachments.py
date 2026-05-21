"""Attachment persistence helpers for the solo workspace."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Any


@dataclass(frozen=True)
class StoredAttachment:
    """A durable reference to one inbound attachment copied into a workspace."""

    kind: str
    original_name: str
    source_path: str
    stored_path: str
    media_type: str
    size_bytes: int
    sha256: str
    captured_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredAttachment":
        return cls(
            kind=str(data.get("kind") or "file"),
            original_name=str(data.get("original_name") or ""),
            source_path=str(data.get("source_path") or ""),
            stored_path=str(data.get("stored_path") or ""),
            media_type=str(data.get("media_type") or "application/octet-stream"),
            size_bytes=int(data.get("size_bytes") or 0),
            sha256=str(data.get("sha256") or ""),
            captured_at=str(data.get("captured_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "original_name": self.original_name,
            "source_path": self.source_path,
            "stored_path": self.stored_path,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "captured_at": self.captured_at,
        }


def persist_attachment_paths(
    media_paths: list[str],
    *,
    workspace_root: Path,
    attachments_root: Path,
    entry_id: str,
    captured_at: str,
) -> list[StoredAttachment]:
    """Copy inbound attachments into the app workspace and return durable refs."""
    if not media_paths:
        return []

    target_dir = attachments_root / "entries" / entry_id
    target_dir.mkdir(parents=True, exist_ok=True)

    stored: list[StoredAttachment] = []
    for index, raw_path in enumerate(media_paths, start=1):
        source = Path(raw_path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Attachment is not a file: {source}")

        filename = _stored_filename(index, source.name)
        destination = target_dir / filename
        shutil.copy2(source, destination)

        media_type = mimetypes.guess_type(str(source))[0] or "application/octet-stream"
        stored.append(
            StoredAttachment(
                kind=_attachment_kind(media_type),
                original_name=source.name,
                source_path=str(source),
                stored_path=str(destination.relative_to(workspace_root)),
                media_type=media_type,
                size_bytes=source.stat().st_size,
                sha256=_sha256_file(source),
                captured_at=captured_at,
            )
        )
    return stored


def resolve_stored_attachment_path(
    workspace_root: str | Path,
    attachment: StoredAttachment,
) -> Path:
    """Resolve a stored attachment path against the owning workspace."""
    return Path(workspace_root).expanduser().resolve() / attachment.stored_path


def _attachment_kind(media_type: str) -> str:
    lowered = media_type.lower()
    if lowered.startswith("image/"):
        return "image"
    if lowered.startswith("audio/"):
        return "audio"
    if lowered.startswith("video/"):
        return "video"
    return "file"


def _stored_filename(index: int, original_name: str) -> str:
    name = original_name.strip() or f"attachment-{index}"
    cleaned = re.sub(r"[\x00-\x1f/\\\\]+", "_", name)
    return f"{index:02d}-{cleaned}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
