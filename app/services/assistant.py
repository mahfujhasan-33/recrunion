from collections.abc import Callable
from uuid import UUID

from pydantic import ValidationError

from app.errors import (
    AssistantActionError,
    AssistantArtifactNotFoundError,
    AssistantConversationNotFoundError,
    AssistantValidationError,
)
from app.graphs.recruiter_assistant import RecruiterAssistantGraph
from app.models.assistant import (
    AssistantArtifact,
    AssistantArtifactStatus,
    AssistantArtifactType,
    AssistantConversation,
    AssistantMessage,
    AssistantMessageRole,
)
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.repositories.assistant import AssistantRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.assistant import (
    AssistantArtifactResponse,
    AssistantConversationResponse,
    AssistantIntent,
    AssistantMessageResponse,
    AssistantRequiredAction,
    AssistantTurnQueuedResponse,
    AssistantTurnRequest,
    AssistantWorkspaceResponse,
    RequirementDraft,
)
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.job_publications import JobPublicationResponse
from app.schemas.jobs import JobResponse, JobWriteRequest
from app.services.job_descriptions import JobDescriptionService
from app.services.job_publications import JobPublishingService
from app.services.jobs import JobService
from app.services.policy_reviews import PolicyReviewService

ProgressReporter = Callable[[int, str], None]


