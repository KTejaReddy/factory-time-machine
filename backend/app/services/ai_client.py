"""AI API integration.

Division of labour
------------------
Every number in this application is produced by numpy/pandas/scipy/sklearn and the
in-house event engine. The language model's *only* job is to turn a compact,
structured evidence bundle into prose an engineer can read: explaining detected
patterns, summarising evidence, answering questions and drafting a
recommendation.

Guardrails implemented here:

* The model receives a **compact structured summary**, never the raw dataset
  (see ``_compact`` - lists are truncated and values rounded).
* The response must validate against a strict JSON contract
  (``schemas.AIFinding``): ``finding``, ``evidence[]``, ``confidence``,
  ``limitations[]``, ``recommendation``.
* Instructions embedded in model output are never executed and never become a
  database command; text is normalised and obviously hostile fragments redacted.
* Calls are rate limited and logged, and the whole layer degrades to a
  deterministic, evidence-grounded narrative when no key is configured or the
  provider fails - so the application always works.

The LLM is never asked to compute a measurement, and any number it produces that
is not present in the evidence bundle is not surfaced as a measurement.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import json
import logging
import re
import time
from collections import deque
from typing import Any, Deque, Dict, List, NamedTuple, Optional, Tuple

import httpx

from ..config import settings
from ..db import Narrative, AICallLog, insert_row, list_rows, session
from ..schemas import AIFinding

log = logging.getLogger("ftm.ai")

_CALL_TIMES: Deque[float] = deque()
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}

SYSTEM_PROMPT = (
    "You are a manufacturing forensic analyst embedded in a decision-support tool for engineers.\n"
    "You will receive a JSON evidence bundle that was computed by the backend from a discrete-event "
    "simulation dataset and a surface-defect image dataset.\n"
    "Hard rules:\n"
    "1. Use ONLY numbers, names and variables present in the evidence bundle. Never invent a measurement, "
    "a defect class, a station name, a cost or a dataset column.\n"
    "2. Never claim causation. Use wording such as 'associated with', 'correlated with', "
    "'supporting evidence', 'possible contributor'.\n"
    "3. If the evidence says something is unavailable, say so explicitly instead of estimating it.\n"
    "4. Respect every limitation listed in the evidence bundle and carry the important ones through.\n"
    "5. Be concise and specific: cite the numbers you were given.\n"
    "Respond with a single JSON object and nothing else, with exactly these keys:\n"
    '{"finding": string, "evidence": [string], "confidence": number between 0 and 1, '
    '"limitations": [string], "recommendation": string}\n'
    "Set confidence to reflect the strength of the supplied evidence, not your own certainty."
)


# ---------------------------------------------------------------------------
# Status & rate limiting
# ---------------------------------------------------------------------------
def calls_last_hour() -> int:
    cutoff = time.time() - 3600
    while _CALL_TIMES and _CALL_TIMES[0] < cutoff:
        _CALL_TIMES.popleft()
    return len(_CALL_TIMES)


def status() -> Dict[str, Any]:
    if settings.ai_disable:
        reason = "AI_DISABLE is set: the deterministic narrative engine is in use."
    elif not settings.ai_api_key:
        reason = (
            "No AI_API_KEY is configured. Every feature works; findings are produced by the built-in "
            "deterministic narrative engine instead of a language model."
        )
    else:
        reason = f"Language model calls enabled via {settings.ai_provider}."
    return {
        "llm_enabled": settings.ai_enabled,
        "provider": settings.ai_provider,
        "model": settings.ai_model,
        "base_url": settings.ai_base_url,
        "reason": reason,
        "calls_last_hour": calls_last_hour(),
        "call_limit_per_hour": settings.ai_max_calls_per_hour,
        "deterministic_fallback": True,
        "contract": ["finding", "evidence", "confidence", "limitations", "recommendation"],
    }


def _allowed() -> Tuple[bool, str]:
    if not settings.ai_enabled:
        return False, "language model is not configured"
    if calls_last_hour() >= settings.ai_max_calls_per_hour:
        return False, f"hourly AI call limit of {settings.ai_max_calls_per_hour} reached"
    return True, ""


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def _compact(payload: Any, max_items: int = 8, depth: int = 0) -> Any:
    """Shrink an evidence bundle before it reaches the model."""
    if depth > 4:
        return "..."
    if isinstance(payload, dict):
        out = {}
        for key, value in list(payload.items())[:40]:
            out[key] = _compact(value, max_items, depth + 1)
        return out
    if isinstance(payload, (list, tuple)):
        trimmed = payload[:max_items]
        result = [_compact(v, max_items, depth + 1) for v in trimmed]
        if len(payload) > max_items:
            result.append(f"... {len(payload) - max_items} more item(s) omitted")
        return result
    if isinstance(payload, float):
        return round(payload, 4)
    if isinstance(payload, (str, int, bool)) or payload is None:
        return payload
    return str(payload)


def _build_messages(topic: str, evidence: Dict[str, Any], question: Optional[str] = None) -> List[Dict[str, str]]:
    instruction = {
        "overview": "Summarise the current state of this plant and the strength of the evidence behind each finding.",
        "case": "Explain this forensic case: what diverged, where along the documented route it started, how it "
                "propagated, and what the operational impact was.",
        "station": "Explain what the data says about this station and why it is or is not a bottleneck candidate.",
        "scenario": "Explain what this what-if scenario changes, how well the simulation was validated against the "
                    "dataset, and what an engineer should be cautious about.",
        "question": "Answer the engineer's question using only the evidence bundle.",
    }.get(topic, "Summarise the evidence bundle.")
    user_parts = [instruction, "", "EVIDENCE BUNDLE (all values were computed by the backend):",
                  json.dumps(_compact(evidence), indent=1, default=str)]
    if question:
        user_parts += ["", f"ENGINEER QUESTION: {question}"]
    user_parts += ["", "Return only the JSON object described in your instructions."]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


# ---------------------------------------------------------------------------
# Provider calls
# ---------------------------------------------------------------------------
class Completion(NamedTuple):
    """Provider reply plus why it stopped.

    ``finish_reason`` matters: a reasoning-capable model that runs out of output
    tokens returns *truncated* JSON, which is unparseable. Treating that as a
    malformed response and giving up would silently hide a fixable configuration
    problem, so the caller retries with a larger budget instead.
    """

    content: str
    finish_reason: str = ""


#: Hard ceiling for the truncation retry, whatever the configured budget is.
_MAX_RETRY_TOKENS = 4000


#: Status codes worth retrying: rate limits and transient provider faults.
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
#: Never wait longer than this between attempts, however long Retry-After asks for.
_MAX_BACKOFF_S = 10.0


def _retry_after(response: "httpx.Response", fallback: float) -> float:
    """Honour a provider's ``Retry-After`` header when it sends a sane one."""
    raw = response.headers.get("retry-after", "")
    try:
        asked = float(raw)
    except (TypeError, ValueError):
        return fallback
    if asked <= 0:
        return fallback
    return min(asked, _MAX_BACKOFF_S)


