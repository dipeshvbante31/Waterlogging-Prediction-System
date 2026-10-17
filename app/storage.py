
"""Safe local artifact storage for datasets and trained models."""

from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import Any


class StorageError(RuntimeError):
    """Raised when an artifact cannot be stored, read, listed, or deleted."""


class ArtifactStore(ABC):
    """Interface shared by artifact storage implementations."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None:
        """Store bytes at the specified artifact key."""
        raise NotImplementedError

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        """Return the bytes for an artifact key."""
        raise NotImplementedError

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """List artifact keys matching a prefix."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete an artifact; return False if it does not exist."""
        raise NotImplementedError


class LocalArtifactStore(ArtifactStore):
    """Filesystem-backed artifact store with path traversal protection."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        try:
            self.root = Path(root).expanduser().resolve()
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Cannot initialize artifact storage: {exc}") from exc

    @staticmethod
    def _validate_key(key: str, *, prefix: bool = False) -> str:
        """Validate a POSIX-style artifact key or listing prefix."""
        if not isinstance(key, str):
            raise StorageError("Artifact key must be a string.")

        if key == "":
            if prefix:
                return ""
            raise StorageError("Artifact key cannot be empty.")

        # Artifact keys use forward slashes on every operating system.

        if (
            key.startswith("/")
            or "\\" in key
            or "\x00" in key
            or any(ch.isspace() for ch in key)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in key)
        ):
            raise StorageError("Invalid artifact key.")


        # A trailing slash is valid for directory/prefix listings only.
        normalized = key[:-1] if prefix and key.endswith("/") else key
        if not normalized:
            if prefix:
                return ""
            raise StorageError("Invalid artifact key.")

        parts = normalized.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise StorageError("Invalid artifact key.")

        # Prevent Windows drive/alternate-stream syntax in artifact names.
        if any(":" in part for part in parts):
            raise StorageError("Invalid artifact key.")

        return key

    def _path(self, key: str, *, prefix: bool = False) -> Path:
        """Resolve a key and ensure it remains inside the storage root."""
        key = self._validate_key(key, prefix=prefix)
        relative = key.rstrip("/") if prefix else key
        candidate = self.root if not relative else self.root / Path(*PurePosixPath(relative).parts)

        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise StorageError("Artifact path escapes the storage directory.") from exc

        return resolved

    def put_bytes(self, key: str, data: bytes) -> None:
        """Atomically write bytes to an artifact key."""
        path = self._path(key)

        if not isinstance(data, bytes):
            raise StorageError("Artifact data must be bytes.")

        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write in the destination directory so os.replace is atomic.
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=".artifact-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temp_path, path)
            temp_path = None
        except OSError as exc:
            raise StorageError(f"Cannot write artifact '{key}': {exc}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def get_bytes(self, key: str) -> bytes:
        """Read bytes from an artifact key."""
        path = self._path(key)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError(f"Cannot read artifact '{key}': {exc}") from exc
    
    def exists(self, key: str) -> bool:
        """Return whether an artifact file exists."""
        path = self._path(key)
        try:
            return path.is_file()
        except OSError as exc:
            raise StorageError(
                f"Cannot check artifact '{key}': {exc}"
            ) from exc

    def list(self, prefix: str = "") -> list[str]:
        """Return sorted, storage-relative file keys matching a prefix."""
        self._validate_key(prefix, prefix=True)

        if not self.root.exists():
            return []

        results: list[str] = []
        try:
            for path in self.root.rglob("*"):
                if not path.is_file():
                    continue

                try:
                    resolved = path.resolve()
                    resolved.relative_to(self.root)
                except (OSError, ValueError):
                    # Do not expose files reached through escaping symlinks.
                    continue

                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    results.append(key)
        except OSError as exc:
            raise StorageError(f"Cannot list artifacts: {exc}") from exc

        return sorted(results)

    def delete(self, key: str) -> bool:
        """Delete a file; return False if it does not exist."""
        path = self._path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError(f"Cannot delete artifact '{key}': {exc}") from exc


def get_store(settings: Any) -> ArtifactStore:
    """Build the configured artifact store from application settings."""
    backend = str(getattr(settings, "storage_backend", "local")).strip().lower()
    root = getattr(settings, "storage_path", None)

    if not root:
        raise StorageError("No storage_path is configured.")

    if backend in {"local", "filesystem", "file"}:
        return LocalArtifactStore(root)

    raise StorageError(
        f"Unsupported storage backend '{backend}'. "
        "Configure storage_backend='local' or implement the requested backend."
    )
