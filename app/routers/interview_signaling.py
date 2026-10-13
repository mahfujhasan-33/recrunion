import asyncio
import json
import logging
from contextlib import suppress
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.dependencies import get_interview_room_service
from app.errors import InterviewEndedError, InterviewLifecycleError
from app.schemas.interview_signaling import (
    CLIENT_SIGNAL_ADAPTER,
    AnswerSignal,
    AuthenticateSignal,
    EndCallSignal,
    IceCandidateSignal,
    IceRestartRequestSignal,
    InterviewParticipantRole,
    LeaveSignal,
    OfferSignal,
    PeerConnectedSignal,
)
from app.services.interview_rooms import InterviewRoomService, SignalingTicketError
from app.services.interview_signaling import DuplicateParticipantError, SignalingHub

router = APIRouter(tags=["interview signalling"], include_in_schema=False)
logger = logging.getLogger(__name__)
MAX_SIGNAL_MESSAGE_BYTES = 131_072

InterviewRoomServiceDependency = Annotated[
    InterviewRoomService,
    Depends(get_interview_room_service),
]


@router.websocket("/ws/interviews/{interview_id}/signal")
async def interview_signaling_socket(
    websocket: WebSocket,
    interview_id: UUID,
    service: InterviewRoomServiceDependency,
) -> None:
    await websocket.accept()
    hub: SignalingHub = websocket.app.state.interview_signaling_hub
    role: InterviewParticipantRole | None = None
    joined = False
    try:
        async with asyncio.timeout(10):
            authentication = await _receive_authentication(websocket)
        claims = service.authenticate_ticket(authentication.ticket, interview_id)
        role = claims.role
        await hub.join(interview_id, role, websocket)
        joined = True
        service.participant_joined(interview_id)

        while True:
            payload = await _receive_json(websocket)
            signal = CLIENT_SIGNAL_ADAPTER.validate_python(payload)
            if isinstance(signal, AuthenticateSignal):
                await _reject(
                    websocket,
                    "ALREADY_AUTHENTICATED",
                    "This connection is already authenticated.",
                )
                break
            if not _is_allowed(role, signal):
                await _reject(
                    websocket,
                    "ACTION_NOT_ALLOWED",
                    "This participant role cannot send that signalling message.",
                )
                break
            if isinstance(signal, LeaveSignal):
                await hub.leave(interview_id, role, websocket)
                joined = False
                await websocket.close(code=1000)
                break
            if isinstance(signal, EndCallSignal):
                service.end_interview(interview_id, role)
                await hub.end_call(
                    interview_id,
                    role,
                )
                joined = False
                await websocket.close(code=1000)
                break
            if isinstance(signal, PeerConnectedSignal):
                both_connected = await hub.mark_peer_connected(
                    interview_id,
                    role,
                    websocket,
                )
                if both_connected:
                    interview = service.mark_active(interview_id)
                    await hub.broadcast(
                        interview_id,
                        {
                            "type": "interview-active",
                            "started_at": (
                                interview.started_at.isoformat()
                                if interview.started_at is not None
                                else None
                            ),
                        },
                    )
                continue
            relayed = await hub.relay_to_peer(
                interview_id,
                role,
                signal.model_dump(by_alias=True),
            )
            if not relayed:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "PEER_NOT_CONNECTED",
                        "message": "The other participant has not joined yet.",
                    }
                )
    except DuplicateParticipantError:
        await _reject(
            websocket,
            "ROLE_ALREADY_CONNECTED",
            "This participant role is already connected.",
        )
    except SignalingTicketError:
        await _reject(
            websocket,
            "AUTHENTICATION_FAILED",
            "The signalling ticket is invalid or expired.",
        )
    except InterviewEndedError:
        await _reject(
            websocket,
            "INTERVIEW_ENDED",
            "This interview has ended and cannot be rejoined.",
        )
    except InterviewLifecycleError:
        await _reject(
            websocket,
            "INTERVIEW_STATE_INVALID",
            "The interview is not available for this action.",
        )
    except TimeoutError:
        await _reject(
            websocket,
            "AUTHENTICATION_TIMEOUT",
            "The signalling connection was not authenticated in time.",
        )
    except (json.JSONDecodeError, ValidationError, InvalidSignalMessageError):
        await _reject(
            websocket,
            "INVALID_MESSAGE",
            "The signalling message is invalid.",
        )
    except MessageTooLargeError:
        await _reject(
            websocket,
            "MESSAGE_TOO_LARGE",
            "The signalling message exceeds the allowed size.",
        )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.error(
            "Interview signalling failed for interview_id=%s role=%s",
            interview_id,
            role.value if role is not None else "UNAUTHENTICATED",
        )
        await _reject(
            websocket,
            "SIGNALING_UNAVAILABLE",
            "Interview signalling is temporarily unavailable.",
            close_code=1011,
        )
    finally:
        if joined and role is not None:
            with suppress(RuntimeError, WebSocketDisconnect):
                await hub.leave(interview_id, role, websocket)


async def _receive_authentication(websocket: WebSocket) -> AuthenticateSignal:
    payload = await _receive_json(websocket)
    return AuthenticateSignal.model_validate(payload)


async def _receive_json(websocket: WebSocket) -> object:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if text is None:
        raise InvalidSignalMessageError
    if len(text.encode("utf-8")) > MAX_SIGNAL_MESSAGE_BYTES:
        raise MessageTooLargeError
    return json.loads(text)


def _is_allowed(role: InterviewParticipantRole, signal: object) -> bool:
    if isinstance(
        signal,
        IceCandidateSignal | LeaveSignal | PeerConnectedSignal | IceRestartRequestSignal,
    ):
        return True
    if isinstance(signal, OfferSignal | EndCallSignal):
        return role == InterviewParticipantRole.INTERVIEWER
    if isinstance(signal, AnswerSignal):
        return role == InterviewParticipantRole.CANDIDATE
    return False


async def _reject(
    websocket: WebSocket,
    code: str,
    message: str,
    *,
    close_code: int = 1008,
) -> None:
    try:
        await websocket.send_json({"type": "error", "code": code, "message": message})
        await websocket.close(code=close_code)
    except (RuntimeError, WebSocketDisconnect):
        pass


class MessageTooLargeError(ValueError):
    pass


class InvalidSignalMessageError(ValueError):
    pass