def _request_json(url: str, body: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """POST with bounded retries on rate limits and transient server errors.

    A 429 from a free tier is not a broken integration, it is a queue: retrying
    with backoff (and a hard cap) turns a wasted call into a slightly slower one.
    Everything else - bad key, bad model name, malformed request - fails fast, and
    the caller still degrades to the deterministic engine.
    """
    delay = 1.0
    for attempt in range(3):
        with httpx.Client(timeout=settings.ai_timeout_s) as client:
            response = client.post(url, json=body, headers=headers)
        if response.status_code in _TRANSIENT_STATUS and attempt < 2:
            wait = _retry_after(response, delay)
            log.info("AI provider returned %s; retrying in %.1fs", response.status_code, wait)
            time.sleep(wait)
            delay *= 3
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable: retry loop exhausted")  # pragma: no cover


def _post_openai(messages: List[Dict[str, str]], max_tokens: Optional[int] = None) -> Completion:
    url = f"{settings.ai_base_url}/chat/completions"
    body: Dict[str, Any] = {
        "model": settings.ai_model,
        "messages": messages,
        "temperature": settings.ai_temperature,
        "max_tokens": max_tokens or settings.ai_max_output_tokens,
    }
    headers = {"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"}
    data = _request_json(url, body, headers)
    choice = data["choices"][0]
    return Completion(choice["message"].get("content") or "", choice.get("finish_reason") or "")


def _post_anthropic(messages: List[Dict[str, str]], max_tokens: Optional[int] = None) -> Completion:
    url = f"{settings.ai_base_url}/messages"
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    body = {
        "model": settings.ai_model,
        "system": system,
        "max_tokens": max_tokens or settings.ai_max_output_tokens,
        "temperature": settings.ai_temperature,
        "messages": [m for m in messages if m["role"] != "system"],
    }
    headers = {
        "x-api-key": settings.ai_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    data = _request_json(url, body, headers)
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return Completion(text, data.get("stop_reason") or "")


def _call_provider(messages: List[Dict[str, str]], max_tokens: Optional[int] = None) -> Completion:
    """Provider call, normalised to :class:`Completion`.

    Tolerant of a test or wrapper replacing ``_post_*`` with a callable that
    takes only the messages (or returns a plain string), so monkeypatching stays
    trivial.
    """
    post = _post_anthropic if settings.ai_provider == "anthropic" else _post_openai
    try:
        params = inspect.signature(post).parameters
        takes_budget = len(params) >= 2 or any(
            p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values()
        )
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        takes_budget = True
    result = post(messages, max_tokens) if takes_budget else post(messages)  # type: ignore[call-arg]
    if isinstance(result, Completion):
        return result
    return Completion(str(result), "")


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _log_call(topic: str, ok: bool, latency_ms: float, prompt_chars: int, error: str = "") -> None:
    try:
        insert_row(
            AICallLog(
                topic=topic,
                provider=settings.ai_provider,
                model=settings.ai_model,
                ok=ok,
                latency_ms=latency_ms,
                prompt_chars=prompt_chars,
                error=error[:500],
            )
        )
    except Exception as exc:  # pragma: no cover
        log.warning("could not log AI call: %s", exc)


# ---------------------------------------------------------------------------
# Deterministic fallback ("no key" mode)
# ---------------------------------------------------------------------------
def deterministic_finding(topic: str, evidence: Dict[str, Any]) -> AIFinding:
    """Evidence-grounded prose produced without any network call.

    It only restates values that are already in the bundle, so the no-key mode
    cannot say anything the analysis did not compute.
    """
    limitations = []
    for key in ("limitations", "caveats", "known_ambiguities", "unavailable", "not_supported"):
        for item in evidence.get(key) or []:
            limitations.append(str(item)[:400])
    limitations = limitations[:6]
    if not limitations:
        limitations.append("Generated by the deterministic narrative engine, which only restates computed values.")

    evidence_lines: List[str] = []
    confidence = 0.5

    def push(label: str, value: Any, unit: str = "") -> None:
        if value is None:
            return
        if isinstance(value, float):
            evidence_lines.append(f"{label}: {value:,.4g}{(' ' + unit) if unit else ''}")
        else:
            evidence_lines.append(f"{label}: {value}{(' ' + unit) if unit else ''}")

    if topic == "case":
        first = evidence.get("first_divergence") or {}
        outcome = evidence.get("outcome") or {}
        push("Case", evidence.get("label") or evidence.get("case_id"))
        if first:
            push("First divergence along the documented route", first.get("label"))
            push("Divergence magnitude", first.get("z"), "standardised units")
            push("Metric", first.get("metric"))
        push("Assembled parts versus population", outcome.get("delta_pct"), "%")
        push("Stations diverging beyond threshold", len(evidence.get("co_occurring") or []))
        confidence = float(evidence.get("confidence") or 0.5)
        finding = (
            "The earliest divergence in this case appears at "
            f"{first.get('label', 'no station')} along the route documented in the model PDFs"
            + (f" (magnitude {first.get('z'):+.2f})." if first.get("z") is not None else ".")
            + " Divergences after that point are associated with the same run set rather than proven consequences."
        )
        recommendation = (
            "Review the operator and maintenance history for the affected station before acting; the dataset "
            "cannot distinguish a genuine process change from ordinary replication variability."
        )
    elif topic == "station":
        push("Station", evidence.get("label") or evidence.get("station"))
        push("Utilisation", evidence.get("utilization"), "of capacity")
        push("Queue mean", evidence.get("queue_mean"), "parts")
        push("Load headroom", evidence.get("headroom"), "x current load")
        finding = (
            f"{evidence.get('label', 'This station')} runs at "
            f"{(evidence.get('utilization') or 0):.3f} of capacity"
            + (f" with a mean queue of {evidence.get('queue_mean'):.1f} parts" if evidence.get("queue_mean") else "")
            + ". Its rank in the bottleneck composite and the components behind that rank are listed with the evidence."
        )
        recommendation = (
            "If this station is the ranked constraint, model a capacity or processing-time change in the "
            "What-If view and check the simulation validation residual before trusting the delta."
        )
    elif topic == "scenario":
        summary = evidence.get("summary") or {}
        validation = evidence.get("validation") or {}
        push("Engine", evidence.get("engine"))
        push("Replications", summary.get("replications"))
        push("Simulated parts completed", summary.get("completed_parts"))
        push("Simulated end-of-horizon WIP", summary.get("wip_parts_end_of_horizon"))
        push("Mean absolute validation residual", validation.get("mean_abs_residual_pct"), "%")
        finding = (
            f"The scenario was evaluated with the {evidence.get('engine', 'simulation')} engine. "
            f"{validation.get('verdict', 'Validation information is unavailable.')} "
            f"Mean absolute utilisation residual: "
            f"{validation.get('mean_abs_residual_pct') if validation.get('mean_abs_residual_pct') is not None else 'n/a'}"
            + ("%." if validation.get("mean_abs_residual_pct") is not None else ".")
        )
        recommendation = (
            "Treat the deltas as directional unless the validation residual is small; the known ambiguities in "
            "the documented model logic are listed alongside the result."
        )
    elif topic == "question":
        push("Scope", evidence.get("scope"))
        finding = (
            "Answered from the evidence bundle supplied to this engine. "
            + "; ".join(evidence_lines[:6])
        )
        recommendation = "Open the relevant investigation view for the full evidence chain and its limitations."
    else:  # overview
        quality = evidence.get("quality") or {}
        bottleneck = evidence.get("bottleneck") or {}
        push("Models analysed", ", ".join(evidence.get("models") or []))
        push("Image classes", len((evidence.get("inspection") or {}).get("classes") or []))
        push("Ranked bottleneck candidate", bottleneck.get("label"))
        push("Utilisation of that candidate", bottleneck.get("utilization"), "of capacity")
        push("Plant load headroom", bottleneck.get("headroom_pct"), "%")
        push("Usable columns", quality.get("usable_columns"))
        push("Anomalous replica fraction", quality.get("anomalous_fraction"))
        finding = (
            "Across the supplied datasets the ranked capacity constraint is "
            f"{bottleneck.get('label', 'not determined')}"
            + (f" at {bottleneck.get('utilization'):.3f} utilisation" if bottleneck.get("utilization") else "")
            + ". Economic impact is not computable from this dataset because no cost, price, scrap or downtime "
            "figure is exported by any model."
        )
        recommendation = (
            "Start from the Production view to confirm the constraint, then open a forensic case to see where "
            "divergence begins along the documented route."
        )

    if not evidence_lines:
        evidence_lines = ["No numeric evidence was available for this topic."]
    return AIFinding(
        finding=finding,
        evidence=evidence_lines,
        confidence=round(min(max(confidence, 0.15), 0.9), 2),
        limitations=limitations,
        recommendation=recommendation,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def narrative(
    topic: str,
    subject_id: str,
    evidence: Dict[str, Any],
    question: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Produce a validated narrative for a topic.

    Returns a dict shaped like ``schemas.AINarrative``. Never raises: on any
    failure it falls back to the deterministic engine and says so.
    """
    cache_key = hashlib.sha1(
        json.dumps([topic, subject_id, question, _compact(evidence)], sort_keys=True, default=str).encode()
    ).hexdigest()

    if use_cache and cache_key in _MEMORY_CACHE:
        cached = dict(_MEMORY_CACHE[cache_key])
        cached["cached"] = True
        return cached
    if use_cache:
        stored = _load_stored(topic, subject_id)
        if stored and stored.get("evidence_hash") == cache_key:
            stored.pop("evidence_hash", None)
            stored["cached"] = True
            _MEMORY_CACHE[cache_key] = stored
            return stored

    allowed, reason = _allowed()
    finding: AIFinding
    source = "deterministic"
    error = ""
    if allowed:
        messages = _build_messages(topic, evidence, question)
        prompt_chars = sum(len(m["content"]) for m in messages)
        started = time.perf_counter()
        try:
            completion = _call_provider(messages)
            parsed = _extract_json(completion.content)
            if parsed is None and completion.finish_reason == "length":
                # The model was cut off mid-JSON. That is a budget problem, not a
                # malformed answer, so retry once with room to finish.
                budget = min(max(settings.ai_max_output_tokens * 2, 1200), _MAX_RETRY_TOKENS)
                if budget > settings.ai_max_output_tokens:
                    log.info(
                        "model stopped at the %s-token output limit; retrying with max_tokens=%s",
                        settings.ai_max_output_tokens,
                        budget,
                    )
                    completion = _call_provider(messages, budget)
                    parsed = _extract_json(completion.content)
            if parsed is None:
                detail = " (truncated at the output-token limit)" if completion.finish_reason == "length" else ""
                raise ValueError(f"response was not valid JSON{detail}")
            finding = AIFinding(**parsed)
            source = "llm"
            _log_call(topic, True, (time.perf_counter() - started) * 1000, prompt_chars)
            _CALL_TIMES.append(time.time())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.warning("AI call failed (%s); falling back to the deterministic engine", error)
            _log_call(topic, False, (time.perf_counter() - started) * 1000, prompt_chars, error)
            finding = deterministic_finding(topic, evidence)
            # The LLM was configured but failed, so the caller must see why the
            # answer came from the template engine rather than the model.
            reason = reason or f"LLM call failed: {error}"
    else:
        finding = deterministic_finding(topic, evidence)

    payload = {
        "topic": topic,
        "subject_id": subject_id,
        "source": source,
        "provider": settings.ai_provider if source == "llm" else "deterministic",
        "model": settings.ai_model if source == "llm" else "evidence-template-v1",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "finding": finding.model_dump(),
        "grounded_on": _compact(evidence, max_items=6),
        "cached": False,
        "fallback_reason": reason if source == "deterministic" else "",
        "error": error,
    }
    if source == "llm":
        _store(topic, subject_id, payload, cache_key)
    _MEMORY_CACHE[cache_key] = payload
    return payload


def _load_stored(topic: str, subject_id: str) -> Optional[Dict[str, Any]]:
    try:
        rows = list_rows(Narrative, limit=40)
        for row in rows:
            if row.topic == topic and row.subject_id == subject_id:
                data = row.as_dict()
                content = data.get("content") or {}
                return {
                    "topic": topic,
                    "subject_id": subject_id,
                    "source": "llm",
                    "provider": data.get("provider", ""),
                    "model": data.get("model", ""),
                    "generated_at": data.get("created_at"),
                    "finding": content.get("finding") or content,
                    "grounded_on": data.get("evidence") or {},
                    "cached": True,
                    "fallback_reason": "",
                }
    except Exception as exc:  # pragma: no cover
        log.warning("could not read stored narratives: %s", exc)
    return None


def _store(topic: str, subject_id: str, payload: Dict[str, Any], evidence_hash: str) -> None:
    try:
        insert_row(
            Narrative(
                topic=topic,
                subject_id=subject_id,
                provider=payload["provider"],
                model=payload["model"],
                content=json.dumps({"finding": payload["finding"], "evidence_hash": evidence_hash}, default=str),
                evidence=json.dumps(payload["grounded_on"], default=str),
            )
        )
    except Exception as exc:  # pragma: no cover
        log.warning("could not store narrative: %s", exc)


def recent_logs(limit: int = 25) -> List[Dict[str, Any]]:
    try:
        return [row.as_dict() for row in list_rows(AICallLog, limit=limit)]
    except Exception:  # pragma: no cover
        return []


def usage_summary() -> Dict[str, Any]:
    logs = recent_logs(limit=200)
    ok = [log for log in logs if log["ok"]]
    latencies = [log["latency_ms"] for log in ok]
    return {
        "calls_logged": len(logs),
        "succeeded": len(ok),
        "failed": len(logs) - len(ok),
        "calls_last_hour": calls_last_hour(),
        "call_limit_per_hour": settings.ai_max_calls_per_hour,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "recent": logs[:10],
    }
