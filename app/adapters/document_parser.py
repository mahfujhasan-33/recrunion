import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.errors import CompanyDocumentValidationError

SUPPORTED_DOCUMENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".txt": {"text/plain"},
}


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    page_number: int | None = None
    section_title: str | None = None


class CompanyDocumentParser:
    """Validate and extract text from the supported company document formats."""

    def validate(self, path: Path, suffix: str, content_type: str) -> None:
        allowed_types = SUPPORTED_DOCUMENT_TYPES.get(suffix.casefold())
        if allowed_types is None or content_type.casefold() not in allowed_types:
            raise CompanyDocumentValidationError(
                "Supported company document types are PDF, DOCX, and UTF-8 TXT."
            )
        if suffix.casefold() == ".pdf" and not path.read_bytes()[:5] == b"%PDF-":
            raise CompanyDocumentValidationError("Uploaded file is not a valid PDF.")
        if suffix.casefold() == ".docx":
            self._validate_docx(path)
        if suffix.casefold() == ".txt":
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise CompanyDocumentValidationError("Text documents must use UTF-8.") from error

    def extract(self, path: Path) -> list[ExtractedSection]:
        suffix = path.suffix.casefold()
        if suffix == ".pdf":
            sections = self._extract_pdf(path)
        elif suffix == ".docx":
            sections = self._extract_docx(path)
        elif suffix == ".txt":
            sections = [ExtractedSection(self._normalize(path.read_text(encoding="utf-8")))]
        else:
            raise CompanyDocumentValidationError("Unsupported company document type.")
        if not any(section.text for section in sections):
            raise CompanyDocumentValidationError(
                "The document does not contain extractable text; OCR is not supported."
            )
        return [section for section in sections if section.text]

    @staticmethod
    def _validate_docx(path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise CompanyDocumentValidationError("Uploaded file is not a valid DOCX.")
                total_uncompressed = sum(item.file_size for item in archive.infolist())
                if total_uncompressed > 50 * 1024 * 1024:
                    raise CompanyDocumentValidationError("DOCX expanded content is too large.")
        except zipfile.BadZipFile as error:
            raise CompanyDocumentValidationError("Uploaded file is not a valid DOCX.") from error

    def _extract_pdf(self, path: Path) -> list[ExtractedSection]:
        try:
            reader = PdfReader(path)
            return [
                ExtractedSection(self._normalize(page.extract_text() or ""), page_number=index)
                for index, page in enumerate(reader.pages, start=1)
            ]
        except Exception as error:
            raise CompanyDocumentValidationError("The PDF could not be parsed.") from error

    def _extract_docx(self, path: Path) -> list[ExtractedSection]:
        try:
            document = Document(path)
        except Exception as error:
            raise CompanyDocumentValidationError("The DOCX could not be parsed.") from error
        sections: list[ExtractedSection] = []
        heading: str | None = None
        paragraphs: list[str] = []
        for paragraph in document.paragraphs:
            text = self._normalize(paragraph.text)
            if not text:
                continue
            if paragraph.style and paragraph.style.name.startswith("Heading"):
                if paragraphs:
                    sections.append(ExtractedSection("\n".join(paragraphs), section_title=heading))
                    paragraphs = []
                heading = text
            else:
                paragraphs.append(text)
        if paragraphs:
            sections.append(ExtractedSection("\n".join(paragraphs), section_title=heading))
        return sections

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
