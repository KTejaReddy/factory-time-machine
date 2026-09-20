"""The documented plant description (Model 3) and the dataset registry.

Restored from DATASET_SCHEMA.md, which was measured from the source archives:
`Model 3.pdf`, `ParametersFile.xls` and the `Model *.doe` Arena files. Nothing in
here is invented; every capacity, time and column name below is documented in the
dataset or measured from the export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import MODEL_CSVS


@dataclass(frozen=True)
class StationSpec:
    key: str
    label: str
    stage: str
    route_index: int
    capacity: Optional[float] = None
    units: str = ""
    capacity_source: str = "documented (Model 3.pdf / ParametersFile.xls)"
    utilization_column: Optional[str] = None
    queue_columns: tuple = ()
    wip_columns: tuple = ()
    counter_columns: tuple = ()
    processing_time_seconds: Optional[float] = None
    time_source: str = "documented"
    time_note: str = ""
    adjustable: bool = False
    adjustable_reason: str = ""
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModelDef:
    key: str
    label: str
    stages: List[str]
    stations: List[StationSpec]
    factors: Dict[str, Optional[str]] = field(default_factory=dict)
    throughput_columns: Dict[str, str] = field(default_factory=dict)
    wip_columns: tuple = ()
    notes: List[str] = field(default_factory=list)


def _station(
    key: str,
    label: str,
    stage: str,
    route_index: int,
    capacity: float,
    units: str,
    util_col: str,
    queue_cols: tuple,
    counters: tuple = (),
    adjustable: bool = False,
    time_note: str = "",
    notes: List[str] | None = None,
) -> StationSpec:
    return StationSpec(
        key=key,
        label=label,
        stage=stage,
        route_index=route_index,
        capacity=capacity,
        units=units,
        utilization_column=util_col,
        queue_columns=queue_cols,
        wip_columns=(),
        counter_columns=counters,
        adjustable=adjustable,
        adjustable_reason=(
            "no processed-quantity counter is exported, so a capacity change to "
            "this station cannot be validated"
            if not adjustable
            else "utilisation for this station is exported, so a capacity change can be "
            "validated against the data"
        ),
        time_note=time_note,
        notes=notes or [],
    )


def _model3() -> ModelDef:
    """The shared-facility plant, exactly as `Model 3.pdf` documents it."""
    notes = [
        "Coil arrival -> BLANKING (1 resource, NORM(900,30)+v_Demand*2 s, batch to 2000 kg) -> "
        "FORKLIFT (1 unit, 180 s/60 s legs) -> PRESS 1..4 by SKU (NORM(5,0.1) s, capacity 2 each) "
        "-> ASSEMBLY CELL 1..4 (capacity 8/2/2/4, time by SKU) -> PAINT (2 conveyors, constant "
        "5400 s) -> QUALITY CHECK (TRIA(50,55,60)) -> WAREHOUSE 1..4.",
        "SKU routing: SKU1 -> Cell 1; SKU2 -> Cells 1,2,3; SKU3 -> Cells 3,4; SKU4 -> Cells 1,2,4.",
        "Assembly time by SKU: 25/15/23/17 s (NORM, sd 0.1).",
        "The export has no within-run timeline: Time_Now is constant 24 in all 605,620 rows, so "
        "rows are independent replications and no wall-clock ordering can be claimed.",
    ]
    return ModelDef(
        key="model3",
        label="Model 3 — shared-facility plant (4 SKUs)",
        stages=["Blanking", "Forklift", "Pressing", "Assembly", "Paint", "Quality"],
        stations=[
            _station("blanking", "Blanking", "Blanking", 0, 1, "coils", "Blanking_Util",
                     ("Blanking_Queue", "Blanking_SKU1_Queue", "Blanking_SKU2_Queue",
                      "Blanking_SKU3_Queue", "Blanking_SKU4_Queue"),
                     time_note="NORM(900,30) s + 2 s per unit of demand; no counter exported"),
            _station("forklift", "Forklift", "Forklift", 1, 1, "forklift", "Forklift_Util",
                     ("Forklift_Queue",), time_note="transport legs of 180 s / 60 s; no counter exported"),
            _station("press1", "Press 1", "Pressing", 2, 2, "machines", "Press1_Util", ("Press1_Queue",),
                     adjustable=True, time_note="NORM(5, 0.1) s"),
            _station("press2", "Press 2", "Pressing", 2, 2, "machines", "Press2_Util", ("Press2_Queue",),
                     adjustable=True, time_note="NORM(5, 0.1) s"),
            _station("press3", "Press 3", "Pressing", 2, 2, "machines", "Press3_Util", ("Press3_Queue",),
                     adjustable=True, time_note="NORM(5, 0.1) s"),
            _station("press4", "Press 4", "Pressing", 2, 2, "machines", "Press4_Util", ("Press4_Queue",),
                     adjustable=True, time_note="NORM(5, 0.1) s"),
            _station("cell1", "Assembly Cell 1", "Assembly", 3, 8, "subassemblies", "Cell1_Util", ("Cell1_Queue",),
                     ("c_Cell1__SKU1", "c_Cell1__SKU2", "c_Cell1__SKU4"),
                     adjustable=True,
                     notes=["SKU1 routes to Cell 1 only, so c_Cycle1 == c_Cell1_SKU1."]),
            _station("cell2", "Assembly Cell 2", "Assembly", 3, 2, "subassemblies", "Cell2_Util", ("Cell2_Queue",),
                     ("c_Cell2__SKU2", "c_Cell2__SKU4"), adjustable=True),
            _station("cell3", "Assembly Cell 3", "Assembly", 3, 2, "subassemblies", "Cell3_Util", ("Cell3_Queue",),
                     ("c_Cell3__SKU2", "c_Cell3__SKU3"), adjustable=True),
            _station("cell4", "Assembly Cell 4", "Assembly", 3, 4, "subassemblies", "Cell4_Util", ("Cell4_Queue",),
                     ("c_Cell4__SKU3", "c_Cell4__SKU4"), adjustable=True,
                     notes=[
                         "DATASET CONTRADICTION: the export's utilisation implies ~5 parallel resources "
                         "while Model 3.pdf documents 4. Reported as a finding, never tuned away.",
                     ]),
            _station("paint", "Paint", "Paint", 4, 2, "conveyors", "Paint1_Util", ("Paint1_Queue", "Paint2_Queue"),
                     time_note="constant 5400 s conveyor delay; no counter exported"),
            _station("quality", "Quality Check", "Quality", 5, 1, "inspector", "Quality_Util", ("Quality_Queue",),
                     time_note="TRIA(50, 55, 60) s; no counter exported"),
        ],
        factors={"demand": None, "sku_mix": "c_Cell1..4__SKU1..4 counters"},
        throughput_columns={"c_TotalProducts": "assembled parts per replication"},
        wip_columns=(
            "Blanking_Queue", "Blanking_SKU1_Queue", "Blanking_SKU2_Queue", "Blanking_SKU3_Queue",
            "Blanking_SKU4_Queue", "Press1_Queue", "Press2_Queue", "Press3_Queue", "Press4_Queue",
            "Cell1_Queue", "Cell2_Queue", "Cell3_Queue", "Cell4_Queue",
            "Warehouse1_Queue", "Warehouse2_Queue", "Warehouse3_Queue", "Warehouse4_Queue",
        ),
        notes=notes,
    )


def _model2() -> ModelDef:
    return ModelDef(
        key="model2",
        label="Model 2 — two parallel feeds to assembly",
        stages=["Drilling", "Milling", "Assembly"],
        stations=[
            _station("drilling", "Drilling", "Drilling", 0, 1, "machine", "Drilling Utilization",
                     ("Drilling Queue Time",), adjustable=True, time_note="TRIA(2,3,4)"),
            _station("milling", "Milling", "Milling", 0, 1, "machine", "Milling Utilization",
                     ("Milling Queue Time",), adjustable=True, time_note="TRIA(2,3,4)"),
            _station("assembly", "Assembly", "Assembly", 1, 1, "machine", "Assembly Utilization",
                     ("Assembly Queue Time",), adjustable=True, time_note="TRIA(2,3,4)"),
        ],
        factors={"demand": "Demand"},
        throughput_columns={"Entities Out": "assembled entities out per run"},
        wip_columns=("Part 1 Stored", "Part 2 Stored"),
        notes=[
            "Route: two parallel feeds (Drilling, Milling) -> storage -> forklift batch -> Assembly.",
            "Demand is a designed input factor at integer levels 1-20 (150 runs per level).",
        ],
    )


def _model1() -> ModelDef:
    return ModelDef(
        key="model1",
        label="Model 1 — serial line",
        stages=["Drilling", "Milling", "Assembly"],
        stations=[
            _station("drilling", "Drilling", "Drilling", 0, 1, "machine", "Drilling Util",
                     ("Drilling Waiting Time",), adjustable=True, time_note="TRIA(2,3,4)"),
            _station("milling", "Milling", "Milling", 0, 1, "machine", "Milling Util",
                     ("Milling Waiting Time",), adjustable=True, time_note="TRIA(2,3,4)"),
            _station("assembly", "Assembly", "Assembly", 1, 1, "machine", "Assembly Util",
                     ("Assembly Waiting Time",), adjustable=True, time_note="TRIA(2,3,4)"),
        ],
        factors={"demand": "Demand"},
        throughput_columns={"Parts per hour": "parts per hour", "Total parts": "parts per run"},
        wip_columns=("Drilling Waiting Time", "Milling Waiting Time", "Assembly Waiting Time"),
        notes=["Route: arrival -> Drilling -> Milling -> Assembly -> store."],
    )


MODELS: Dict[str, ModelDef] = {m.key: m for m in (_model3(), _model2(), _model1())}


def model_for(key: str, catalog: Any = None) -> ModelDef:
    if key in MODELS:
        return MODELS[key]
    if catalog and key.startswith("user:"):
        return _build_user_model(key, catalog)
    raise KeyError(f"dataset '{key}' not found in registry and no catalog provided for dynamic generation")

def _build_user_model(key: str, catalog: Any) -> ModelDef:
    profile = catalog.profile_map(key)
    m = profile.get("manufacturing", {}) or {}
    station_cols = m.get("stations", [])
    process_vars = m.get("process_vars", [])
    
    stations = []
    if station_cols:
        # Long format: we don't know the exact stations until we read the data,
        # but ModelDef expects a list of StationSpec. 
        # We will create a single "Dynamic Plant" spec here, but bottleneck.py 
        # will override station_rows to extract the actual stations from the dataframe.
        pass
    else:
        # Wide format: try to group process_vars by prefix (e.g. Cell1_Util, Cell1_Queue)
        prefixes = set()
        for p in process_vars:
            parts = p.split("_")
            if len(parts) > 1:
                prefixes.add(parts[0])
        for i, prefix in enumerate(sorted(prefixes)):
            util_col = next((c for c in process_vars if c.startswith(prefix) and "util" in c.lower()), None)
            queue_cols = [c for c in process_vars if c.startswith(prefix) and ("queue" in c.lower() or "wip" in c.lower())]
            if util_col or queue_cols:
                stations.append(
                    _station(
                        key=prefix.lower(),
                        label=prefix,
                        stage=prefix,
                        route_index=i,
                        capacity=1,
                        units="units",
                        util_col=util_col,
                        queue_cols=tuple(queue_cols)
                    )
                )

    return ModelDef(
        key=key,
        label=f"Uploaded Dataset ({key})",
        stages=["Unknown"],
        stations=stations,
        notes=["Dynamically generated from uploaded dataset profile."],
    )


def station_sort_key(spec: StationSpec) -> tuple:
    return (spec.route_index, spec.key)


def present_models() -> Dict[str, ModelDef]:
    """Models whose export CSV actually exists on disk."""
    return {k: v for k, v in MODELS.items() if MODEL_CSVS[k].exists()}
