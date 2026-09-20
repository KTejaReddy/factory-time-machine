"""End-to-end smoke test for the Factory Time Machine API.

Exercises every documented endpoint against a running server and asserts the
response shapes the frontend actually depends on (see ``frontend/src/lib/api.ts``).

    python -m uvicorn app.main:app --port 8010 --app-dir backend
    python scripts/smoke_test_api.py --base http://127.0.0.1:8010

Exit code is non-zero when any check fails, so this doubles as a CI gate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

FAILURES: list[str] = []
PASSES = 0


def call(base: str, path: str, *, method: str = "GET", body: dict | None = None, timeout: int = 300):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed = (time.perf_counter() - started) * 1000
            ctype = resp.headers.get("Content-Type", "")
            parsed = raw if ("json" not in ctype and "text" not in ctype) else raw
            if "json" in ctype:
                parsed = json.loads(raw.decode())
            return resp.status, parsed, elapsed, ctype
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        detail = exc.read().decode()[:500]
        return exc.code, detail, elapsed, "text/plain"
    except Exception as exc:  # noqa: BLE001
        elapsed = (time.perf_counter() - started) * 1000
        return 0, f"{type(exc).__name__}: {exc}", elapsed, ""


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSES
    if cond:
        PASSES += 1
        print(f"  PASS  {name}")
    else:
        FAILURES.append(f"{name} :: {detail}")
        print(f"  FAIL  {name}  {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def get(base, path, **kw):
    return call(base, path, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    args = ap.parse_args()
    base = args.base

    # ------------------------------------------------------------------ meta
    section("meta / health")
    status, body, ms, _ = get(base, "/api/health")
    check("GET /api/health", status == 200 and isinstance(body, dict), f"status={status} body={str(body)[:200]}")
    check("health declares advisory_only", isinstance(body, dict) and body.get("advisory_only") is True)
    check("health reports AI configuration", isinstance(body, dict) and isinstance(body.get("ai"), dict))

    # ------------------------------------------------- catalog readiness
    section("dataset processing status")
    deadline = time.time() + 240
    state = None
    while time.time() < deadline:
        status, body, ms, _ = call(base, "/api/datasets/status")
        if status == 200 and isinstance(body, dict):
            state = (body.get("status") or {}).get("state")
            if state == "ready":
                break
            if state == "failed":
                break
        time.sleep(3)
    check("GET /api/datasets/status reaches 'ready'", state == "ready", f"state={state}")
    if state == "ready":
        st = body["status"]
        print(f"        build {st.get('seconds')}s | stages={st.get('stages')}")
        check("status exposes progress and stages", "progress" in st and isinstance(st.get("stages"), dict))
        check("status lists datasets", isinstance(body.get("datasets"), list) and len(body["datasets"]) > 0)

    # ---------------------------------------------------- datasets + schema
    section("dataset overview / provenance / capabilities")
    status, overview, ms, _ = get(base, "/api/datasets/overview")
    check("GET /api/datasets/overview", status == 200 and isinstance(overview, dict), f"status={status} body={str(overview)[:250]}")
    if status == 200 and isinstance(overview, dict):
        for field in ("status", "datasets", "issues", "linkage", "capabilities", "provenance"):
            check(f"overview carries '{field}'", field in overview)
        dss = overview.get("datasets") or []
        keys = [d.get("key") for d in dss]
        print(f"        datasets: {keys}")
        check("overview lists datasets", len(dss) > 0)
        present = [d for d in dss if d.get("present")]
        check("at least one dataset present on disk", len(present) > 0, f"present={[d.get('key') for d in present]}")

    status, body, ms, _ = get(base, "/api/datasets/models")
    check("GET /api/datasets/models", status == 200 and isinstance(body, list), f"status={status}")
    model_keys = [m.get("key") for m in body] if isinstance(body, list) else []
    print(f"        documented models: {model_keys}")
    station_keys: list[str] = []
    if isinstance(body, list):
        for m in body:
            if m.get("key") == "model3":
                station_keys = [s.get("key") for s in (m.get("stations") or [])]
    check("model3 documents stations", len(station_keys) > 0, f"stations={station_keys[:6]}")
    if station_keys:
        print(f"        model3 stations: {station_keys}")

    status, body, ms, _ = get(base, "/api/datasets/model3/columns")
    check("GET /api/datasets/model3/columns", status == 200 and isinstance(body, list), f"status={status}")
    docs = [c for c in body if c.get("documented")] if isinstance(body, list) else []
    check("columns flag documented vs undocumented", any(c.get("documented") for c in body) and any(not c.get("documented") for c in body) if isinstance(body, list) else False)

    status, body, ms, _ = get(base, "/api/datasets/model3/quality")
    check("GET /api/datasets/model3/quality", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        check("quality reports rows/columns", body.get("rows", 0) > 0 and body.get("columns", 0) > 0)

    status, body, ms, _ = get(base, "/api/datasets/linkage")
    check("GET /api/datasets/linkage", status == 200, f"status={status}")
    if status == 200 and isinstance(body, dict):
        check("linkage explains why no join exists", bool(body.get("arguments") or body.get("linkable") is False))

    status, body, ms, _ = get(base, "/api/datasets/design")
    check("GET /api/datasets/design (MATLAB DOE arrays)", status == 200, f"status={status}")

    status, body, ms, _ = get(base, "/api/datasets/specimens?per_class=3")
    check("GET /api/datasets/specimens", status == 200, f"status={status}")

    # ------------------------------------------------------------- production
    section("production / bottleneck")
    status, body, ms, _ = get(base, "/api/production/snapshot?key=model3")
    check("GET /api/production/snapshot", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        check("snapshot lists stations", len(body.get("stations") or []) > 0)
        check("snapshot carries a calibration block", isinstance(body.get("calibration"), dict))
        print(f"        throughput_total={body.get('throughput_total')} wip_total={body.get('wip_total')}")

    status, body, ms, _ = get(base, "/api/production/bottlenecks?key=model3")
    check("GET /api/production/bottlenecks", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        ranking = body.get("ranking") or []
        check("bottleneck ranking non-empty", len(ranking) > 0, f"n={len(ranking)}")
        if ranking:
            top = ranking[0]
            print(f"        top: {top.get('label')} util={top.get('utilization')} score={top.get('bottleneck_score')}")
            check("candidate justifies itself with evidence", bool(top.get("evidence")))

    status, body, ms, _ = get(base, "/api/production/anomalies?key=model3&top=10")
    check("GET /api/production/anomalies", status == 200, f"status={status}")
    if status == 200 and isinstance(body, dict):
        check("anomaly report declares its method", bool(body.get("method")))
        check("anomaly report declares limitations", isinstance(body.get("limitations"), list))

    status, body, ms, _ = get(base, "/api/production/regimes?key=model3")
    check("GET /api/production/regimes", status == 200, f"status={status}")
    status, body, ms, _ = get(base, "/api/production/calibration?key=model3")
    check("GET /api/production/calibration (utilisation identity)", status == 200, f"status={status} body={str(body)[:250]}")
    status, body, ms, _ = get(base, "/api/production/association?key=model3")
    check("GET /api/production/association", status == 200, f"status={status}")
    status, body, ms, _ = get(base, "/api/production/demand-response?key=model1")
    check("GET /api/production/demand-response", status == 200, f"status={status}")

    # ------------------------------------------------------------- forensics
    section("forensic cases")
    status, body, ms, _ = get(base, "/api/diagnostics/cases?key=model3")
    check("GET /api/diagnostics/cases", status == 200, f"status={status} body={str(body)[:250]}")
    case_ids: list[str] = []
    if status == 200 and isinstance(body, dict):
        case_ids = [c.get("case_id") for c in (body.get("cases") or [])]
        print(f"        cases: {case_ids}")
        check("cases are listed", len(case_ids) > 0)

    for cid in case_ids[:3]:
        status, body, ms, _ = get(base, f"/api/diagnostics/case?case_id={cid}&key=model3")
        check(f"GET /api/diagnostics/case ({cid})", status == 200, f"status={status} body={str(body)[:250]}")
        if status == 200 and isinstance(body, dict):
            check(f"  {cid}: has an ordered timeline", len(body.get("timeline") or []) > 0)
            check(f"  {cid}: separates evidence list", isinstance(body.get("evidence"), list))
            check(f"  {cid}: carries limitations", isinstance(body.get("limitations"), list))
            check(f"  {cid}: carries confidence_basis", isinstance(body.get("confidence_basis"), list))
            check(f"  {cid}: states the causal caveat", bool(body.get("causal_caveat")))
            check(f"  {cid}: states its baseline definition", bool(body.get("baseline_definition")))
            fd = body.get("first_divergence")
            if cid == case_ids[0] and fd:
                print(f"        first divergence: {fd.get('station')} {fd.get('metric')} z={fd.get('z')} severity={fd.get('severity')}")

    status, body, ms, _ = get(base, "/api/diagnostics/propagation?key=model3")
    check("GET /api/diagnostics/propagation", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        nodes = body.get("nodes") or []
        edges = body.get("edges") or []
        check("graph has nodes", len(nodes) > 0, f"n={len(nodes)}")
        check("graph has edges", len(edges) > 0, f"n={len(edges)}")
        kinds = sorted({n.get("kind") for n in nodes})
        statuses = sorted({n.get("status") for n in nodes})
        print(f"        node kinds={kinds} statuses={statuses}")
        check("graph spans process -> outcome/economics", {"process"} <= set(kinds) and ({"outcome"} & set(kinds) or {"economic"} & set(kinds)))
        ids = {n.get("id") for n in nodes}
        check("every edge references real nodes", all(e.get("source") in ids and e.get("target") in ids for e in edges))
        check("edges carry status labels", all(e.get("status") for e in edges))
        check("graph states its linkage notice", bool(body.get("linkage_notice")))
        econ_nodes = [n for n in nodes if n.get("kind") == "economic"]
        if econ_nodes:
            mark = econ_nodes[0].get("status")
            print(f"        economic node status: {mark}")
            check("economic nodes are gated when unsupported", mark in ("unavailable", "observed"), f"status={mark}")

    status, body, ms, _ = get(base, "/api/diagnostics/stations?key=model3")
    check("GET /api/diagnostics/stations", status == 200, f"status={status}")
    if status == 200 and isinstance(body, dict):
        rows = body.get("stations") or []
        check("station rows returned", len(rows) > 0)
        if rows:
            check("station row carries baseline + severity", "severity" in rows[0] and "baseline" in rows[0])

    # ------------------------------------------------------------- economics
    section("economics (gated)")
    status, body, ms, _ = get(base, "/api/economics/assessment?key=model3")
    check("GET /api/economics/assessment", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        check("assessment states availability", "available" in body)
        print(f"        available={body.get('available')} reason={str(body.get('reason'))[:90]}")
        check("assessment always carries a disclaimer", bool(body.get("disclaimer")))
        check("assessment names missing variables", isinstance(body.get("missing_variables"), list))
        if body.get("available") is False:
            check("unsupported economics returns no invented total", body.get("total") is None)

    status, body, ms, _ = get(base, "/api/economics/requirements")
    check("GET /api/economics/requirements", status == 200, f"status={status}")

    status, body, ms, _ = call(
        base,
        "/api/economics/assessment?key=model3",
        method="POST",
        body={"currency": "USD", "margin_per_unit": 12.5, "cost_per_unit_scrapped": 6.0, "holding_cost_per_unit_hour": 0.4},
    )
    check("POST /api/economics/assessment with a user rate card", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        lines = body.get("lines") or []
        check("rate-card run produces line items", len(lines) > 0)
        if lines:
            check("each line records quantity_source", all("quantity_source" in l for l in lines))
            print(f"        total={body.get('total')} {body.get('currency')} over {len(lines)} lines")

    # ------------------------------------------------------------ inspection
    section("visual inspection")
    status, body, ms, _ = get(base, "/api/inspection/metrics")
    check("GET /api/inspection/metrics", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        print(f"        available={body.get('available')} classes={body.get('classes')}")
        if body.get("available"):
            check("metrics carry a confusion matrix", bool(body.get("confusion_matrix")))
        check("metrics declare localization availability", "localization" in body or "localization_available" in body or body.get("available") is False)

    status, body, ms, _ = get(base, "/api/inspection/samples?per_class=3")
    check("GET /api/inspection/samples", status == 200, f"status={status} body={str(body)[:250]}")
    specimens: list[str] = []
    if status == 200 and isinstance(body, dict):
        samples = body.get("samples") or {}
        # shape: {class: [{name, class, index}, ...]} or {class: [name, ...]}
        for cls, items in samples.items():
            for it in items:
                if isinstance(it, dict):
                    specimens.append(it.get("name") or it.get("path") or "")
                else:
                    specimens.append(str(it))
        specimens = [s for s in specimens if s]
        print(f"        classes={list(samples)} specimens={len(specimens)}")
        check("sample specimens listed", len(specimens) > 0)
        check("samples state the no-identifier limitation", bool(body.get("note")))

    if specimens:
        name = specimens[0]
        status, body, ms, _ = get(base, f"/api/inspection/thumbnail?name={urllib.request.quote(name)}&size=128")
        check("GET /api/inspection/thumbnail", status == 200 and isinstance(body, bytes) and len(body) > 100, f"status={status} bytes={len(body) if isinstance(body, bytes) else '-'}")
        status, img, ms, _ = get(base, f"/api/inspection/image?name={urllib.request.quote(name)}")
        check("GET /api/inspection/image", status == 200 and isinstance(img, bytes) and len(img) > 100, f"status={status}")

        status, body, ms, _ = call(base, f"/api/inspection/predict?specimen={urllib.request.quote(name)}", method="POST")
        check("POST /api/inspection/predict", status == 200, f"status={status} body={str(body)[:300]}")
        if status == 200 and isinstance(body, dict):
            check("prediction carries label + confidence", bool(body.get("label")) and isinstance(body.get("confidence"), (int, float)))
            check("prediction carries an uncertainty flag", "uncertain" in body and "uncertainty_reason" in body)
            check("prediction states localization availability", "localization_available" in body)
            check("localization is not fabricated", body.get("localization_available") is False or bool(body.get("attention")))
            print(f"        predict -> {body.get('label_display')} conf={body.get('confidence'):.3f} uncertain={body.get('uncertain')} tta={body.get('tta_agreement')}")

    status, body, ms, _ = call(base, "/api/inspection/predict?specimen=definitely-not-in-archive/999", method="POST")
    check("unknown specimen returns 404, not a fake prediction", status == 404, f"status={status}")

    # ------------------------------------------------------------ simulation
    section("what-if simulation")
    status, options, ms, _ = get(base, "/api/simulation/options")
    check("GET /api/simulation/options", status == 200, f"status={status} body={str(options)[:250]}")
    adjustable: list[str] = []
    if status == 200 and isinstance(options, dict):
        adjustable = [s.get("key") for s in (options.get("stations") or [])]
        print(f"        adjustable stations: {adjustable}")
        check("options list adjustable stations", len(adjustable) > 0)
        if adjustable:
            check("every offered station is adjustable", all(
                (s.get("adjustable") or {}).get("capacity") is True for s in options["stations"]
            ))
        check("options state which stations are not adjustable", isinstance(options.get("not_adjustable"), list))

    status, body, ms, _ = get(base, "/api/simulation/validation")
    check("GET /api/simulation/validation", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        val = body.get("validation") or {}
        check("re-simulation is validated against the export", bool(val), str(list(body))[:200])
        if val:
            checks = val.get("checks") or val.get("comparisons") or val.get("stations") or []
            n = len(checks) if isinstance(checks, list) else "dict"
            print(f"        validation: {n} comparisons, overall={val.get('overall') or val.get('passed')}")
        check("simulation declares itself advisory", bool(body.get("advisory")))

    station = (adjustable or station_keys or [None])[0]
    spec = {
        "model_key": "model3",
        "name": "smoke: capacity relief",
        "kind": "capacity_change",
        "station": station,
        "capacity_delta": 1,
        "time_factor": 1.0,
        "demand_factor": 1.0,
        "replications": 2,
    }
    status, body, ms, _ = call(base, "/api/simulation/run", method="POST", body={"spec": spec, "rates": None})
    check("POST /api/simulation/run", status == 200, f"status={status} body={str(body)[:300]}")
    if status == 200 and isinstance(body, dict):
        check("scenario returns comparisons", len(body.get("comparisons") or []) > 0)
        check("scenario carries a validation block", isinstance(body.get("validation"), dict))
        check("scenario is labelled advisory", bool(body.get("advisory")))
        check("scenario gates economics", isinstance(body.get("economics"), dict))
        summary = body.get("summary") or {}
        print(f"        engine={body.get('engine')} replications={body.get('replications')} summary_keys={list(summary)}")
        comps = body.get("comparisons") or []
        if comps:
            c = comps[0]
            check("comparison carries current vs simulated", "current" in c and "simulated" in c)
            # The 'current' column must never mix the export with this engine: doing so
            # reported -100% queue 'improvements' that were a definition mismatch.
            util_rows = [r for r in comps if r["metric"] == "utilization"]
            queue_rows = [r for r in comps if r["metric"].startswith("queue")]
            check("utilisation rows declare the export as their baseline",
                  bool(util_rows) and all(r.get("comparison_basis") == "export (validated)" for r in util_rows))
            check("queue rows declare the engine's own baseline",
                  bool(queue_rows) and all(r.get("comparison_basis") == "re-simulation baseline" for r in queue_rows))
            check("non-like-for-like rows explain themselves",
                  all(r.get("comparison_basis_note") for r in queue_rows))
            check("no negative queue length is ever reported",
                  all((r["current"] or 0) >= 0 and (r["simulated"] or 0) >= 0 for r in queue_rows))

    status, body, ms, _ = get(base, "/api/simulation/history?limit=5")
    check("GET /api/simulation/history", status == 200 and isinstance(body, list), f"status={status}")
    if status == 200 and isinstance(body, list) and body:
        check("history rows are stored runs", "scenario" in body[0] or "name" in body[0], str(list(body[0]))[:180])

    for bad_station, why in (("no_such_station", "unknown"), ("blanking", "documented but not adjustable"), ("paint1", "unbounded buffer")):
        implausible = dict(spec, kind="capacity_change", station=bad_station, capacity_delta=1)
        status, body, ms, _ = call(base, "/api/simulation/run", method="POST", body={"spec": implausible, "rates": None})
        check(f"{why} station is rejected, not silently simulated", status in (400, 409, 422), f"status={status} body={str(body)[:160]}")

    zero = dict(spec, kind="capacity_change", station=station, capacity_delta=0)
    status, body, ms, _ = call(base, "/api/simulation/run", method="POST", body={"spec": zero, "rates": None})
    check("a no-op capacity change is rejected", status in (400, 409, 422), f"status={status}")

    # ----------------------------------------------------------- AI API
    section("AI investigator")
    status, body, ms, _ = get(base, "/api/ai/status")
    check("GET /api/ai/status", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        print(f"        llm_enabled={body.get('llm_enabled')} provider={body.get('provider')} reason={str(body.get('reason'))[:70]}")
        check("status explains enablement", bool(body.get("reason")))
        check("usage is rate limited", isinstance(body.get("call_limit_per_hour"), int))

    status, body, ms, _ = get(base, "/api/ai/narrative?topic=overview&key=model3")
    check("GET /api/ai/narrative (overview)", status == 200, f"status={status} body={str(body)[:300]}")
    if status == 200 and isinstance(body, dict):
        check("narrative declares its source", body.get("source") in ("llm", "deterministic"))
        finding = body.get("finding") or {}
        for field in ("finding", "evidence", "confidence", "limitations", "recommendation"):
            check(f"narrative finding carries '{field}'", field in finding)
        check("narrative shows the evidence it was grounded on", isinstance(body.get("grounded_on"), dict))
        print(f"        source={body.get('source')} model={body.get('model')} conf={finding.get('confidence')}")

    if case_ids:
        status, body, ms, _ = get(base, f"/api/ai/narrative?topic=case&subject_id={case_ids[0]}&key=model3")
        check("GET /api/ai/narrative (case)", status == 200, f"status={status} body={str(body)[:250]}")

    status, body, ms, _ = call(
        base,
        "/api/ai/ask",
        method="POST",
        body={"question": "Which station is most constrained and why?", "scope": "overview", "subject_id": None},
    )
    check("POST /api/ai/ask", status == 200, f"status={status} body={str(body)[:300]}")
    if status == 200 and isinstance(body, dict):
        check("ask returns the validated finding contract", all(k in (body.get("finding") or {}) for k in ("finding", "evidence", "confidence", "limitations", "recommendation")))

    status, body, ms, _ = get(base, "/api/ai/logs?limit=10")
    check("GET /api/ai/logs", status == 200, f"status={status}")

    # ------------------------------------------------ human in the loop
    section("engineer review (human-in-the-loop)")
    payload = {
        "finding_id": "smoke-test-finding",
        "finding_kind": "diagnostic_case",
        "finding_title": "Smoke test finding",
        "decision": "needs_review",
        "note": "created by scripts/smoke_test_api.py",
        "engineer": "smoke-test",
        "payload": {"case_id": case_ids[0] if case_ids else None},
    }
    status, body, ms, _ = call(base, "/api/review/feedback", method="POST", body=payload)
    check("POST /api/review/feedback", status == 200, f"status={status} body={str(body)[:250]}")
    if status == 200 and isinstance(body, dict):
        check("response states no retraining happened", body.get("model_retrained") is False, str(body.get("model_retrained")))

    status, body, ms, _ = get(base, "/api/review/feedback?limit=50")
    check("GET /api/review/feedback", status == 200 and isinstance(body, list), f"status={status}")
    if status == 200 and isinstance(body, list):
        check("feedback was persisted", any(i.get("finding_id") == "smoke-test-finding" for i in body), f"n={len(body)}")
        if body:
            first = body[0]
            for field in ("finding_id", "decision", "created_at"):
                check(f"feedback row stores '{field}'", field in first, str(list(first))[:180])

    status, body, ms, _ = get(base, "/api/review/summary")
    check("GET /api/review/summary", status == 200, f"status={status}")
    if status == 200 and isinstance(body, dict):
        check("summary counts decisions", isinstance(body.get("counts"), dict))
        check("summary is honest about retraining", (body.get("retraining") or {}).get("implemented") is False)

    status, body, ms, _ = get(base, "/api/review/assumptions")
    check("GET /api/review/assumptions", status == 200, f"status={status}")

    status, body, ms, _ = call(
        base,
        "/api/review/assumptions",
        method="POST",
        body={"defect_class": "crack", "station": station or "cell1_r1", "rationale": "smoke test declared edge", "author": "smoke-test", "active": True},
    )
    check("POST /api/review/assumptions", status == 200, f"status={status} body={str(body)[:250]}")
    created_id = None
    if status == 200 and isinstance(body, dict):
        created_id = (body.get("assumption") or {}).get("id")
        check("assumption is marked as declared, not derived", "assumed" in json.dumps(body).lower() or bool((body.get("assumption") or {}).get("id")))
    if created_id:
        status, body, ms, _ = get(base, "/api/diagnostics/propagation?key=model3")
        if status == 200 and isinstance(body, dict):
            check("declared edge appears in the propagation graph", any(e.get("status") == "assumed" for e in (body.get("edges") or [])))
        status, _, ms, _ = call(base, f"/api/review/assumptions/{created_id}", method="DELETE")
        check("DELETE /api/review/assumptions/{id}", status == 200, f"status={status}")

    # ------------------------------------------------------- error handling
    section("error handling")
    status, body, ms, _ = get(base, "/api/production/snapshot?key=not_a_model")
    check("unknown dataset -> 404", status == 404, f"status={status}")
    status, body, ms, _ = get(base, "/api/diagnostics/case?case_id=run:99999999")
    check("unresolvable case id -> 4xx with a message", status in (400, 404, 409), f"status={status}")

    # ------------------------------------------------------------- frontend
    section("frontend delivery")
    status, body, ms, _ = get(base, "/")
    check("GET / serves the built SPA", status == 200, f"status={status}")

    print(f"\n{'=' * 62}")
    print(f"{PASSES} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
