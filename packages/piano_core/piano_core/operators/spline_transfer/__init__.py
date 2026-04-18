"""SplineTransfer — directed curve transfer between notes (i3 §4)."""

from .operator import SplineTransferDiagnostics, SplineTransferOperator
from .params import (
    VALID_MODES,
    ParameterConfig,
    SplineTransferParams,
    TransferMode,
)

__all__ = [
    "VALID_MODES",
    "ParameterConfig",
    "SplineTransferDiagnostics",
    "SplineTransferOperator",
    "SplineTransferParams",
    "TransferMode",
]
