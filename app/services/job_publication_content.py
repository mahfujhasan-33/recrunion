import re
from datetime import UTC, datetime

from app.adapters.publisher import JobPublicationContent
from app.errors import JobPublicationContentError
from app.schemas.jobs import JobResponse

MAX_BLUESKY_TEXT_LENGTH = 300


class JobPublicationContentBuilder:
    """Build a concise announcement without changing the approved JD."""

    def build(self, job: JobResponse) -> JobPublicationContent:
        if not job.jd_content:
            raise JobPublicationContentError(
                "The approved job description is unavailable for publication."
            )

        apply_line = f"Apply: {job.application_email}"
        if len(apply_line) > MAX_BLUESKY_TEXT_LENGTH:
            raise JobPublicationContentError(
                "The application instruction is too long for a Bluesky post."
            )

        available = MAX_BLUESKY_TEXT_LENGTH - len(apply_line) - 1
        candidates = [
            f"We're hiring: {job.title}",
            f"{job.location} | {job.employment_type.value.replace('_', ' ').title()}",
            self._extract_summary(job.jd_content),
            f"Skills: {', '.join(job.required_skills[:4])}" if job.required_skills else "",
            (
                f"Experience: {job.minimum_experience:g}+ years"
                if job.minimum_experience is not None
                else ""
            ),
        ]
        lines: list[str] = []
        for candidate in candidates:
            candidate = " ".join(candidate.split())
            if not candidate or available <= 0:
                continue
            separator = 1 if lines else 0
            room = available - separator
            if room <= 0:
                break
            if len(candidate) > room:
                if room < 20:
                    continue
                candidate = candidate[: room - 1].rstrip() + "…"
            lines.append(candidate)
            available -= len(candidate) + separator

        text = "\n".join([*lines, apply_line])
        return JobPublicationContent(text=text, created_at=datetime.now(UTC))

    @staticmethod
    def _extract_summary(content: str) -> str:
        summary_match = re.search(
            r"(?ims)^#{1,3}\s+summary\s*$\s*(.+?)(?=^#{1,3}\s|\Z)",
            content,
        )
        if summary_match:
            return summary_match.group(1).strip().split("\n\n", maxsplit=1)[0]
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped
        return ""
