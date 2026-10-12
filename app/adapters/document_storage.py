import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from app.errors import (
    DocumentStorageTooLargeError,
    DocumentStorageValidationError,
)


@dataclass(frozen=True)
class StoredDocument:
    storage_key: str
    size_bytes: int
    sha256: str


class DocumentStorage(Protocol):
    """Minimal boundary for managed document persistence."""

    def save(
        self,
        source: BinaryIO,
        suffix: str,
        *,
        namespace: str | None = None,
    ) -> StoredDocument: ...

    def open(self, storage_key: str) -> BinaryIO: ...

    def delete(self, storage_key: str) -> None: ...


class LocalDocumentStorage:
    """Store documents under generated keys inside one managed root."""

    def __init__(self, root: Path, max_size_bytes: int) -> None:
        self._root = root.resolve()
        self._max_size_bytes = max_size_bytes
        self._root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        source: BinaryIO,
        suffix: str,
        *,
        namespace: str | None = None,
    ) -> StoredDocument:
        normalized_suffix = suffix.casefold()
        if re.fullmatch(r"\.[a-z0-9]{1,10}", normalized_suffix) is None:
            raise DocumentStorageValidationError("Document file extension is invalid.")
        if (
            namespace is not None
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", namespace) is None
        ):
            raise DocumentStorageValidationError("Document storage namespace is invalid.")
        filename = f"{uuid4()}{normalized_suffix}"
        storage_key = f"{namespace}/{filename}" if namespace else filename
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.upload")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as destination:
                while chunk := source.read(64 * 1024):
                    size += len(chunk)
                    if size > self._max_size_bytes:
                        raise DocumentStorageTooLargeError(
                            "Document exceeds the configured upload limit."
                        )
                    digest.update(chunk)
                    destination.write(chunk)
            if size == 0:
                raise DocumentStorageValidationError("Document cannot be empty.")
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return StoredDocument(storage_key, size, digest.hexdigest())

    def open(self, storage_key: str) -> BinaryIO:
        return self.path_for(storage_key).open("rb")

    def path_for(self, storage_key: str) -> Path:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise DocumentStorageValidationError("Stored document is unavailable.")
        return path

    def delete(self, storage_key: str) -> None:
        self._resolve(storage_key).unlink(missing_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        if not storage_key or "\\" in storage_key:
            raise DocumentStorageValidationError("Invalid document storage key.")
        parts = storage_key.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise DocumentStorageValidationError("Invalid document storage key.")
        path = self._root.joinpath(*parts).resolve()
        try:
            path.relative_to(self._root)
        except ValueError as error:
            raise DocumentStorageValidationError("Invalid document storage key.") from error
        return path
