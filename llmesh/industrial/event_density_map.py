"""Event density map — DVS event stream → multivariate observation.

A small grid-based aggregator that converts a DVS (Dynamic Vision Sensor)
event stream into a fixed-dimension feature vector suitable for
``HotellingT2Chart`` or ``OnlineMTEngine``.

Why a grid?
-----------
A DVS sensor emits ``(t, x, y, polarity)`` tuples at micro-second rates.
Feeding individual events into a multivariate chart is impractical —
instead we bin events into a coarse spatial grid (default 8×8) over a
fixed time window. The resulting flat histogram is the multivariate
observation.

The grid coordinates are linear-mapped from the sensor's full
resolution (``sensor_w`` × ``sensor_h``) so the same code works for
DVS128, DVS346, Prophesee EVK, etc. without per-sensor tuning.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mt_engine import _require_numpy


@dataclass(frozen=True)
class DensityFeature:
    """A flat density vector ready to feed an SPC chart."""

    vector: object  # numpy 1-D array, length grid_w * grid_h
    grid_shape: tuple[int, int]  # (rows, cols)
    event_count: int


class EventDensityMap:
    """Aggregates DVS events into a coarse grid histogram.

    Parameters
    ----------
    sensor_w, sensor_h:
        Native sensor resolution (e.g. DVS346 = 346×260).
    grid_w, grid_h:
        Coarse grid dimensions (default 8×8 = 64-D feature vector).
    polarity:
        ``"both"`` counts every event, ``"on"`` counts only positive
        polarity, ``"off"`` counts only negative.
    """

    def __init__(
        self,
        sensor_w: int,
        sensor_h: int,
        *,
        grid_w: int = 8,
        grid_h: int = 8,
        polarity: str = "both",
    ) -> None:
        if sensor_w <= 0 or sensor_h <= 0:
            raise ValueError("sensor dimensions must be positive")
        if grid_w <= 0 or grid_h <= 0:
            raise ValueError("grid dimensions must be positive")
        if polarity not in ("both", "on", "off"):
            raise ValueError("polarity must be 'both', 'on', or 'off'")
        self._sensor_w = int(sensor_w)
        self._sensor_h = int(sensor_h)
        self._grid_w = int(grid_w)
        self._grid_h = int(grid_h)
        self._polarity = polarity

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def feature_dim(self) -> int:
        """Length of the produced feature vector."""
        return self._grid_w * self._grid_h

    @property
    def grid_shape(self) -> tuple[int, int]:
        return (self._grid_h, self._grid_w)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def aggregate(self, events) -> DensityFeature:
        """Convert ``events`` into a density vector.

        ``events`` may be:
        - a numpy structured array with fields ``x, y, polarity``
        - a regular numpy array of shape ``(n, 3)`` or ``(n, 4)`` where
          the last column is polarity (timestamp column ignored)
        - any iterable of ``(t, x, y, polarity)`` or ``(x, y, polarity)``
          tuples
        """
        np = _require_numpy()
        x_arr, y_arr, p_arr = self._extract_xyp(events, np)
        if x_arr.size == 0:
            return DensityFeature(
                vector=np.zeros(self.feature_dim, dtype=np.float64),
                grid_shape=self.grid_shape,
                event_count=0,
            )

        # Polarity filter
        if self._polarity == "on":
            mask = p_arr > 0
        elif self._polarity == "off":
            mask = p_arr <= 0
        else:
            mask = np.ones_like(p_arr, dtype=bool)
        x_arr = x_arr[mask]
        y_arr = y_arr[mask]

        # Bin into the coarse grid
        col = np.clip((x_arr * self._grid_w // self._sensor_w).astype(np.int64),
                      0, self._grid_w - 1)
        row = np.clip((y_arr * self._grid_h // self._sensor_h).astype(np.int64),
                      0, self._grid_h - 1)
        flat_idx = row * self._grid_w + col
        counts = np.bincount(flat_idx, minlength=self.feature_dim).astype(np.float64)
        return DensityFeature(
            vector=counts,
            grid_shape=self.grid_shape,
            event_count=int(x_arr.size),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_xyp(events, np):
        """Pull (x, y, polarity) arrays out of various event shapes."""
        if hasattr(events, "dtype") and events.dtype.names:
            # Structured array
            return (
                np.asarray(events["x"]),
                np.asarray(events["y"]),
                np.asarray(events["polarity"]),
            )
        arr = np.asarray(events)
        if arr.ndim == 2 and arr.shape[1] == 3:
            return arr[:, 0], arr[:, 1], arr[:, 2]
        if arr.ndim == 2 and arr.shape[1] == 4:
            # (t, x, y, polarity) — drop t
            return arr[:, 1], arr[:, 2], arr[:, 3]
        if arr.ndim == 1 and arr.size == 0:
            empty = np.empty(0, dtype=np.int64)
            return empty, empty, empty
        raise ValueError(
            "events must be a structured array, (n,3), (n,4), or empty"
        )
