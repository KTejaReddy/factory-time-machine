"""Pydantic contracts.

Includes the strict AI-output contract required by the product spec:

    {"finding", "evidence": [...], "confidence", "limitations": [...], "recommendation"}

Model output is validated here and is never interpreted as an instruction or a
database command.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator

Decision = Literal["confirmed", "rejected", "needs_review"]


class BaseModel(_PydanticBaseModel):
    """Project base model.

    ``model_*`` field names are used deliberately (``model_key``, ``model_metrics``),
    so the pydantic protected namespace is released for this project only.
    """

    model_config = ConfigDict(protected_namespaces=())


# ---------------------------------------------------------------------------
# Dataset domain
# ---------------------------------------------------------------------------
class DatasetIssue(BaseModel):
    severity: Literal["info", "warning", "error"]
    message: str
    rows_affected: int = 0
    columns: List[str] = Field(default_factory=list)


class ColumnProfile(BaseModel):
    name: str
    group: str
    dtype: str = "double"
    count: int = 0
    missing: int = 0
    mean: Optional[float] = None
    std: Optional[float] = None
    minimum: Optional[float] = None
    p05: Optional[float] = None
    median: Optional[float] = None
    p95: Optional[float] = None
    maximum: Optional[float] = None
    documented: bool = True
    note: str = ""


class DatasetSummary(BaseModel):
    key: str
    label: str
    kind: Literal["images", "simulation", "surrogate_design"]
    present: bool
    rows: int = 0
    columns: int = 0
    source_file: str = ""
    description: str = ""
    warnings: List[str] = Field(default_factory=list)
    extras: Dict[str, Any] = Field(default_factory=dict)


class ProcessingStatus(BaseModel):
    state: Literal["idle", "running", "ready", "failed"]
    progress: float = 0.0
    step: str = ""
    message: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    seconds: Optional[float] = None
    stages: Dict[str, float] = Field(default_factory=dict)
    error: Optional[str] = None


class DatasetOverview(BaseModel):
    status: ProcessingStatus
    datasets: List[DatasetSummary]
    issues: List[DatasetIssue]
    linkage: Dict[str, Any]
    capabilities: Dict[str, Any]
    provenance: Dict[str, Any]


# ---------------------------------------------------------------------------
# Production analytics
# ---------------------------------------------------------------------------
class StationMetric(BaseModel):
    station: str
    stage: str
    capacity: Optional[float] = None
    units: str = ""
    utilization: Optional[float] = None
    utilization_source: Literal["dataset", "recomputed", "not_available"] = "dataset"
    queue_mean: Optional[float] = None
    queue_max: Optional[float] = None
    wip: Optional[float] = None
    throughput: Optional[float] = None
    cycle_or_time: Optional[float] = None
    headroom: Optional[float] = None
    bottleneck_score: Optional[float] = None
    rank: Optional[int] = None
    evidence: List[str] = Field(default_factory=list)
    unavailable: List[str] = Field(default_factory=list)


class ProductionSnapshot(BaseModel):
    model_key: str
    model_label: str
    horizon_seconds: float
    throughput_total: Optional[float] = None
    throughput_unit: str = ""
    wip_total: Optional[float] = None
    stations: List[StationMetric]
    bottleneck_candidates: List[StationMetric]
    calibration: Dict[str, Any]
    utilisation_profile: List[Dict[str, Any]]
    unavailable: List[str] = Field(default_factory=list)


class DemandResponsePoint(BaseModel):
    demand: int
    runs: int
    parts_per_hour: float
    utilisation: Dict[str, float]
    waiting_time: Dict[str, float]


class DemandResponse(BaseModel):
    model_key: str
    factor: str
    factor_levels: List[int]
    metric_label: str
    points: List[DemandResponsePoint]
    saturation: Dict[str, Any]
    surrogate: Dict[str, Any]


class AnomalyRun(BaseModel):
    run_index: int
    score: float
    parts: Optional[float] = None
    drivers: List[Dict[str, Any]] = Field(default_factory=list)


class AnomalyReport(BaseModel):
    model_key: str
    n_rows: int
    n_scored: int
    threshold: float
    anomalous_fraction: float
    top: List[AnomalyRun]
    feature_importance: List[Dict[str, Any]]
    method: str
    limitations: List[str]


# ---------------------------------------------------------------------------
# Forensic timeline / root cause
# ---------------------------------------------------------------------------
class DivergenceEvent(BaseModel):
    order: int
    station: str
    stage: str
    value: Optional[float]
    baseline: Optional[float]
    z: Optional[float]
    severity: Literal["low", "moderate", "high"]
    metric: str = ""
    evidence: List[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    claim: str
    value: Optional[float] = None
    unit: str = ""
    source: str = ""
    strength: Literal["strong", "moderate", "weak", "assumed"] = "moderate"


class ForensicCase(BaseModel):
    case_id: str
    label: str
    scope: str
    selection: Dict[str, Any]
    baseline_definition: str
    outcome: Dict[str, Any]
    timeline: List[DivergenceEvent]
    first_divergence: Optional[DivergenceEvent] = None
    co_occurring: List[DivergenceEvent] = Field(default_factory=list)
    evidence: List[EvidenceItem]
    confidence: float
    limitations: List[str]
    causal_caveat: str
    data_quality: List[str] = Field(default_factory=list)


class PropagationNode(BaseModel):
    id: str
    label: str
    kind: Literal["process", "state", "defect", "outcome", "economic"]
    domain: Literal["simulation", "vision", "assumed", "derived"]
    status: Literal["observed", "assumed", "unavailable"] = "observed"
    metrics: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class PropagationEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    status: Literal["observed", "assumed", "unavailable"]
    strength: Optional[float] = None
    method: str = ""
    evidence: List[str] = Field(default_factory=list)


class PropagationGraph(BaseModel):
    nodes: List[PropagationNode]
    edges: List[PropagationEdge]
    legend: Dict[str, str]
    linkage_notice: str
    limitations: List[str]


# ---------------------------------------------------------------------------
# Economics
# ---------------------------------------------------------------------------
class CostRateCard(BaseModel):
    """Engineer-supplied rates. Never inferred from the dataset.

    ``intervention_cost_*`` values price a *change*, not a loss: no dataset exports
    them, so they exist only here and the repair engine refuses to rank repairs
    economically without them.
    """

    currency: str = "USD"
    margin_per_unit: Optional[float] = None
    cost_per_unit_scrapped: Optional[float] = None
    holding_cost_per_unit_hour: Optional[float] = None
    downtime_cost_per_hour: Optional[float] = None
    rework_cost_per_unit: Optional[float] = None
    #: One-off cost of adding one parallel resource (a machine, a server, an operator bay).
    intervention_cost_per_capacity_unit: Optional[float] = None
    #: One-off engineering/process-change cost that applies to time, queue, scrap,
    #: rework and downtime levers.
    intervention_cost_fixed: Optional[float] = None


class EconomicLine(BaseModel):
    label: str
    quantity: Optional[float] = None
    unit: str = ""
    rate: Optional[float] = None
    amount: Optional[float] = None
    quantity_source: str = ""
    rate_source: str = "user-supplied"
    note: str = ""


class EconomicAssessment(BaseModel):
    available: bool
    reason: str
    currency: Optional[str] = None
    lines: List[EconomicLine] = Field(default_factory=list)
    total: Optional[float] = None
    missing_variables: List[str] = Field(default_factory=list)
    dataset_supplied_values: List[str] = Field(default_factory=list)
    disclaimer: str


# ---------------------------------------------------------------------------
# What-if simulation
# ---------------------------------------------------------------------------
class ScenarioSpec(BaseModel):
    model_key: Literal["model1", "model2", "model3"] = "model3"
    name: str = "capacity relief"
    kind: Literal[
        "capacity_change", "processing_time_change", "demand_change", "sku_mix_change", "combined"
    ] = "capacity_change"
    station: Optional[str] = None
    capacity_delta: int = 0
    time_factor: float = 1.0
    demand_factor: float = 1.0
    sku_mix: Optional[Dict[str, float]] = None
    replications: int = 3
    horizon_seconds: Optional[float] = None


class StationComparison(BaseModel):
    station: str
    station_label: str = ""
    metric: str
    unit: str = ""
    current: Optional[float] = None
    simulated: Optional[float] = None
    delta: Optional[float] = None
    delta_pct: Optional[float] = None
    #: What the 'current' column actually is. Utilisation is compared against the
    #: export (validated like-for-like); queue length is compared against an
    #: unmodified run of the same engine, because the exported Queue columns are a
    #: different quantity and comparing them produced meaningless -100% deltas.
    comparison_basis: str = ""
    comparison_basis_note: str = ""
    export_reference_value: Optional[float] = None
    comparable: bool = True
    validation_residual_pct: Optional[float] = None


class ScenarioResult(BaseModel):
    scenario: ScenarioSpec
    engine: str
    engine_note: str
    horizon_seconds: float
    replications: int
    comparisons: List[StationComparison]
    summary: Dict[str, Any]
    validation: Dict[str, Any]
    economics: EconomicAssessment
    unavailable: List[str] = Field(default_factory=list)
    advisory: str


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------
class AttentionRegion(BaseModel):
    x: float
    y: float
    width: float
    height: float
    weight: float


class InspectionPrediction(BaseModel):
    specimen: str
    label: str
    label_display: str
    confidence: float
    probabilities: Dict[str, float]
    uncertain: bool
    uncertainty_reason: str
    verdict: str = ""
    defect: Optional[str] = None
    product: str = "not provided by dataset"
    #: Out-of-distribution guard: distance to the nearest training image in the
    #: network's feature space, plus the threshold it is judged against. Optional
    #: because it needs the training image cache to be present.
    ood_distance: Optional[float] = None
    ood_threshold: Optional[float] = None
    out_of_distribution: bool = False
    ood_note: str = ""
    tta_agreement: float = 0.0
    localization_available: bool = False
    localization_note: str
    attention: List[AttentionRegion] = Field(default_factory=list)
    attention_note: str
    model_metrics: Dict[str, Any] = Field(default_factory=dict)
    dataset_provenance: Dict[str, Any] = Field(default_factory=dict)


class VisionMetrics(BaseModel):
    available: bool
    message: str
    classes: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    confusion_matrix: List[List[int]] = Field(default_factory=list)
    training: Dict[str, Any] = Field(default_factory=dict)
    #: Whether defect *location* can be measured at all, with the reason.
    localization: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# External test images (test-only; never training data)
# ---------------------------------------------------------------------------
class ExternalImageEntry(BaseModel):
    """One stored external test image.

    ``training_data`` is a fixed ``False`` so every consumer can assert the
    separation rule instead of trusting a convention.
    """

    id: str
    file: str
    original_name: str = ""
    source: str
    source_detail: str = ""
    description: str = ""
    note: str = ""
    training_data: Literal[False] = False
    created_at: str = ""
    format: str = "png"
    width: Optional[int] = None
    height: Optional[int] = None
    stored_bytes: Optional[int] = None
    downscaled_to: Optional[List[int]] = None


# ---------------------------------------------------------------------------
# AI contract
# ---------------------------------------------------------------------------
#: Characters a language model may emit that are invisible or typographically
#: different from the labels in the datasets. Observed live: a soft hyphen inside
#: "Blanking", which made a real station name stop matching the dataset wherever
#: the UI looks a name up. Invisible characters are dropped; look-alike hyphens
#: and spaces become their ASCII equivalents.
_LOOKALIKE_CHARS = {
    "\u00ad": "",  # soft hyphen (invisible)
    "\u200b": "",  # zero-width space
    "\u200c": "",  # zero-width non-joiner
    "\u200d": "",  # zero-width joiner
    "\u2060": "",  # word joiner
    "\ufeff": "",  # byte-order mark
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2212": "-",  # minus sign
    "\u00a0": " ",  # non-breaking space
    "\u202f": " ",  # narrow non-breaking space
}

#: Fragments that would look like a database or shell instruction if the model ever
#: produced one. Model output is never executed anywhere in this application; this
#: only stops it from *reading* like a command. Word boundaries are applied per
#: alternative, because a boundary after "--" can never match and silently
#: disabled that branch of an earlier version of this expression.
_UNSAFE_FRAGMENTS = re.compile(
    r"(?i)\bdrop\s+table\b|\bdelete\s+from\b|\btruncate\s+table\b|;--|\brm\s+-rf\b"
)


def _normalise_text(value: Any) -> str:
    """Collapse whitespace, drop invisible characters, and neutralise command-like text."""
    text = str(value)
    for source, replacement in _LOOKALIKE_CHARS.items():
        text = text.replace(source, replacement)
    text = re.sub(r"\s+", " ", text).strip()
    return _UNSAFE_FRAGMENTS.sub("[redacted]", text)


class AIFinding(BaseModel):
    """The exact structured shape requested from, and validated for, the LLM."""

    finding: str = Field(min_length=8, max_length=2000)
    evidence: List[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: List[str] = Field(default_factory=list, max_length=20)
    recommendation: str = Field(default="", max_length=2000)

    @field_validator("evidence", "limitations")
    @classmethod
    def _clean_list(cls, value: List[str]) -> List[str]:
        out: List[str] = []
        for item in value:
            text = _normalise_text(item)
            if text:
                out.append(text[:600])
        return out

    @field_validator("finding", "recommendation")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        return _normalise_text(value)


class AINarrative(BaseModel):
    topic: str
    subject_id: str
    source: Literal["llm", "deterministic"]
    provider: str = ""
    model: str = ""
    generated_at: Optional[str] = None
    finding: AIFinding
    grounded_on: Dict[str, Any] = Field(default_factory=dict)
    cached: bool = False


class AIQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=600)
    scope: Literal["overview", "case", "station", "scenario"] = "overview"
    subject_id: Optional[str] = None
    #: Required: the answer must be based on one dataset's evidence, never on a
    #: default archive the user did not choose.
    key: str = Field(default="")


class AIStatus(BaseModel):
    llm_enabled: bool
    provider: str
    model: str
    base_url: str
    reason: str
    calls_last_hour: int
    call_limit_per_hour: int


# ---------------------------------------------------------------------------
# Human in the loop
# ---------------------------------------------------------------------------
class FeedbackIn(BaseModel):
    finding_id: str
    finding_kind: str
    finding_title: str
    decision: Decision
    note: str = ""
    engineer: str = "anonymous"
    payload: Dict[str, Any] = Field(default_factory=dict)
    #: The dataset whose finding was reviewed. Required for any new review so a
    #: verdict can never float between case files.
    dataset_key: str = ""


class FeedbackOut(BaseModel):
    item: Dict[str, Any]
    model_retrained: bool = False
    note: str


class LinkageAssumptionIn(BaseModel):
    defect_class: str
    station: str
    rationale: str = ""
    author: str = "anonymous"
    active: bool = True
