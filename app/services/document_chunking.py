from dataclasses import dataclass

from app.adapters.document_parser import ExtractedSection


@dataclass(frozen=True)
class DocumentChunk:
    content: str
    page_number: int | None
    section_title: str | None


def chunk_sections(
    sections: list[ExtractedSection],
    *,
    target_words: int = 200,
    overlap_words: int = 30,
) -> list[DocumentChunk]:
    """Create bounded, overlapping chunks without crossing source sections."""

    chunks: list[DocumentChunk] = []
    step = target_words - overlap_words
    for section in sections:
        words = section.text.split()
        for start in range(0, len(words), step):
            content = " ".join(words[start : start + target_words]).strip()
            if content:
                chunks.append(
                    DocumentChunk(
                        content=content,
                        page_number=section.page_number,
                        section_title=section.section_title,
                    )
                )
            if start + target_words >= len(words):
                break
    return chunks
