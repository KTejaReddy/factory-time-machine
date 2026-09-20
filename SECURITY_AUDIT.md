# SECURITY_AUDIT.md

Advisory tool, no equipment control. This audit covers what the code does with
untrusted input: HTTP requests, uploaded images, dataset files and language-model
output. Every finding was reproduced against the running server, then fixed, then
re-checked. Dates: 2026-09-19.

---

## 1. Summary

| Area | Status | Evidence |
|---|---|---|
| No hard-coded secrets | ✅ pass | `.env` is git-ignored; `.env.example` holds placeholders only; no key-shaped string in tracked source |
| API key handling | ✅ pass | Read from environment at start-up; never logged, never returned by `/api/ai/status` |
| SQL injection | ✅ pass | ORM-only; hostile strings stored literally and tables intact after probes |
| Path traversal | ✅ pass | Uploads stored under server-generated names; image ids resolved through an index, never concatenated into a path |
| Upload validation | ✅ pass | Magic-byte sniffing, 10 MB streaming cap, PNG/JPEG only, re-encode before storage |
| Prompt injection | ✅ pass | Model output is data: validated against a strict schema, never executed, command-shaped text redacted |
| Safe AI output handling | ✅ pass | Output rendered as text (React escaping), trimmed, bounded, invisible characters removed |
| Error output to users | ✅ pass | Generic sentence + log reference; no exception text or traceback |
| CORS | ✅ pass | Explicit origin allow-list, no wildcard fallback, narrowed methods/headers |
| Rate limiting / spend | ✅ pass | `AI_MAX_CALLS_PER_HOUR` (default 120); over the limit falls back to the local engine |
| Dependency footprint | ⚠ review | No lockfile pinning for Python deps; see §8 |

---

## 2. Secrets and configuration

* `.env` holds the live values and is listed in `.gitignore`; `.env.example` contains
  empty placeholders and comments only.
* The key is read once in `backend/app/config.py` and exposed as
  `settings.ai_api_key`. `/api/ai/status` returns `llm_enabled`, provider, model and
  base URL — **not** the key. Log lines record topic, latency and error text only.
* No credential is embedded in frontend code: the browser never talks to the model
  provider, only to this backend.
* A blank `AI_API_KEY` is a supported mode, not a failure: the deterministic engine
  answers and `/api/ai/status` says so.

**Residual note.** If a key is ever pasted into a chat, an issue or a log, rotate it —
this audit cannot detect that.

---

## 3. Injection

### SQL
All database access goes through SQLAlchemy models (`backend/app/db.py`); there is no
string-built SQL. Probes sent as feedback and assumption values:

```
finding_id  = "'; DROP TABLE feedback; --"
note        = "'; DELETE FROM feedback; --"
engineer    = "<img src=x onerror=alert(1)>"
rationale   = "'; DROP TABLE feedback; --"
```

Result: stored verbatim as text, `GET /api/review/feedback` returned normally
afterwards, and no table was dropped. Parameter binding is doing its job.

### Command / shell
There is no `subprocess`, `os.system` or `eval` anywhere in the request path, and no
dataset value is ever passed to a shell.

### Prompt injection
The model is given a compact evidence bundle and asked for a JSON object. Its answer:

1. must parse as JSON (`_extract_json`) *and*
2. must satisfy `schemas.AIFinding` (`finding`, `evidence[]`, `confidence` in 0–1,
   `limitations[]`, `recommendation`), with lengths and list sizes bounded, *and*
3. passes through `_normalise_text`, which redacts command-shaped fragments.

Model text is never interpreted as an instruction, never used to build SQL, and never
used to choose a code path — the numerical work has already happened before the model
is called. A response containing `drop table stations;--` is redacted; the previous
regex could not match `;--` at all (a trailing `\b` after a non-word character is
impossible), which this audit fixed and now tests.

### Cross-site scripting
Feedback titles and notes are stored as free text and rendered through React, which
escapes by default. Verified live: a stored `<script>alert(1)</script>` title renders
as visible text and does not execute.

---

## 4. File handling

