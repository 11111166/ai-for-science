"""Model-neutral contracts for genetic perturbation and virtual-cell adapters.

This module deliberately contains no scientific model.  It defines the inputs,
outputs, provenance and safe empty-state behaviour that CPA, GEARS or later
models must implement before they can be called by a worker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class GeneticOperation(str, Enum):
    KNOCKOUT = "knockout"
    KNOCKDOWN = "knockdown"
    ACTIVATION = "activation"
    OVEREXPRESSION = "overexpression"


class AdapterResultStatus(str, Enum):
    COMPLETED = "completed"
    NOT_CONFIGURED = "not_configured"
    OUT_OF_DOMAIN = "out_of_domain"
    FAILED = "failed"


@dataclass(frozen=True)
class CellContext:
    species: str
    tissue: str
    cell_type: str
    disease: str | None = None
    donor_id: str | None = None
    batch_id: str | None = None
    covariates: Mapping[str, str | int | float | bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("species", "tissue", "cell_type"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} is required")


@dataclass(frozen=True)
class PerturbationSpec:
    operation: GeneticOperation
    gene_ids: tuple[str, ...]
    guide_ids: tuple[str, ...] = ()
    time_hours: float | None = None

    def __post_init__(self) -> None:
        normalized = tuple(gene.strip() for gene in self.gene_ids if gene.strip())
        if not normalized:
            raise ValueError("at least one gene_id is required")
        if len(normalized) != len(set(normalized)):
            raise ValueError("gene_ids must be unique")
        if self.time_hours is not None and self.time_hours <= 0:
            raise ValueError("time_hours must be positive")
        object.__setattr__(self, "gene_ids", normalized)


@dataclass(frozen=True)
class VirtualCellRequest:
    project_id: str
    baseline_asset_id: str
    input_version: str
    context: CellContext
    perturbation: PerturbationSpec
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("project_id", "baseline_asset_id", "input_version"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} is required")


@dataclass(frozen=True)
class GeneEffect:
    gene_id: str
    predicted_log2_fold_change: float
    uncertainty: float | None = None


@dataclass(frozen=True)
class VirtualCellResult:
    status: AdapterResultStatus
    adapter_name: str
    adapter_version: str
    input_hash: str | None
    output_asset_id: str | None = None
    predicted_cell_count: int = 0
    gene_effects: tuple[GeneEffect, ...] = ()
    uncertainty: float | None = None
    out_of_domain: bool = False
    message: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


class VirtualCellAdapter(ABC):
    """Base contract for a context-aware virtual-cell prediction model."""

    name: str
    version: str

    @abstractmethod
    def predict(self, request: VirtualCellRequest) -> VirtualCellResult:
        """Predict a versioned cell-state artifact or return an explicit empty state."""


class GeneticPerturbationAdapter(VirtualCellAdapter, ABC):
    """Marker contract for CRISPR KO/KD/activation/overexpression models."""


class NotConfiguredVirtualCellAdapter(GeneticPerturbationAdapter):
    """Safe default: never fabricates expression values when no model exists."""

    name = "NotConfiguredVirtualCellAdapter"
    version = "1.0.0"

    def predict(self, request: VirtualCellRequest) -> VirtualCellResult:
        return VirtualCellResult(
            status=AdapterResultStatus.NOT_CONFIGURED,
            adapter_name=self.name,
            adapter_version=self.version,
            input_hash=None,
            message="No virtual-cell model is configured for this cell context.",
            provenance={
                "project_id": request.project_id,
                "input_version": request.input_version,
                "scientific_conclusion": False,
            },
        )
