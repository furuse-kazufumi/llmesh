"""Neuromorphic sparse multimodal encoder (Phase 18, D3 skeleton).

D3's full promise — event-stream + spike neural network grounding for
VLA at ~28 µJ/inference on neuromorphic hardware — is a multi-month
implementation. This module ships the **API skeleton** so downstream
phases (real DVS camera input, Loihi / Akida runtime, sparse
autodiff) can drop in without churning the public interface.

Core idea: a dense observation (image frame, joint state, tactile
reading) is converted into a stream of :class:`EventToken` objects —
``(timestamp, channel, value)`` — where only *changes above a delta
threshold* generate tokens. The downstream VLA agent operates on this
sparse stream instead of dense tensors, which is what enables event-
camera-class power budgets and low-latency edge deploy.

The mock encoder in this file is pure stdlib: it tokenises by simple
thresholded delta over a synthetic dense observation. A real implementation
would plug in a DVS-style log-intensity threshold for vision and a
rate-coded scheme for tactile / proprioceptive channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EventToken:
    """One event in the sparse stream.

    ``timestamp`` is monotonic (any unit — ms / µs / step index — as
    long as it's consistent within a stream). ``channel`` identifies
    the modality + spatial index (``"vision/0/0"`` / ``"tactile/3"``
    / ``"joint/0"``). ``value`` is the post-threshold signed delta
    (positive = brighter / harder / faster; negative = reverse).
    """

    timestamp: float
    channel: str
    value: float


@dataclass(frozen=True)
class SparseObservation:
    """One observation expressed as an event stream + summary metadata."""

    events: tuple[EventToken, ...]
    duration: float = 0.0
    n_dense_channels: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def n_events(self) -> int:
        return len(self.events)

    @property
    def sparsity(self) -> float:
        """Fraction of dense channels that produced *no* events.

        1.0 = fully sparse (no activity), 0.0 = every channel fired
        at least once. Returns 0.0 when ``n_dense_channels`` is unset
        so the field is safe to call unconditionally.
        """
        if self.n_dense_channels <= 0:
            return 0.0
        active = len(set(e.channel for e in self.events))
        return max(0.0, 1.0 - active / self.n_dense_channels)


def dense_to_events(
    *,
    prev: tuple[float, ...] | None,
    cur: tuple[float, ...],
    timestamp: float,
    channel_prefix: str,
    threshold: float = 0.1,
) -> list[EventToken]:
    """Tokenise one dense vector by signed-delta thresholding.

    Emits one :class:`EventToken` per channel whose absolute delta
    from ``prev`` exceeds ``threshold``. ``prev=None`` means "no
    reference yet" — every active channel above threshold (vs. zero)
    fires, modeling fresh-on conditions like a freshly powered DVS.
    """
    if threshold <= 0:
        raise ValueError(f"threshold must be > 0 (got {threshold})")
    out: list[EventToken] = []
    ref = prev if prev is not None else tuple(0.0 for _ in cur)
    for i, val in enumerate(cur):
        delta = val - (ref[i] if i < len(ref) else 0.0)
        if abs(delta) > threshold:
            out.append(
                EventToken(
                    timestamp=timestamp,
                    channel=f"{channel_prefix}/{i}",
                    value=delta,
                )
            )
    return out


def events_to_feature_vector(
    obs: SparseObservation, *, n_channels: int
) -> tuple[float, ...]:
    """Pool events back into a fixed-length feature vector.

    The pooler signs each channel's events: total positive delta
    contributes positively, negative deltas subtract. The result is
    a small dense vector usable by any downstream model that wasn't
    designed for sparse tokens — the bridge during migration.
    """
    if n_channels <= 0:
        raise ValueError(f"n_channels must be > 0 (got {n_channels})")
    sums = [0.0] * n_channels
    for e in obs.events:
        # channel names look like "<prefix>/<idx>"; extract idx
        idx_str = e.channel.rsplit("/", 1)[-1]
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if 0 <= idx < n_channels:
            sums[idx] += e.value
    return tuple(sums)


class SparseEncoder:
    """Stateful encoder — keeps the previous dense state for delta coding."""

    def __init__(
        self,
        *,
        channel_prefix: str = "input",
        threshold: float = 0.1,
    ) -> None:
        if threshold <= 0:
            raise ValueError(f"threshold must be > 0 (got {threshold})")
        self.channel_prefix = channel_prefix
        self.threshold = threshold
        self._prev: tuple[float, ...] | None = None
        self._t: float = 0.0

    def encode(
        self, dense: tuple[float, ...], *, dt: float = 1.0
    ) -> SparseObservation:
        """Encode one dense observation; advances internal time by ``dt``."""
        if dt <= 0:
            raise ValueError(f"dt must be > 0 (got {dt})")
        self._t += dt
        events = dense_to_events(
            prev=self._prev,
            cur=dense,
            timestamp=self._t,
            channel_prefix=self.channel_prefix,
            threshold=self.threshold,
        )
        self._prev = tuple(dense)
        return SparseObservation(
            events=tuple(events),
            duration=dt,
            n_dense_channels=len(dense),
        )

    def reset(self) -> None:
        """Drop the previous dense reference; next encode treats input as fresh-on."""
        self._prev = None
        self._t = 0.0


__all__ = [
    "EventToken",
    "SparseEncoder",
    "SparseObservation",
    "dense_to_events",
    "events_to_feature_vector",
]
