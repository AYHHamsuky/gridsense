"""Close the loop: tell every customer on a fault what happened, in their language.

When a photo inspection closes a fault (or flags it unsafe), each report merged
into that fault gets one message. The restoration text comes from the Muse Spark
inspection verdict (customer_message_en / customer_message_ha), so one model call
serves hundreds of customers; templates cover a verdict that left them out.
Messages land in an outbox (store.notifications); a real deployment would hand
the outbox to an SMS/WhatsApp gateway.
"""
from datetime import datetime, timezone

from .store import store

RESTORED = {
    "en": "Good news: fault {fid} in {area} has been repaired and supply is restored. "
          "Thank you for your patience.",
    "ha": "Albishir: an gyara matsalar {fid} a {area}, wuta ta dawo. Mun gode da hakurinku.",
}
FOLLOWUP = {
    "en": "Update on fault {fid} in {area}: our technician found a safety issue and the crew "
          "is returning to finish the repair. Please keep away from the equipment.",
    "ha": "Sabunta kan matsalar {fid} a {area}: ma'aikacinmu ya ga matsalar tsaro, za su dawo "
          "su kammala gyaran. Don Allah ku nisanta da kayan wutar.",
}


def report_language(report: dict) -> str:
    """Language the customer wrote in, as decided by the agent or the fast lane."""
    for src in (report.get("result") or {}, report.get("classification") or {}):
        lang = (src.get("language") or "").lower()
        if lang in ("ha", "en"):
            return lang
    return "en"


def _message(kind: str, lang: str, fault: dict, verdict: dict) -> tuple[str, str]:
    """Returns (text, source): Muse Spark's wording when present, else a template."""
    if kind == "restored":
        text = (verdict.get(f"customer_message_{lang}") or "").strip()
        if text:
            return text, "muse-spark"
    tpl = RESTORED if kind == "restored" else FOLLOWUP
    return tpl[lang].format(fid=fault["id"], area=", ".join(fault["areas"])), "template"


def notify_fault(fault: dict, kind: str, verdict: dict | None = None) -> list[dict]:
    """Queue one message per customer report on the fault. Idempotent per (report, kind)."""
    verdict = verdict or {}
    sent = {(n["report_id"], n["kind"]) for n in store.notifications}
    out = []
    for rid in fault["report_ids"]:
        if (rid, kind) in sent:
            continue
        r = store.reports.get(rid, {})
        lang = report_language(r)
        text, source = _message(kind, lang, fault, verdict)
        n = {"at": datetime.now(timezone.utc).isoformat(), "fault_id": fault["id"],
             "report_id": rid, "customer_name": r.get("customer_name", ""),
             "phone": r.get("phone", ""), "channel": r.get("channel", "text"),
             "language": lang, "kind": kind, "message": text, "source": source}
        store.notifications.append(n)
        out.append(n)
    if out:
        langs = [n["language"] for n in out]
        store.log(fault["id"], "notify", {"kind": kind, "customers": len(out),
                                          "hausa": langs.count("ha"), "english": langs.count("en")})
    return out
