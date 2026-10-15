import os
from dataclasses import dataclass
from typing import Any, Protocol

from livekit.plugins import assemblyai

ASSEMBLYAI_MODEL = "universal-streaming-english"


class STTAdapter(Protocol):
    provider: str
    model: str

    def create(self) -> Any:
        """Create the provider STT instance used by one publisher session."""


@dataclass(frozen=True)
class AssemblyAIStreamingSTTAdapter:
    provider: str = "ASSEMBLYAI"
    model: str = ASSEMBLYAI_MODEL

    def create(self) -> Any:
        if not os.getenv("ASSEMBLYAI_API_KEY", "").strip():
            raise RuntimeError("AssemblyAI transcription is not configured.")
        return assemblyai.STT(
            model=self.model,
            min_turn_silence=100,
            max_turn_silence=1000,
            vad_threshold=0.3,
        )
