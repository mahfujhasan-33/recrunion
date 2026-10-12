import html
import re
from datetime import UTC, datetime

from app.adapters.publisher import JobPublicationContent
from app.errors import JobPublicationContentError
from app.schemas.jobs import JobResponse

MAX_BLUESKY_TEXT_LENGTH = 300


class JobPublicationContentBuilder:
    """Build full and short views from the same approved JD without rewriting it."""

    def build(
        self,
        job: JobResponse,
        *,
        full_post_url: str | None = None,
    ) -> JobPublicationContent:
        if not job.jd_content:
            raise JobPublicationContentError(
                "The approved job description is unavailable for publication."
            )
        subject = f"[{job.application_code}] Application"
        return JobPublicationContent(
            title=f"{job.title} — {job.location}",
            short_text=self._short_text(job, full_post_url),
            full_html=self._full_html(job, subject),
            jd_version=job.jd_version,
            created_at=datetime.now(UTC),
        )

    def _short_text(self, job: JobResponse, full_post_url: str | None) -> str:
        apply_line = (
            f"Full job and application details: {full_post_url}"
            if full_post_url
            else (f"Apply: {job.application_email} · Subject: [{job.application_code}] Application")
        )
        if len(apply_line) > MAX_BLUESKY_TEXT_LENGTH:
            raise JobPublicationContentError(
                "The application instruction is too long for a Bluesky post."
            )

        available = MAX_BLUESKY_TEXT_LENGTH - len(apply_line) - 1
        candidates = [
            f"{job.title} — {job.location}",
            "We're hiring.",
            f"Key skills: {', '.join(job.required_skills[:3])}",
            self._extract_summary(job.jd_content),
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

        return "\n".join([*lines, apply_line])

    @staticmethod
    def _full_html(job: JobResponse, subject: str) -> str:
        def items(values: list[str]) -> str:
            return "".join(f"<li>{html.escape(value)}</li>" for value in values)

        experience = (
            f"{job.minimum_experience:g}+ years"
            if job.minimum_experience is not None
            else "Not specified"
        )
        sections = [
            f"<p><strong>Location:</strong> {html.escape(job.location)}</p>",
            (
                "<p><strong>Employment type:</strong> "
                f"{html.escape(job.employment_type.value.replace('_', ' ').title())}</p>"
            ),
            "<h2>Approved job description</h2>",
            f"<pre>{html.escape(job.jd_content or '')}</pre>",
            "<h2>Required skills</h2>",
            f"<ul>{items(job.required_skills)}</ul>",
        ]
        if job.preferred_skills:
            sections.extend(
                ["<h2>Preferred skills</h2>", f"<ul>{items(job.preferred_skills)}</ul>"]
            )
        sections.extend(["<h2>Minimum experience</h2>", f"<p>{html.escape(experience)}</p>"])
        if job.qualifications:
            sections.extend(["<h2>Qualifications</h2>", f"<ul>{items(job.qualifications)}</ul>"])
        if job.selection_criteria:
            sections.extend(
                [
                    "<h2>Selection criteria</h2>",
                    f"<ul>{items(job.selection_criteria)}</ul>",
                ]
            )
        sections.extend(
            [
                "<h2>Apply by Email</h2>",
                (
                    "<p>Send one PDF CV using the exact subject below so RecrUnion can "
                    "associate your application with this vacancy.</p>"
                ),
                f"<p><strong>Email:</strong> {html.escape(job.application_email)}</p>",
                f"<p><strong>Application code:</strong> {html.escape(job.application_code)}</p>",
                f"<p><strong>Subject:</strong> {html.escape(subject)}</p>",
                "<p><strong>Attachment:</strong> One PDF CV only</p>",
            ]
        )
        return "\n".join(sections)

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
