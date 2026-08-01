"""Optional depth/IR liveness signal — interface stub only.

No depth or infrared hardware/backend is implemented in this milestone.
This is the minimal typed interface HERMES.md's M14 "optional depth or
IR support" describes, so a future hardware-specific adapter (structured
light, stereo, ToF, or IR reflectance) has a stable contract to implement
without any other M14 module changing. Nothing constructs a real
implementation of this protocol yet, and no configuration enables one.
"""

from __future__ import annotations

from typing import Protocol


class DepthIRSignal(Protocol):
    """Optional hardware-backed liveness signal, independent of passive/active checks."""

    def is_available(self) -> bool: ...

    def liveness_hint(self) -> float | None: ...
