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

from .config import OPEN_API_KEY, OPEN_BASE_URL, OPEN_MODEL
from .tools import FAULT_TYPES

_client = None

PROMPT = """Classify this electricity outage report from Kaduna, Nigeria (Hausa or English).
Return ONLY JSON:
{"fault_type": one of %s,
 "area": "<neighbourhood name mentioned, or empty>",
 "landmark": "<landmark mentioned, or empty>",
 "language": "ha" or "en",
 "danger": true if fire, smoke, sparks or a fallen wire is mentioned else false,
 "confidence": 0.0-1.0,
 "summary_en": "<max 12 words, English>"}
Report: """ % FAULT_TYPES


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
    if out.get("fault_type") not in FAULT_TYPES:
        out["fault_type"], out["confidence"] = "other", 0.0
    try:
        out["confidence"] = float(out.get("confidence", 0))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    out["danger"] = bool(out.get("danger"))
    return out


def classify(report: dict, cli=None) -> dict:
    cli = cli or client()
    text = report["text"]
    if report.get("area_hint"):
        text += f" (customer area: {report['area_hint']})"
    resp = cli.chat.completions.create(
        model=OPEN_MODEL, temperature=0, max_tokens=200,
        messages=[{"role": "user", "content": PROMPT + text}])
    return _parse(resp.choices[0].message.content)


def classify_many(reports: list[dict], workers: int = 8, cli=None) -> list[dict]:
    cli = cli or client()

    def one(r):
        try:
            return classify(r, cli)
        except Exception as e:  # one bad call must not sink the batch
            return {"fault_type": "other", "confidence": 0.0, "error": str(e)}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, reports))
