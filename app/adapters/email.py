from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class OutboundEmail:
    recipient: str
    subject: str
    text_body: str


@dataclass(frozen=True)
class EmailSendResult:
    provider_message_id: str
    sent_at: datetime


class EmailAdapter(Protocol):
    async def send(self, message: OutboundEmail) -> EmailSendResult:
        """Send one normalized message or raise a safe domain error."""
