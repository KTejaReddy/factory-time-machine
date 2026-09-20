"""AI fallback behaviour.

With no API key configured (the default for tests and demos), the narrative
engine must still produce a validated, evidence-grounded finding - and must say
that it did not call a language model.
"""

from __future__ import annotations

import json

import pytest

from app.services import ai_client


def _force_llm_enabled(monkeypatch):
    """Enable the LLM code path against a dead local endpoint.

    ``Settings`` is a frozen dataclass whose defaults were evaluated once at import
    time, so constructing a new ``Settings()`` does *not* re-read the environment.
    ``dataclasses.replace`` is therefore the only honest way to build a variant for
    a test: it produces a new frozen instance with just these fields changed.
    """
    import dataclasses

    variant = dataclasses.replace(
        ai_client.settings,
        ai_api_key="test-key",
        ai_disable=False,
        ai_base_url="http://127.0.0.1:9/v1",
    )
    assert variant.ai_enabled, "the helper must actually enable the LLM path"
    monkeypatch.setattr(ai_client, "settings", variant, raising=False)


@pytest.fixture(scope="module")
def overview_evidence(catalog):
    from app.routers.ops import _overview_evidence

    return _overview_evidence()


def test_status_reports_deterministic_mode_when_unconfigured():
    payload = ai_client.status()
    assert payload["deterministic_fallback"] is True
    if not payload["llm_enabled"]:
        assert "deterministic" in payload["reason"].lower() or "no ai_api_key" in payload["reason"].lower()


def test_overview_finding_is_grounded_in_evidence_and_honest(catalog, overview_evidence):
    payload = ai_client.narrative("overview", "test-overview", overview_evidence, use_cache=False)
    assert payload["source"] == "deterministic"
    finding = payload["finding"]
    assert isinstance(finding, dict), "narrative() returns a serialisable dict"
    assert finding["finding"]
    assert finding["evidence"], "the deterministic engine must cite the evidence it restates"
    assert 0.0 <= finding["confidence"] <= 1.0
    assert finding["limitations"], "an honest finding always carries limitations"
    assert payload["fallback_reason"], "deterministic mode must say why the LLM was not used"


def test_case_finding_names_the_first_divergence(catalog):
    from app.services import forensics

    case = forensics.build_case(catalog, "model3", "group:lowest-output-1pct")
    payload = ai_client.narrative("case", case["case_id"], case, use_cache=False)
    finding = payload["finding"]
    assert payload["source"] == "deterministic"
    blob = finding["finding"].lower()
    if case.get("first_divergence"):
        assert "divergence" in blob
    assert finding["recommendation"]


def test_invalid_llm_output_falls_back_to_deterministic(catalog, monkeypatch, overview_evidence):
    """A provider that answers garbage must degrade, not crash the endpoint."""
    _force_llm_enabled(monkeypatch)
    monkeypatch.setattr(ai_client, "_post_openai", lambda messages: "not json at all")
    payload = ai_client.narrative("overview", "fallback-test", overview_evidence, use_cache=False)
    assert payload["source"] == "deterministic"
    assert payload["error"], "the failure must be recorded, not swallowed"


def _valid_finding_json() -> str:
    return json.dumps(
        {
            "finding": "The ranked constraint is a station named in the evidence bundle.",
            "evidence": ["utilisation taken from the bundle"],
            "confidence": 0.6,
            "limitations": ["association is not causation"],
            "recommendation": "Confirm the constraint in the Production view.",
        }
    )


def test_truncated_json_is_retried_with_a_larger_budget(catalog, monkeypatch, overview_evidence):
    """A model that runs out of output tokens returns cut-off JSON.

    Found in the live audit against a reasoning-capable model: the reply stopped at
    ``finish_reason=length``, so nothing parsed and every large bundle silently
    answered from the template engine. That is a budget problem, so the client must
    retry once with more room instead of giving up.
    """
    budgets = []

    def fake(messages, max_tokens=None):
        budgets.append(max_tokens)
        if len(budgets) == 1:
            return ai_client.Completion('{"finding": "The earliest divergence appe', "length")
        return ai_client.Completion(_valid_finding_json(), "stop")

    _force_llm_enabled(monkeypatch)
    monkeypatch.setattr(ai_client, "_post_openai", fake)
    payload = ai_client.narrative("overview", "truncation-retry", overview_evidence, use_cache=False)

    assert payload["source"] == "llm", "the retry must be able to succeed"
    assert len(budgets) == 2, "exactly one retry"
    assert budgets[0] is None, "the first call uses the configured budget"
    assert budgets[1] == min(max(ai_client.settings.ai_max_output_tokens * 2, 1200), 4000)
    assert budgets[1] > ai_client.settings.ai_max_output_tokens, "the retry must actually raise the budget"


def test_permanent_truncation_falls_back_with_a_truncation_reason(catalog, monkeypatch, overview_evidence):
    def always_truncated(messages, max_tokens=None):
        return ai_client.Completion('{"finding": "cut off', "length")

    _force_llm_enabled(monkeypatch)
    monkeypatch.setattr(ai_client, "_post_openai", always_truncated)
    payload = ai_client.narrative("overview", "truncation-fallback", overview_evidence, use_cache=False)

    assert payload["source"] == "deterministic"
    assert "truncat" in payload["error"].lower(), "the cause must be visible, not a generic 'invalid JSON'"
    assert payload["fallback_reason"]