class AssistantService:
    """Coordinate bounded recruiter-assistant actions through existing use cases."""

    def __init__(
        self,
        repository: AssistantRepository,
        task_repository: ProcessingJobRepository,
        job_service: JobService,
        description_service: JobDescriptionService,
        policy_service: PolicyReviewService,
        publishing_service: JobPublishingService,
        assistant_graph: RecruiterAssistantGraph,
        *,
        worker_max_attempts: int,
    ) -> None:
        self._repository = repository
        self._task_repository = task_repository
        self._job_service = job_service
        self._description_service = description_service
        self._policy_service = policy_service
        self._publishing_service = publishing_service
        self._assistant_graph = assistant_graph
        self._worker_max_attempts = worker_max_attempts

    def create_conversation(self) -> AssistantConversationResponse:
        conversation = self._repository.create_conversation(AssistantConversation())
        self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation.id,
                role=AssistantMessageRole.ASSISTANT,
                content=(
                    "Tell me what role you need. I will draft structured requirements, "
                    "show what still needs your input, and generate the JD when you are ready."
                ),
            )
        )
        self._repository.save_artifact(
            conversation_id=conversation.id,
            artifact_type=AssistantArtifactType.JOB_REQUIREMENTS,
            payload=RequirementDraft().model_dump(mode="json"),
            status=AssistantArtifactStatus.WORKING,
            validation={"missing_fields": self._missing_fields(RequirementDraft())},
        )
        return self.get_conversation(conversation.id)

    def get_latest_or_create(self) -> AssistantConversationResponse:
        conversation = self._repository.latest_conversation()
        return (
            self.get_conversation(conversation.id) if conversation else self.create_conversation()
        )

    def get_conversation(self, conversation_id: UUID) -> AssistantConversationResponse:
        conversation = self._get_conversation(conversation_id)
        return AssistantConversationResponse(
            id=conversation.id,
            title=conversation.title,
            active_job_id=conversation.active_job_id,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            messages=[
                AssistantMessageResponse.model_validate(message, from_attributes=True)
                for message in self._repository.list_messages(conversation.id)
            ],
            workspace=self._build_workspace(conversation),
        )

    def queue_turn(self, conversation_id: UUID, content: str) -> AssistantTurnQueuedResponse:
        conversation = self._get_conversation(conversation_id)
        message = self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation.id,
                role=AssistantMessageRole.USER,
                content=content,
            )
        )
        task = self._task_repository.create(
            ProcessingJob(
                job_type=ProcessingJobType.ASSISTANT_TURN,
                entity_type="ASSISTANT_MESSAGE",
                entity_id=message.id,
                max_attempts=self._worker_max_attempts,
                progress_message="Waiting for the recruiter assistant",
            )
        )
        return AssistantTurnQueuedResponse(task_id=task.id, conversation_id=conversation.id)

    async def process_turn(self, message_id: UUID, report: ProgressReporter) -> None:
        message = self._repository.get_message(message_id)
        if message is None:
            raise AssistantConversationNotFoundError
        conversation = self._get_conversation(message.conversation_id)
        report(10, "Understanding your request")
        requirements_artifact = self._repository.latest_artifact(
            conversation.id, AssistantArtifactType.JOB_REQUIREMENTS
        )
        requirements = self._requirements_from_artifact(requirements_artifact)
        active_job = (
            self._job_service.get_job(conversation.active_job_id)
            if conversation.active_job_id
            else None
        )
        graph_state = await self._assistant_graph.run(
            AssistantTurnRequest(
                conversation_id=conversation.id,
                user_message=message.content,
                current_requirements=requirements,
                active_job=active_job,
            )
        )
        error = graph_state.get("error")
        if error is not None:
            raise error
        plan = graph_state["plan"]
        report(30, "Updating the active workspace")
        response = plan.response_message
        if plan.intent in {
            AssistantIntent.DRAFT_REQUIREMENTS,
            AssistantIntent.UPDATE_REQUIREMENTS,
        }:
            if active_job is not None and active_job.status != JobStatus.DRAFT:
                response = (
                    "Structured requirements are locked after JD generation. "
                    "Edit the JD or start a new hiring conversation."
                )
            else:
                draft = plan.requirements or requirements
                self._save_requirements(conversation.id, draft)
        elif plan.intent == AssistantIntent.GENERATE_DESCRIPTION:
            response = await self._generate(conversation, requirements, report)
        elif plan.intent == AssistantIntent.ENHANCE_DESCRIPTION:
            response = await self._enhance(conversation, report)
        elif plan.intent == AssistantIntent.RECHECK_POLICY:
            response = await self._recheck(conversation, report)
        elif plan.intent == AssistantIntent.APPROVE_DESCRIPTION:
            response = (
                "Approval is consequential. Review the current JD and policy evidence, then "
                "use Confirm approval in the workspace if you want to approve it."
            )
        elif plan.intent == AssistantIntent.PUBLISH_JOB:
            response = self._publication_guidance(conversation, retry=False)
        elif plan.intent == AssistantIntent.RETRY_JOB_PUBLICATION:
            response = self._publication_guidance(conversation, retry=True)
        elif plan.intent == AssistantIntent.GET_PUBLICATION_STATUS:
            response = self._publication_status(conversation)
        report(95, "Preparing the workspace")
        self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation.id,
                role=AssistantMessageRole.ASSISTANT,
                content=response,
            )
        )

    def update_requirements(
        self, conversation_id: UUID, artifact_id: UUID, payload: dict[str, object]
    ) -> AssistantConversationResponse:
        conversation = self._get_conversation(conversation_id)
        if conversation.active_job_id is not None:
            active_job = self._job_service.get_job(conversation.active_job_id)
            if active_job.status != JobStatus.DRAFT:
                raise AssistantActionError(
                    "Structured requirements cannot change after JD generation. "
                    "Edit the JD instead."
                )
        artifact = self._repository.get_artifact(artifact_id)
        if (
            artifact is None
            or artifact.conversation_id != conversation_id
            or artifact.artifact_type != AssistantArtifactType.JOB_REQUIREMENTS
        ):
            raise AssistantArtifactNotFoundError
        try:
            draft = RequirementDraft.model_validate(payload)
        except ValidationError:
            raise AssistantValidationError(
                "The structured requirements did not pass validation."
            ) from None
        self._save_requirements(conversation_id, draft)
        self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation_id,
                role=AssistantMessageRole.SYSTEM,
                content="The recruiter updated the structured requirements in the workspace.",
            )
        )
        return self.get_conversation(conversation_id)

    def apply_proposal(
        self, conversation_id: UUID, artifact_id: UUID
    ) -> AssistantConversationResponse:
        conversation = self._get_conversation(conversation_id)
        artifact = self._repository.get_artifact(artifact_id)
        if (
            artifact is None
            or artifact.conversation_id != conversation_id
            or artifact.artifact_type != AssistantArtifactType.JOB_DESCRIPTION_PROPOSAL
            or artifact.status != AssistantArtifactStatus.PROPOSED
            or conversation.active_job_id is None
        ):
            raise AssistantArtifactNotFoundError
        job = self._job_service.get_job(conversation.active_job_id)
        if job.jd_version != artifact.base_job_version:
            raise AssistantActionError(
                "The job description changed after this proposal was created. "
                "Generate a new proposal."
            )
        content = str(artifact.payload.get("content", ""))
        updated = self._description_service.update_description(
            job.id, JobDescriptionUpdateRequest(content=content)
        )
        artifact.status = AssistantArtifactStatus.APPLIED
        self._repository.update_artifact(artifact)
        self._save_description(conversation.id, updated)
        self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation.id,
                role=AssistantMessageRole.ASSISTANT,
                content=(
                    "The enhancement proposal was applied. Policy alignment must be rechecked "
                    "for this new JD version before approval."
                ),
            )
        )
        return self.get_conversation(conversation.id)

    def confirm_approval(
        self, conversation_id: UUID, confirmed: bool
    ) -> AssistantConversationResponse:
        conversation = self._get_conversation(conversation_id)
        if not confirmed or conversation.active_job_id is None:
            raise AssistantActionError(
                "Explicit confirmation is required to approve a job description."
            )
        self._description_service.approve_description(conversation.active_job_id)
        self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation.id,
                role=AssistantMessageRole.ASSISTANT,
                content="The reviewed job description is now approved.",
            )
        )
        return self.get_conversation(conversation.id)

    def confirm_publication(
        self,
        conversation_id: UUID,
        confirmed: bool,
        *,
        retry: bool,
    ) -> JobPublicationResponse:
        conversation = self._get_conversation(conversation_id)
        if not confirmed or conversation.active_job_id is None:
            raise AssistantActionError(
                "Explicit confirmation is required to publish a job externally."
            )
        publication = (
            self._publishing_service.retry(conversation.active_job_id)
            if retry
            else self._publishing_service.publish(conversation.active_job_id)
        )
        action = "retry" if retry else "publication"
        self._repository.add_message(
            AssistantMessage(
                conversation_id=conversation.id,
                role=AssistantMessageRole.ASSISTANT,
                content=(
                    f"The Bluesky {action} is queued. I will show progress and update "
                    "the workspace when it finishes."
                ),
            )
        )
        return publication

    async def _generate(
        self,
        conversation: AssistantConversation,
        requirements: RequirementDraft,
        report: ProgressReporter,
    ) -> str:
        try:
            request = JobWriteRequest.model_validate(requirements.model_dump())
        except ValidationError:
            self._save_requirements(conversation.id, requirements)
            return (
                "The requirements are not complete yet. Fill the required actions in the "
                "workspace, then ask me to generate the job description again."
            )
        report(45, "Saving the structured job requirements")
        if conversation.active_job_id is None:
            job = self._job_service.create_job(request)
            conversation.active_job_id = job.id
            conversation.title = job.title
            self._repository.update_conversation(conversation)
        else:
            current = self._job_service.get_job(conversation.active_job_id)
            if current.status == JobStatus.DRAFT:
                job = self._job_service.update_job(current.id, request)
            else:
                raise AssistantActionError(
                    "The active job already has a generated description. Edit or enhance that JD."
                )
        report(55, "Retrieving relevant company policy evidence")
        report(70, "Generating the job description")
        generated = await self._description_service.generate_description(job.id)
        report(88, "Checking company-policy alignment")
        self._save_description(conversation.id, generated)
        return (
            "The job description is ready in the workspace. Review and edit it, inspect the "
            "evidence panel, or ask me to enhance it using the policy findings."
        )

    async def _enhance(self, conversation: AssistantConversation, report: ProgressReporter) -> str:
        if conversation.active_job_id is None:
            raise AssistantActionError("Create and generate a job description before enhancing it.")
        report(45, "Retrieving policy evidence and findings")
        report(65, "Drafting an evidence-backed enhancement")
        content, base_version, findings = await self._description_service.preview_enhancement(
            conversation.active_job_id
        )
        self._repository.save_artifact(
            conversation_id=conversation.id,
            artifact_type=AssistantArtifactType.JOB_DESCRIPTION_PROPOSAL,
            payload={"content": content, "policy_findings": findings},
            status=AssistantArtifactStatus.PROPOSED,
            job_id=conversation.active_job_id,
            base_job_version=base_version,
        )
        report(88, "Preparing the enhancement proposal")
        return (
            "I prepared an evidence-backed enhancement proposal without changing the current JD. "
            "Compare it in the workspace and apply it when you are satisfied."
        )

    async def _recheck(self, conversation: AssistantConversation, report: ProgressReporter) -> str:
        if conversation.active_job_id is None:
            raise AssistantActionError("There is no active job description to check.")
        report(55, "Retrieving current policy evidence")
        report(75, "Evaluating policy alignment")
        review = await self._policy_service.recheck(conversation.active_job_id)
        return (
            f"Policy alignment is current. I found {review.actionable_finding_count} "
            "partially met or unmet item(s)."
        )

    def _save_requirements(self, conversation_id: UUID, draft: RequirementDraft) -> None:
        missing = self._missing_fields(draft)
        self._repository.save_artifact(
            conversation_id=conversation_id,
            artifact_type=AssistantArtifactType.JOB_REQUIREMENTS,
            payload=draft.model_dump(mode="json"),
            status=(
                AssistantArtifactStatus.READY if not missing else AssistantArtifactStatus.WORKING
            ),
            validation={"missing_fields": missing},
        )

    def _save_description(self, conversation_id: UUID, job: JobResponse) -> None:
        self._repository.save_artifact(
            conversation_id=conversation_id,
            artifact_type=AssistantArtifactType.JOB_DESCRIPTION,
            payload={"content": job.jd_content or "", "version": job.jd_version},
            status=AssistantArtifactStatus.READY,
            job_id=job.id,
            base_job_version=job.jd_version,
        )

    def _build_workspace(self, conversation: AssistantConversation) -> AssistantWorkspaceResponse:
        requirements = self._repository.latest_artifact(
            conversation.id, AssistantArtifactType.JOB_REQUIREMENTS
        )
        description = self._repository.latest_artifact(
            conversation.id, AssistantArtifactType.JOB_DESCRIPTION
        )
        proposal = self._repository.latest_artifact(
            conversation.id, AssistantArtifactType.JOB_DESCRIPTION_PROPOSAL
        )
        job = (
            self._job_service.get_job(conversation.active_job_id)
            if conversation.active_job_id
            else None
        )
        review = self._policy_service.get_latest(job.id) if job and job.jd_content else None
        publication = self._publishing_service.get_latest(job.id) if job else None
        actions: list[AssistantRequiredAction] = []
        draft = self._requirements_from_artifact(requirements)
        missing = self._missing_fields(draft)
        if missing and (job is None or job.status == JobStatus.DRAFT):
            actions.append(
                AssistantRequiredAction(
                    code="COMPLETE_REQUIREMENTS",
                    label="Complete requirements",
                    severity="BLOCKING",
                    message=f"Required: {', '.join(missing)}.",
                )
            )
        elif job is None:
            actions.append(
                AssistantRequiredAction(
                    code="GENERATE_DESCRIPTION",
                    label="Generate JD",
                    severity="READY",
                    message="Requirements are ready for job-description generation.",
                )
            )
        if job and job.status == JobStatus.GENERATED and review and not review.is_current:
            actions.append(
                AssistantRequiredAction(
                    code="RECHECK_POLICY",
                    label="Recheck policy",
                    severity="BLOCKING",
                    message="The JD changed and its policy review is no longer current.",
                )
            )
        elif job and job.status == JobStatus.GENERATED and review and review.is_current:
            actions.append(
                AssistantRequiredAction(
                    code="REVIEW_AND_APPROVE",
                    label="Review and approve",
                    severity="REVIEW_RECOMMENDED",
                    message="Review the current JD and evidence before explicit approval.",
                )
            )
        elif job and job.status == JobStatus.APPROVED:
            actions.append(
                AssistantRequiredAction(
                    code="PUBLISH_JOB",
                    label="Publish to Bluesky",
                    severity="CONFIRMATION_REQUIRED",
                    message=(
                        f"{job.code} · {job.title} · approved JD version {job.jd_version}. "
                        "The policy review is current. Target: Bluesky. Publishing creates "
                        "an external post and requires confirmation."
                    ),
                )
            )
        elif job and job.status == JobStatus.PUBLISHING:
            actions.append(
                AssistantRequiredAction(
                    code="PUBLICATION_IN_PROGRESS",
                    label="Publishing to Bluesky",
                    severity="IN_PROGRESS",
                    message="A publication attempt is already in progress.",
                )
            )
        elif job and job.status == JobStatus.PUBLISH_FAILED:
            actions.append(
                AssistantRequiredAction(
                    code="RETRY_JOB_PUBLICATION",
                    label="Retry Bluesky publication",
                    severity="CONFIRMATION_REQUIRED",
                    message=(
                        publication.error_message_safe
                        if publication and publication.error_message_safe
                        else "The last publication failed. An explicit retry is available."
                    ),
                )
            )
        return AssistantWorkspaceResponse(
            requirements=self._artifact_response(requirements),
            description=self._artifact_response(description),
            proposal=self._artifact_response(proposal),
            job=job,
            policy_review=review,
            publication=publication,
            required_actions=actions,
        )

    def _publication_guidance(
        self,
        conversation: AssistantConversation,
        *,
        retry: bool,
    ) -> str:
        if conversation.active_job_id is None:
            return "There is no active job to publish. Create a job description first."
        job = self._job_service.get_job(conversation.active_job_id)
        if job.status == JobStatus.DRAFT:
            return (
                "This job is still a draft. Generate and review its job description, then "
                "approve it before publishing."
            )
        if job.status == JobStatus.GENERATED:
            return (
                "This job description still needs explicit recruiter approval before it can "
                "be published. Review the JD and policy evidence first."
            )
        if job.status == JobStatus.APPROVED:
            if retry:
                return "This job has not failed publication. Use Publish to Bluesky instead."
            return (
                f"{job.code} is approved and ready to publish to Bluesky. Review the target "
                "and approved JD version in the workspace, then confirm publication."
            )
        if job.status == JobStatus.PUBLISHING:
            return "This job is already being published. Its progress is shown in the workspace."
        if job.status == JobStatus.PUBLISHED:
            return self._publication_status(conversation)
        if job.status == JobStatus.PUBLISH_FAILED:
            if retry:
                return (
                    "The last Bluesky publication failed. Review the safe failure message in "
                    "the workspace, then explicitly confirm the retry."
                )
            return "The last publication failed. Use the explicit Retry Publishing action."
        return "A closed job cannot be published."

    def _publication_status(self, conversation: AssistantConversation) -> str:
        if conversation.active_job_id is None:
            return "There is no active job publication to report."
        job = self._job_service.get_job(conversation.active_job_id)
        publication = self._publishing_service.get_latest(job.id)
        if publication is None:
            return f"{job.code} has not been submitted for publication."
        if job.status == JobStatus.PUBLISHED:
            destination = (
                f" View it at {publication.external_url}." if publication.external_url else ""
            )
            return f"{job.code} is published on Bluesky.{destination}"
        if job.status == JobStatus.PUBLISH_FAILED:
            safe_detail = publication.error_message_safe or "No safe detail is available."
            return (
                f"The Bluesky publication failed: {safe_detail} You can request an explicit retry."
            )
        return f"The Bluesky publication is {publication.status.value.lower()}."

    def _get_conversation(self, conversation_id: UUID) -> AssistantConversation:
        conversation = self._repository.get_conversation(conversation_id)
        if conversation is None:
            raise AssistantConversationNotFoundError
        return conversation

    @staticmethod
    def _requirements_from_artifact(artifact: AssistantArtifact | None) -> RequirementDraft:
        return RequirementDraft.model_validate(artifact.payload) if artifact else RequirementDraft()

    @staticmethod
    def _artifact_response(
        artifact: AssistantArtifact | None,
    ) -> AssistantArtifactResponse | None:
        return (
            AssistantArtifactResponse.model_validate(artifact, from_attributes=True)
            if artifact
            else None
        )

    @staticmethod
    def _missing_fields(draft: RequirementDraft) -> list[str]:
        missing: list[str] = []
        for field in ("title", "location", "employment_type", "application_email"):
            if not getattr(draft, field):
                missing.append(field.replace("_", " "))
        if not draft.required_skills:
            missing.append("required skills")
        return missing
