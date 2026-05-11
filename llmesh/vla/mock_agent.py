"""MockVLAAgent — turtlesim VLA PoC (Phase 9).

Composes a :class:`VisionEncoder` (defaulting to
:class:`MockTextSceneEncoder`) with an :class:`ActionDecoder`
(defaulting to :class:`MockTwistDecoder`). Together they produce a
deterministic, scene-conditional :class:`ActionStream`.

The agent is intentionally rule-based: the Phase 9 acceptance
criterion is "same instruction × different scene → different actions",
not "good actions" — that's the job of a real LLM-backed VLA in the
roadmap's further reaches.
"""

from __future__ import annotations

from llmesh.core.agent import AgentConfig
from llmesh.vla.decoders import ActionDecoder, MockTwistDecoder
from llmesh.vla.encoders import MockTextSceneEncoder, VisionEncoder
from llmesh.vla.vla import ActionStream, VisionLanguageRequest, VLAAgent


class MockVLAAgent(VLAAgent):
    """Rule-based VLA agent for the turtlesim PoC."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        encoder: VisionEncoder | None = None,
        decoder: ActionDecoder | None = None,
    ) -> None:
        super().__init__(config)
        self._encoder: VisionEncoder = (
            encoder if encoder is not None else MockTextSceneEncoder()
        )
        self._decoder: ActionDecoder = (
            decoder if decoder is not None else MockTwistDecoder()
        )

    def run(self, request: VisionLanguageRequest) -> ActionStream:
        features = self._encoder.encode(request.observation)
        return self._decoder.decode(
            instruction=request.instruction, features=features
        )


__all__ = ["MockVLAAgent"]