| Check | Implementation | Live result |
|---|---|---|
| Traversal in image id | ids are looked up in `index.json`; file names are server-generated (`upload_<uuid12>.png`) | `../../.env`, `..%2f..%2f.env`, `....//....//.env` → not found |
| Delete traversal | `DELETE /api/external-images/{id}` matches only known ids | `DELETE /api/external-images/../.env` → 405, no file touched |
| Content-type lying | magic bytes decide the format, not the header or the extension | non-image bytes rejected |
| Oversized body | declared size rejected before reading; otherwise chunked read stops at the cap | 11 MB → `413`, 8.4 MB → accepted |
| Stored format | every accepted image is re-encoded to PNG before it is written | stored files are always valid PNG |
| Location | everything under `data/external_test/`, inside the project directory | no write outside the project |
| Static serving | `.env` and friends are not served; unknown paths fall through to the SPA shell | `/..%2f.env` returned HTML, not file content |

The training pipeline reads `train.zip` exclusively; nothing in `data/external_test/`
is on any path that touches training data, labels or weights (asserted in
`tests/test_external_images.py`).

---

## 5. Network and browser exposure

* **CORS** is an explicit allow-list from `CORS_ORIGINS` (default
  `http://localhost:5173,http://127.0.0.1:5173`). Verified: the configured origin
  receives `access-control-allow-origin`; `http://evil.example.com` receives no header,
  so a browser refuses to hand the response to script.
* **Previously fail-open:** `allow_origins=... or ["*"]` meant that emptying the
  variable allowed every origin. Now empty means "no cross-origin access" plus a
  startup warning.
* Methods are narrowed to `GET, POST, DELETE, OPTIONS` and headers to
  `Content-Type` instead of `*`.
* **Bound to loopback in development.** Exposing the API to a network is a deployment
  decision for the operator; there is no authentication layer in this build, so it is
  an internal tool as shipped.
* **Rate limiting:** the AI layer caps calls per hour and falls back to the local
  engine past the cap; the endpoint is not an unbounded proxy to a paid API.

---

## 6. Error handling and information disclosure

* The catch-all 500 handler logs the traceback and returns
  `Something went wrong while handling that request. It has been logged as reference
  <8 hex chars>` — previously it returned `internal error: <Type>: <message>`.
* Validation failures return FastAPI's structured detail, which the frontend now
  formats into a sentence before display (`describeError`).
* Hand-written errors are safe to show (`unknown station 'nope'`, `This image is
  11.0 MB, which is above the 10 MB limit…`).
* Verification probes: `topic=../../etc/passwd` → `422` pattern mismatch;
  `{}` body → `422` listing the missing fields; unknown station → `404`.

---

## 7. Data integrity and honesty controls

These are security-adjacent because they prevent a false claim from reaching a user:

* Every graph edge declares `observed`, `assumed` or `unavailable`; economic impact
  stays `unavailable` unless the operator supplies a rate card.
* Declaring a link between a defect class and a station is refused (422) unless both
  names exist in the datasets.
* Feedback is stored but **never** fed back into any model; the API and the UI both say
  `model_retrained: false`.
* The AI layer receives a compact bundle and is instructed to use only its numbers; the
  deterministic engine exists so no feature depends on the provider being reachable.

---

## 8. Open items and residual risk

1. **Dependency pinning.** `requirements.txt` lists packages without hashes or exact
   pins, and `frontend/package.json` allows caret ranges. Not a vulnerability by
   itself, but it means two installs can differ. Recommend a lockfile
   (`pip-compile`/`uv lock`, and the committed `package-lock.json`) before any shared
   deployment.
2. **No authentication.** Anyone who can reach the port can read the data, upload test
   images and spend AI quota. Acceptable for a local advisory tool; not acceptable
   exposed to a network. Put it behind a reverse proxy with auth if that changes.
3. **Whole-file dataset loads.** The catalog reads the archives into a bounded sample;
   a hostile archive could still consume memory during extraction. Keep datasets
   trusted.
4. **AI spend.** The hourly cap is per process and resets on restart. On a shared
   deployment, move the counter to a shared store.
5. **`data/external_test/` grows.** Bounded at `MAX_EXTERNAL_IMAGES = 500` by pruning
   the oldest entries; if uploads are opened to more people, add per-user quotas.

---

## 9. How to re-run the security checks

```bash
# 1. start the API
python -m uvicorn app.main:app --port 8000 --app-dir backend

# 2. unit-level hardening tests
python -m pytest tests/test_api_hardening.py tests/test_external_images.py -q

# 3. end-to-end probes (valid, missing, malformed, hostile input on every endpoint)
python scripts/smoke_test_api.py --base http://127.0.0.1:8000

# 4. browser checks
cd frontend && npm run dev      # then open http://localhost:5173
```

The probes used in this audit are the ones listed in §1–§6; they are deliberately
non-destructive (nothing is deleted except test images and test rows the probes
themselves created).
