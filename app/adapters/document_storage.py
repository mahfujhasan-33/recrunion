import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.errors import CompanyDocumentTooLargeError, CompanyDocumentValidationError


@dataclass(frozen=True)
class StoredUpload:
    storage_key: str
    size_bytes: int
    sha256: str


class LocalDocumentStorage:
    """Store uploaded company files under generated, non-user-controlled keys."""

    def __init__(self, root: Path, max_size_bytes: int) -> None:
        self._root = root.resolve()
        self._max_size_bytes = max_size_bytes
        self._root.mkdir(parents=True, exist_ok=True)

    def store(self, source: BinaryIO, suffix: str) -> StoredUpload:
        storage_key = f"{uuid4()}{suffix.casefold()}"
        target = self._resolve(storage_key)
        temporary = self._resolve(f".{storage_key}.upload")
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as destination:
                while chunk := source.read(64 * 1024):
                    size += len(chunk)
                    if size > self._max_size_bytes:
                        raise CompanyDocumentTooLargeError(
                            "Company document exceeds the configured upload limit."
                        )
                    digest.update(chunk)
                    destination.write(chunk)
            if size == 0:
                raise CompanyDocumentValidationError("Company document cannot be empty.")
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return StoredUpload(storage_key, size, digest.hexdigest())

    def path_for(self, storage_key: str) -> Path:
        path = self._resolve(storage_key)
        if not path.is_file():
            raise CompanyDocumentValidationError("Stored company document is unavailable.")
        return path

    def delete(self, storage_key: str) -> None:
        self._resolve(storage_key).unlink(missing_ok=True)

    def _resolve(self, storage_key: str) -> Path:
        if Path(storage_key).name != storage_key:
            raise CompanyDocumentValidationError("Invalid document storage key.")
        path = (self._root / storage_key).resolve()
        if path.parent != self._root:
            raise CompanyDocumentValidationError("Invalid document storage key.")
        return path
