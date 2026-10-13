from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class InterviewParticipantRole(StrEnum):
    INTERVIEWER = "INTERVIEWER"
    CANDIDATE = "CANDIDATE"


class SignalingTicketClaims(BaseModel):
    interview_id: UUID
    role: InterviewParticipantRole
    issued_at: int
    expires_at: int
    version: Literal[1] = 1


class AuthenticateSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["authenticate"]
    ticket: str = Field(min_length=1, max_length=4096)


class OfferSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["offer"]
    sdp: str = Field(min_length=1, max_length=100_000)


class AnswerSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["answer"]
    sdp: str = Field(min_length=1, max_length=100_000)


class IceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    candidate: str = Field(max_length=4096)
    sdp_mid: str | None = Field(default=None, alias="sdpMid", max_length=128)
    sdp_m_line_index: int | None = Field(
        default=None,
        alias="sdpMLineIndex",
        ge=0,
        le=100,
    )
    username_fragment: str | None = Field(
        default=None,
        alias="usernameFragment",
        max_length=256,
    )


class IceCandidateSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ice-candidate"]
    candidate: IceCandidate


class LeaveSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["leave"]


class EndCallSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["end-call"]


ClientSignal = Annotated[
    AuthenticateSignal
    | OfferSignal
    | AnswerSignal
    | IceCandidateSignal
    | LeaveSignal
    | EndCallSignal,
    Field(discriminator="type"),
]
CLIENT_SIGNAL_ADAPTER = TypeAdapter(ClientSignal)
