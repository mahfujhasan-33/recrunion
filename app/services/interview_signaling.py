import asyncio
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from app.schemas.interview_signaling import InterviewParticipantRole


class DuplicateParticipantError(ValueError):
    """Raised when a role is already connected to an interview room."""


@dataclass
class SignalingRoom:
    participants: dict[InterviewParticipantRole, WebSocket] = field(default_factory=dict)
    connected_roles: set[InterviewParticipantRole] = field(default_factory=set)


class SignalingHub:
    """Coordinate transient, process-local signalling for two-party rooms."""

    def __init__(self) -> None:
        self._rooms: dict[UUID, SignalingRoom] = {}
        self._lock = asyncio.Lock()

    async def join(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        socket: WebSocket,
    ) -> bool:
        async with self._lock:
            room = self._rooms.setdefault(interview_id, SignalingRoom())
            if role in room.participants:
                raise DuplicateParticipantError
            peer_role = _peer_role(role)
            peer = room.participants.get(peer_role)
            room.participants[role] = socket
            room.connected_roles.clear()

        await socket.send_json(
            {
                "type": "authenticated",
                "role": role.value,
                "offerer": role == InterviewParticipantRole.INTERVIEWER,
                "peer_present": peer is not None,
            }
        )
        if peer is not None:
            await peer.send_json({"type": "peer-joined", "role": role.value})
            await socket.send_json({"type": "peer-joined", "role": peer_role.value})
        return peer is not None

    async def mark_peer_connected(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        socket: WebSocket,
    ) -> bool:
        """Confirm both current role sockets have established WebRTC."""

        async with self._lock:
            room = self._rooms.get(interview_id)
            if room is None or room.participants.get(role) is not socket:
                return False
            room.connected_roles.add(role)
            return all(
                expected in room.connected_roles and expected in room.participants
                for expected in InterviewParticipantRole
            )

    async def broadcast(
        self,
        interview_id: UUID,
        message: dict[str, object],
    ) -> None:
        async with self._lock:
            room = self._rooms.get(interview_id)
            sockets = list(room.participants.values()) if room is not None else []
        for socket in sockets:
            try:
                await socket.send_json(message)
            except (RuntimeError, WebSocketDisconnect):
                continue

    async def relay_to_peer(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        message: dict[str, object],
    ) -> bool:
        async with self._lock:
            room = self._rooms.get(interview_id)
            peer = room.participants.get(_peer_role(role)) if room is not None else None
        if peer is None:
            return False
        await peer.send_json(message)
        return True

    async def leave(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
        socket: WebSocket,
        *,
        notify_peer: bool = True,
    ) -> None:
        async with self._lock:
            room = self._rooms.get(interview_id)
            if room is None or room.participants.get(role) is not socket:
                return
            del room.participants[role]
            room.connected_roles.discard(role)
            peer = room.participants.get(_peer_role(role))
            if not room.participants:
                self._rooms.pop(interview_id, None)
        if notify_peer and peer is not None:
            await peer.send_json({"type": "peer-left", "role": role.value})

    async def end_call(
        self,
        interview_id: UUID,
        role: InterviewParticipantRole,
    ) -> None:
        async with self._lock:
            room = self._rooms.pop(interview_id, None)
            sockets = list(room.participants.values()) if room is not None else []
        for socket in sockets:
            try:
                await socket.send_json({"type": "call-ended", "ended_by": role.value})
            except (RuntimeError, WebSocketDisconnect):
                continue

    async def participant_count(self, interview_id: UUID) -> int:
        async with self._lock:
            room = self._rooms.get(interview_id)
            return len(room.participants) if room is not None else 0


def _peer_role(role: InterviewParticipantRole) -> InterviewParticipantRole:
    if role == InterviewParticipantRole.INTERVIEWER:
        return InterviewParticipantRole.CANDIDATE
    return InterviewParticipantRole.INTERVIEWER
