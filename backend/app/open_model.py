"""Fast lane: Meta open-weight model (Muse Spark Open) for bulk report classification.

Served through any OpenAI-compatible endpoint:
  - Modal Shared Endpoint (Muse Spark Open): copy the endpoint URL + model name from
    the Modal dashboard; key = "<proxy-token-id>.<proxy-token-secret>"
    (create with: modal workspace proxy-tokens create)
  - Baseten Model APIs: model "meta-models/muse-spark-open-1026"
Configure OPEN_BASE_URL / OPEN_API_KEY / OPEN_MODEL in .env.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

from .config import OPEN_API_KEY, OPEN_BASE_URL, OPEN_MODEL, OPEN_REASONING_EFFORT
from .tools import FAULT_TYPES

_client = None

FIELDS = """{"fault_type": one of %s,
 "area": "<neighbourhood name mentioned, or empty>",
 "landmark": "<landmark mentioned, or empty>",
 "language": "ha" or "en",
 "danger": true if fire, smoke, sparks or a fallen wire is mentioned else false,
 "confidence": 0.0-1.0,
 "summary_en": "<max 12 words, English>"}""" % FAULT_TYPES
PROMPT = ("Classify this electricity outage report from Kaduna, Nigeria (Hausa or English).\n"
          "Return ONLY JSON:\n" + FIELDS + "\nReport: ")
BATCH_PROMPT = ("Classify each electricity outage report below from Kaduna, Nigeria (Hausa or English).\n"
                "Return ONLY a JSON array with one object per report, in the same order, each:\n"
                + FIELDS + "\nReports:\n")
# Reports per call. The model reasons once per call, so batching cuts tokens per report
# ~4x; measured on Modal: 50 reports in 4.3s at 25/call vs 15.6s one per call.
BATCH_SIZE = 25


def enabled() -> bool:
    return bool(OPEN_BASE_URL and OPEN_API_KEY and OPEN_MODEL)


def client() -> OpenAI:
    global _client
    if not enabled():
        raise RuntimeError("Open-weight fast lane not configured: set OPEN_BASE_URL, "
                           "OPEN_API_KEY and OPEN_MODEL in .env")
    if _client is None:
        _client = OpenAI(base_url=OPEN_BASE_URL, api_key=OPEN_API_KEY)
    return _client


def _parse(text: str) -> dict:
    s, e = (text or "").find("{"), (text or "").rfind("}")
    try:
        out = json.loads(text[s:e + 1])
    except (ValueError, json.JSONDecodeError):
        return {"fault_type": "other", "confidence": 0.0, "parse_error": True}
    return _normalise(out)


def _parse_many(text: str, n: int) -> list[dict]:
    """Parse a batch reply; raise if it is not a JSON array of exactly n objects."""
    s, e = (text or "").find("["), (text or "").rfind("]")
    out = json.loads((text or "")[s:e + 1]) if s != -1 else None
    if not isinstance(out, list) or len(out) != n or not all(isinstance(o, dict) for o in out):
        raise ValueError(f"expected a JSON array of {n} objects")
    return [_normalise(o) for o in out]


def _normalise(out: dict) -> dict:
    if out.get("fault_type") not in FAULT_TYPES:
        out["fault_type"], out["confidence"] = "other", 0.0
    try:
        out["confidence"] = float(out.get("confidence", 0))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["danger"] = bool(out.get("danger"))
    return out


def _text(report: dict) -> str:
    text = report["text"]
    if report.get("area_hint"):
        text += f" (customer area: {report['area_hint']})"
    return text


def _complete(cli, prompt: str, max_tokens: int) -> str:
    # Reasoning tokens count toward max_tokens: leave room for the JSON after the thinking
    extra = {"reasoning_effort": OPEN_REASONING_EFFORT} if OPEN_REASONING_EFFORT else {}
    resp = cli.chat.completions.create(
        model=OPEN_MODEL, temperature=0, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}], **extra)
    return resp.choices[0].message.content


def classify(report: dict, cli=None) -> dict:
    return _parse(_complete(cli or client(), PROMPT + _text(report), 1024))


def classify_batch(reports: list[dict], cli=None) -> list[dict]:
    lines = "\n".join(f"{i + 1}. {_text(r)}" for i, r in enumerate(reports))
    return _parse_many(_complete(cli or client(), BATCH_PROMPT + lines, 1024 + 200 * len(reports)),
                       len(reports))


def classify_many(reports: list[dict], workers: int = 16, cli=None,
                  batch: int = BATCH_SIZE) -> list[dict]:
    cli = cli or client()

    def one(r):
        try:
            return classify(r, cli)
        except Exception as e:  # one bad call must not sink the batch
            return {"fault_type": "other", "confidence": 0.0, "error": str(e)}

    def chunk(rs):
        if len(rs) > 1:
            try:
                return classify_batch(rs, cli)
            except Exception:  # unreadable batch reply: fall back to one report per call
                pass
        return [one(r) for r in rs]

    chunks = [reports[i:i + batch] for i in range(0, len(reports), max(batch, 1))]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [c for out in ex.map(chunk, chunks) for c in out]
