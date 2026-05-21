"""Personal memory helpers for ~/.wolo."""

from __future__ import annotations

from pathlib import Path
from re import sub

from openharness.memory.scan import scan_memory_files
from openharness.memory.schema import (
    SCHEMA_VERSION,
    coerce_int,
    compute_memory_signature,
    first_content_line,
    format_datetime,
    generate_memory_id,
    memory_metadata_from_path,
    render_memory_file,
    split_memory_file,
    utc_now,
)
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

from wolo.core.workspace import get_memory_dir, get_memory_index_path


def list_memory_files(workspace: str | Path | None = None) -> list[Path]:
    """List wolo memory markdown files."""
    memory_dir = get_memory_dir(workspace)
    if not memory_dir.exists():
        return []
    return sorted(
        header.path
        for header in scan_memory_files(
            _scan_cwd(workspace, memory_dir),
            max_files=None,
            memory_dir=memory_dir,
        )
    )


def add_memory_entry(workspace: str | Path | None, title: str, content: str) -> Path:
    """Create a memory file and append it to MEMORY.md index."""
    memory_dir = get_memory_dir(workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"
    with exclusive_file_lock(memory_dir / ".memory.lock"):
        memory_type = "personal"
        category = "preference"
        body = content.strip() + "\n"
        signature = compute_memory_signature(body, memory_type, category)
        existing = scan_memory_files(
            _scan_cwd(workspace, memory_dir),
            max_files=None,
            include_disabled=True,
            include_expired=True,
            memory_dir=memory_dir,
        )
        duplicate = next(
            (h for h in existing if _effective_signature(h.path, h.signature) == signature),
            None,
        )
        if duplicate is not None:
            path = duplicate.path
        else:
            # Prefer updating the base slug file in-place over creating _2/_3 variants.
            base_path = memory_dir / f"{slug}.md"
            slug_match = next((h for h in existing if h.path == base_path), None)
            path = slug_match.path if slug_match is not None else _next_memory_path(memory_dir, slug)
        now = utc_now()
        now_text = format_datetime(now)
        if path.exists():
            metadata, old_body, _, _ = split_memory_file(path.read_text(encoding="utf-8"))
            metadata = memory_metadata_from_path(
                path,
                metadata,
                old_body,
                now=now,
                source=str(metadata.get("source") or "manual"),
                default_type=memory_type,
                default_category=category,
            )
            created_at = str(metadata.get("created_at") or now_text)
            memory_id = str(metadata.get("id") or generate_memory_id(now))
        else:
            metadata = {}
            created_at = now_text
            memory_id = generate_memory_id(now)
        metadata.update(
            {
                "schema_version": SCHEMA_VERSION,
                "id": memory_id,
                "name": title.strip(),
                "description": first_content_line(body) or title.strip(),
                "type": str(metadata.get("type") or memory_type),
                "category": str(metadata.get("category") or category),
                "importance": max(coerce_int(metadata.get("importance"), default=0), 1),
                "source": "manual",
                "signature": signature,
                "created_at": created_at,
                "updated_at": now_text,
                "ttl_days": metadata.get("ttl_days"),
                "disabled": False,
                "supersedes": metadata.get("supersedes") or [],
            }
        )
        atomic_write_text(path, render_memory_file(metadata, body))

        index_path = get_memory_index_path(workspace)
        existing_index = index_path.read_text(encoding="utf-8") if index_path.exists() else "# Memory Index\n"
        if path.name not in existing_index:
            existing_index = existing_index.rstrip() + f"\n- [{title}]({path.name})\n"
            atomic_write_text(index_path, existing_index)
    return path


def remove_memory_entry(workspace: str | Path | None, name: str) -> bool:
    """Soft-delete a memory file and remove its index entry."""
    memory_dir = get_memory_dir(workspace)
    if not memory_dir.exists():
        return False
    matches = [
        header
        for header in scan_memory_files(
            _scan_cwd(workspace, memory_dir),
            max_files=None,
            include_disabled=True,
            include_expired=True,
            memory_dir=memory_dir,
        )
        if name in {header.path.stem, header.path.name, header.title, header.id}
    ]
    if not matches:
        return False
    header = matches[0]
    if header.disabled:
        return False
    path = header.path
    with exclusive_file_lock(memory_dir / ".memory.lock"):
        content = path.read_text(encoding="utf-8")
        metadata, body, _, _ = split_memory_file(content)
        metadata = memory_metadata_from_path(
            path,
            metadata,
            body,
            source="manual",
            default_type="personal",
            default_category="preference",
        )
        metadata["disabled"] = True
        metadata["updated_at"] = format_datetime(utc_now())
        atomic_write_text(path, render_memory_file(metadata, body))

        index_path = get_memory_index_path(workspace)
        if index_path.exists():
            lines = [
                line
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if path.name not in line
            ]
            atomic_write_text(index_path, "\n".join(lines).rstrip() + "\n")
    return True


def load_memory_prompt(workspace: str | Path | None = None, *, max_files: int = 8) -> str | None:
    """Return a prompt section containing personal memory content, or None if empty."""
    memory_dir = get_memory_dir(workspace)
    index_path = get_memory_index_path(workspace)

    if not memory_dir.exists() and not index_path.exists():
        return None

    lines: list[str] = [
        "# Personal Memory",
        "Each file below may start with a YAML frontmatter block (`---`). "
        "That block is structural metadata managed automatically — focus on the content below it.",
    ]

    if index_path.exists():
        index_lines = index_path.read_text(encoding="utf-8").splitlines()[:200]
        lines.extend(["", "## MEMORY.md", "```md", *index_lines, "```"])

    for path in list_memory_files(workspace)[:max_files]:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue
        lines.extend(["", f"## {path.name}", "```md", content[:4000], "```"])

    return "\n".join(lines) if len(lines) > 1 else None


def _scan_cwd(workspace: str | Path | None, memory_dir: Path) -> Path:
    return Path(workspace) if workspace is not None else memory_dir.parent


def _next_memory_path(memory_dir: Path, slug: str) -> Path:
    path = memory_dir / f"{slug}.md"
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = memory_dir / f"{slug}_{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def _effective_signature(path: Path, existing_signature: str) -> str:
    if existing_signature:
        return existing_signature
    try:
        metadata, body, _, _ = split_memory_file(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    memory_type = str(metadata.get("type") or "personal")
    category = str(metadata.get("category") or "preference")
    return compute_memory_signature(body, memory_type, category)
