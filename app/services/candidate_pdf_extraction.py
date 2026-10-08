import re
from dataclasses import dataclass
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.errors import CandidateDocumentValidationError


@dataclass(frozen=True)
class ExtractedCVPage:
    page_number: int
    text: str


class CandidatePDFExtractor:
    """Extract normalized CV text page-by-page without performing OCR."""

    def extract(self, source: BinaryIO) -> list[ExtractedCVPage]:
        try:
            source.seek(0)
            reader = PdfReader(source, strict=False)
            if reader.is_encrypted:
                raise CandidateDocumentValidationError(
                    "Encrypted candidate documents cannot be processed."
                )
            if not reader.pages:
                raise CandidateDocumentValidationError(
                    "The candidate document does not contain any pages."
                )
            return [
                ExtractedCVPage(
                    page_number=index,
                    text=self._normalize(page.extract_text() or ""),
                )
                for index, page in enumerate(reader.pages, start=1)
            ]
        except CandidateDocumentValidationError:
            raise
        except (PdfReadError, OSError, ValueError) as error:
            raise CandidateDocumentValidationError(
                "The candidate PDF could not be processed."
            ) from error

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = text.replace("\x00", "")
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()
