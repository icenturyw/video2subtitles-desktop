#!/usr/bin/env python3
"""Create a safe source-code package for sharing with an AI assistant.

The archive intentionally excludes local caches, downloaded models, virtual
Python environments, media outputs, cookies, credentials, private voices, and
other files that are either sensitive or too large for code review.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_OUTPUT_NAME = "video2subtitles-source-package.zip"

# Directory names that should never be included in a shareable source package.
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "site-packages",
    "models",
    "model",
    "checkpoints",
    "checkpoint",
    "weights",
    "output",
    "outputs",
    "downloads",
    "download",
    "cache",
    "temp",
    "tmp",
    "logs",
    "log",
    "cookie_backups",
    "private",
    ".ai-bridge",
    ".chatgpt-git-mcp",
}

# Project-specific directories or subtrees that often hold runtime artifacts.
EXCLUDED_PATH_GLOBS = {
    "whisper-server/cache/**",
    "whisper-server/temp/**",
    "whisper-server/cookie_backups/**",
    "localization-engine/cache/**",
    "localization-engine/temp/**",
    "localization-engine/data/**",
    "localization-engine/models/**",
    "qwen3-tts-engine/cache/**",
    "qwen3-tts-engine/temp/**",
    "qwen3-tts-engine/models/**",
    "qwen3-tts-engine/voices/private/**",
    "localization_workspace/**",
    "chatgpt_package/**",
}

# File name patterns that are usually credentials, local config, or runtime data.
EXCLUDED_FILE_GLOBS = {
    ".env",
    ".env.*",
    "*.env",
    "*.local",
    "*.local.*",
    "*.secret",
    "*.secrets",
    "*secret*",
    "*token*",
    "*apikey*",
    "*api_key*",
    "*access_key*",
    "*credential*",
    "*credentials*",
    "cookies.txt",
    "*.cookie",
    "*.cookies",
    "*.pem",
    "*.key",
    "*.crt",
    "*.cer",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "package_manifest.json",
    "*.log",
    "*.tmp",
    "*.bak",
    "*.pid",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.zip",
    "*.7z",
    "*.rar",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.gz",
    "*.bz2",
    "*.xz",
    "*.mp4",
    "*.mkv",
    "*.mov",
    "*.avi",
    "*.webm",
    "*.mp3",
    "*.wav",
    "*.m4a",
    "*.aac",
    "*.flac",
    "*.ogg",
    "*.opus",
    "*.safetensors",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.onnx",
    "*.bin",
    "*.gguf",
    "*.ggml",
    "*.ct2",
}

# Image files are normally not needed for code fixes; include them explicitly if needed.
MEDIA_FILE_GLOBS = {
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.bmp",
    "*.ico",
}

# Files that are safe and useful even if they match a broad pattern above.
ALWAYS_INCLUDE = {
    ".env.example",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
}


@dataclass
class PackageStats:
    included_files: int = 0
    included_bytes: int = 0
    skipped_files: int = 0
    skipped_dirs: int = 0
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    skipped_samples: list[dict[str, str]] = field(default_factory=list)

    def skip(self, rel_path: str, reason: str) -> None:
        self.skipped_files += 1
        self.skipped_by_reason[reason] = self.skipped_by_reason.get(reason, 0) + 1
        if len(self.skipped_samples) < 80:
            self.skipped_samples.append({"path": rel_path, "reason": reason})

    def skip_dir(self, rel_path: str, reason: str) -> None:
        self.skipped_dirs += 1
        self.skipped_by_reason[reason] = self.skipped_by_reason.get(reason, 0) + 1
        if len(self.skipped_samples) < 80:
            self.skipped_samples.append({"path": rel_path + "/", "reason": reason})


def _as_posix(path: Path) -> str:
    normalized = path.as_posix()
    if normalized == ".":
        return ""
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    normalized = value.replace("\\", "/")
    name = Path(normalized).name
    return any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def should_skip_dir(rel_dir: str, dir_name: str) -> tuple[bool, str]:
    lowered_name = dir_name.lower()
    lowered_rel = rel_dir.lower().replace("\\", "/")
    if lowered_name in EXCLUDED_DIR_NAMES:
        return True, "excluded directory"
    if _matches_any(lowered_rel, EXCLUDED_PATH_GLOBS):
        return True, "excluded runtime path"
    return False, ""


def should_skip_file(
    rel_file: str,
    file_path: Path,
    *,
    output_path: Path,
    include_media: bool,
    max_file_bytes: int,
) -> tuple[bool, str]:
    rel_norm = rel_file.replace("\\", "/")
    rel_lower = rel_norm.lower()
    name_lower = file_path.name.lower()

    if rel_norm in ALWAYS_INCLUDE or file_path.name in ALWAYS_INCLUDE:
        return False, ""

    if _is_relative_to(file_path, output_path.parent) and file_path.resolve() == output_path.resolve():
        return True, "output archive"

    if _matches_any(rel_lower, EXCLUDED_PATH_GLOBS):
        return True, "excluded runtime path"
    if _matches_any(name_lower, EXCLUDED_FILE_GLOBS) or _matches_any(rel_lower, EXCLUDED_FILE_GLOBS):
        return True, "excluded file pattern"
    if not include_media and (_matches_any(name_lower, MEDIA_FILE_GLOBS) or _matches_any(rel_lower, MEDIA_FILE_GLOBS)):
        return True, "media file"

    try:
        size = file_path.stat().st_size
    except OSError:
        return True, "unreadable"
    if size > max_file_bytes:
        return True, f"larger than {max_file_bytes} bytes"
    return False, ""


def iter_package_files(
    root: Path,
    *,
    output_path: Path,
    include_media: bool,
    max_file_bytes: int,
    stats: PackageStats,
) -> Iterable[Path]:
    for current_root, dir_names, file_names in os.walk(root):
        current = Path(current_root)
        rel_current = _as_posix(current.relative_to(root)) if current != root else ""

        kept_dirs: list[str] = []
        for dir_name in sorted(dir_names):
            rel_dir = f"{rel_current}/{dir_name}" if rel_current else dir_name
            skip, reason = should_skip_dir(rel_dir, dir_name)
            if skip:
                stats.skip_dir(rel_dir, reason)
            else:
                kept_dirs.append(dir_name)
        dir_names[:] = kept_dirs

        for file_name in sorted(file_names):
            file_path = current / file_name
            rel_file = _as_posix(file_path.relative_to(root))
            skip, reason = should_skip_file(
                rel_file,
                file_path,
                output_path=output_path,
                include_media=include_media,
                max_file_bytes=max_file_bytes,
            )
            if skip:
                stats.skip(rel_file, reason)
                continue
            yield file_path


def build_manifest(root: Path, output_path: Path, stats: PackageStats, args: argparse.Namespace) -> dict:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "archive": str(output_path),
        "script": "tools/package_source.py",
        "include_media": bool(args.include_media),
        "max_file_mb": float(args.max_file_mb),
        "included_files": stats.included_files,
        "included_bytes": stats.included_bytes,
        "skipped_files": stats.skipped_files,
        "skipped_dirs": stats.skipped_dirs,
        "skipped_by_reason": dict(sorted(stats.skipped_by_reason.items())),
        "skipped_samples": stats.skipped_samples,
        "note": "Review this package before sharing. The script excludes common secrets and large artifacts, but custom secret file names should still be checked manually.",
    }


def package_source(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"ERROR: root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2

    output_path = Path(args.output).resolve() if args.output else (root / DEFAULT_OUTPUT_NAME).resolve()
    max_file_bytes = int(float(args.max_file_mb) * 1024 * 1024)
    stats = PackageStats()

    files = list(iter_package_files(
        root,
        output_path=output_path,
        include_media=args.include_media,
        max_file_bytes=max_file_bytes,
        stats=stats,
    ))

    if args.dry_run:
        print("Dry run: no archive created.")
        print(f"Root: {root}")
        print(f"Would write: {output_path}")
        print(f"Files to include: {len(files)}")
        print(f"Skipped files: {stats.skipped_files}, skipped dirs: {stats.skipped_dirs}")
        for file_path in files[: args.list_limit]:
            print("  +", _as_posix(file_path.relative_to(root)))
        if len(files) > args.list_limit:
            print(f"  ... {len(files) - args.list_limit} more")
        print("Skipped summary:")
        for reason, count in sorted(stats.skipped_by_reason.items()):
            print(f"  - {reason}: {count}")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file_path in files:
            rel_file = _as_posix(file_path.relative_to(root))
            try:
                archive.write(file_path, rel_file)
                size = file_path.stat().st_size
                stats.included_files += 1
                stats.included_bytes += size
            except OSError as exc:
                stats.skip(rel_file, f"read failed: {exc}")

        manifest = build_manifest(root, output_path, stats, args)
        archive.writestr("PACKAGE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"Created: {output_path}")
    print(f"Included files: {stats.included_files}")
    print(f"Included size: {stats.included_bytes / 1024:.1f} KiB")
    print(f"Skipped files: {stats.skipped_files}, skipped dirs: {stats.skipped_dirs}")
    print("Skipped summary:")
    for reason, count in sorted(stats.skipped_by_reason.items()):
        print(f"  - {reason}: {count}")
    print("\nBefore sharing, quickly inspect PACKAGE_MANIFEST.json inside the zip.")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package source code safely for AI-assisted review/editing.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root to package. Default: current directory.",
    )
    parser.add_argument(
        "--output",
        default="",
        help=f"Output zip path. Default: ./{DEFAULT_OUTPUT_NAME}",
    )
    parser.add_argument(
        "--max-file-mb",
        type=float,
        default=8.0,
        help="Skip individual files larger than this size. Default: 8 MB.",
    )
    parser.add_argument(
        "--include-media",
        action="store_true",
        help="Include image files such as PNG/JPG. Video/audio/model files are still excluded.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be included without creating the zip.",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=120,
        help="Maximum included files to print during --dry-run. Default: 120.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return package_source(args)


if __name__ == "__main__":
    raise SystemExit(main())