def test_rate_limited_call_is_retried_then_succeeds(monkeypatch):
    """A 429 is a queue, not a broken integration (seen live on a free tier)."""
    import httpx

    calls = []

    class FakeResponse:
        def __init__(self, status, payload=None, headers=None):
            self.status_code = status
            self._payload = payload or {}
            self.headers = headers or {}

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"Client error '{self.status_code}'", request=httpx.Request("POST", "http://test"), response=None
                )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            calls.append(url)
            if len(calls) == 1:
                return FakeResponse(429, headers={"retry-after": "0"})
            return FakeResponse(200, {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(ai_client.httpx, "Client", FakeClient)
    data = ai_client._request_json("http://test/chat/completions", {}, {})
    assert len(calls) == 2, "the first response is retried"
    assert data["choices"][0]["finish_reason"] == "stop"


def test_rate_limit_backoff_is_capped(monkeypatch):
    """A provider asking for a 10-minute wait must not stall the request."""
    class AnyResponse:
        headers = {"retry-after": "600"}

    assert ai_client._retry_after(AnyResponse(), fallback=2.0) == 10.0

    class NoHeader:
        headers = {}

    assert ai_client._retry_after(NoHeader(), fallback=2.0) == 2.0

    class Garbage:
        headers = {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}

    assert ai_client._retry_after(Garbage(), fallback=2.0) == 2.0


def test_llm_failure_falls_back_and_labels_the_answer(catalog, monkeypatch, overview_evidence):
    def boom(messages):
        raise RuntimeError("provider down")

    _force_llm_enabled(monkeypatch)
    monkeypatch.setattr(ai_client, "_post_openai", boom)
    payload = ai_client.narrative("overview", "failure-test", overview_evidence, use_cache=False)
    assert payload["source"] == "deterministic"
    assert payload["finding"]["finding"]


def test_deterministic_finding_never_contains_ungrounded_numbers(catalog, overview_evidence):
    payload = ai_client.narrative("overview", "grounding-test", overview_evidence, use_cache=False)
    text = payload["finding"]["finding"]
    # The overview template only restates bottleneck/utilisation numbers that the
    # evidence bundle carries; a fabricated "100%" claim would indicate drift.
    if "100%" in text:
        assert any(
            "100" in str(v) for v in _flatten(overview_evidence)
        ), "deterministic narrative quoted a number absent from its evidence"


def _flatten(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten(v)
    else:
        yield obj


def test_compact_truncates_and_never_leaks_huge_payloads():
    big = {"items": list(range(500)), "nested": {"deep": {"deeper": list("x" * 100)}}}
    compact = ai_client._compact(big)
    assert len(compact["items"]) <= 9  # 8 items + the "... N more" marker
    assert "more item(s) omitted" in compact["items"][-1]


def test_llm_text_is_normalised_for_display_and_label_matching():
    """Observed live: the model wrote "Blanking" with a soft hyphen.

    Station names are looked up by string, so an invisible character inside a real
    label breaks matching and looks like a data error to the user.
    """
    from app.schemas import AIFinding

    finding = AIFinding(
        finding="The Blank\u00ading station and the Press\u20111 station show divergence.",
        evidence=["queue\u200b mean 4.2 parts"],
        confidence=0.6,
        limitations=["association only"],
        recommendation="Check Press\u00a02.",
    )
    assert "Blanking" in finding.finding
    assert "Press-1" in finding.finding
    assert finding.evidence == ["queue mean 4.2 parts"]
    assert finding.recommendation == "Check Press 2."
    # No invisible characters may survive into anything the UI renders.
    blob = finding.finding + finding.recommendation + "".join(finding.evidence)
    assert not any(ch in blob for ch in ("\u00ad", "\u200b", "\u00a0", "\u200c", "\u2011"))


def test_command_like_model_output_is_redacted():
    """Model text must never read like a database or shell instruction.

    The previous expression grouped a trailing word boundary around ";--", which
    can never match, so that branch was dead.
    """
    from app.schemas import AIFinding

    finding = AIFinding(
        finding="Remediation: drop table stations;-- then rerun.",
        evidence=[],
        confidence=0.4,
        limitations=[],
        recommendation="delete from feedback",
    )
    assert "drop table" not in finding.finding.lower()
    assert ";--" not in finding.finding
    assert "[redacted]" in finding.finding
    assert "delete from" not in finding.recommendation.lower()
    # Ordinary prose survives untouched.
    plain = AIFinding(finding="Utilisation is associated with queue growth.", evidence=[], confidence=0.5)
    assert "[redacted]" not in plain.finding


def test_extract_json_handles_fenced_and_polluted_output():
    assert ai_client._extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai_client._extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    assert ai_client._extract_json("no json here") is None
    assert ai_client._extract_json("") is None
