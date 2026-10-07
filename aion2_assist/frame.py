"""Frame = full-screen RGB pixels (rows of bytes, 3 bytes/pixel) + capture timestamp.

Rectangles are (x, y, w, h) in FULL-FRAME coordinates only. A crop is never
passed around as if it were a frame; sizes are always checked against the profile.
"""
from dataclasses import dataclass
from typing import List, NamedTuple, Sequence, Tuple

Rect = Tuple[int, int, int, int]


class Reading(NamedTuple):
    value: object
    confidence: float
    ts: float  # timestamp of the frame the reading came from


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    rows: Sequence[bytes]  # len == height, each len == 3*width
    ts: float

    def __post_init__(self):
        if len(self.rows) != self.height or any(len(r) != 3 * self.width for r in self.rows):
            raise ValueError("frame rows do not match width/height")

    def crop_rows(self, rect: Rect) -> List[bytes]:
        x, y, w, h = rect
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            raise ValueError(f"rect {rect} outside {self.width}x{self.height} frame")
        return [r[3 * x:3 * (x + w)] for r in self.rows[y:y + h]]

    def flat(self, rect: Rect) -> bytes:
        return b"".join(self.crop_rows(rect))


def mean_rgb(flat: bytes) -> Tuple[float, float, float]:
    n = len(flat) // 3
    return (sum(flat[0::3]) / n, sum(flat[1::3]) / n, sum(flat[2::3]) / n)


def mean_vs(flat: bytes) -> Tuple[float, float]:
    """Mean brightness (max channel) and mean saturation (0..255 scale)."""
    n = len(flat) // 3
    vs = ss = 0.0
    for i in range(0, len(flat), 3):
        r, g, b = flat[i], flat[i + 1], flat[i + 2]
        hi, lo = max(r, g, b), min(r, g, b)
        vs += hi
        ss += (hi - lo) * 255.0 / hi if hi else 0.0
    return vs / n, ss / n


def template_score(flat: bytes, template: Sequence[int]) -> float:
    """1.0 = identical, 0.0 = maximally different (mean abs diff / 255)."""
    if len(flat) != len(template):
        raise ValueError("template size mismatch")
    return 1.0 - sum(abs(a - b) for a, b in zip(flat, template)) / (255.0 * len(flat))


def dist(a: Sequence[float], b: Sequence[float]) -> float:
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5
