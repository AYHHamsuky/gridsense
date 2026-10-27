"""GridSense dashboard. Run the API first, then: streamlit run frontend/streamlit_app.py"""
import json
import os
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("GRIDSENSE_API_URL", "http://127.0.0.1:8000").rstrip("/")
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}

st.set_page_config(page_title="GridSense", page_icon="⚡", layout="wide")


def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{API_URL}{path}", timeout=120, **kw)
    except httpx.HTTPError as e:
        st.error(f"Cannot reach the GridSense API at {API_URL}: {e}")
        st.stop()
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text) if "json" in r.headers.get("content-type", "") else r.text
        raise RuntimeError(f"{r.status_code}: {detail}")
    return r.json()


@st.cache_data
def sample_reports():
    p = DATA_DIR / "reports.json"
    return json.loads(p.read_text()) if p.exists() else []


def show_result(result):
    if "error" in result:
        st.error(result["error"])
        return
    sev = result.get("severity", "")
    c1, c2, c3 = st.columns(3)
    c1.metric("Fault", result.get("fault_id", "—"))
    c2.metric("Type", result.get("fault_type", "—"))
    c3.metric("Severity", f"{SEVERITY_ICON.get(sev, '')} {sev or '—'}")
    st.info(f"**Reply to customer ({result.get('language', '?')}):** {result.get('reply', '')}")
    st.caption(f"Agent reasoning: {result.get('reasoning', '')}")


# ---------- sidebar ----------
with st.sidebar:
    st.title("⚡ GridSense")
    st.caption("Hausa/English outage triage for Kaduna")
    health = api("GET", "/health")
    st.success(f"API online · {health['feeders']} feeders · {health['technicians']} technicians")
    st.caption(f"API: `{API_URL}`")
    if st.button("Reset demo state", width="stretch"):
        api("POST", "/demo/reset")
        st.toast("Demo state reset")
        st.rerun()

tab_report, tab_faults, tab_map, tab_log = st.tabs(
    ["📨 New report", "🛠️ Faults", "🗺️ Map", "📜 Agent log"])

# ---------- new report ----------
with tab_report:
    mode = st.radio("Channel", ["Text", "Voice note"], horizontal=True)
    if mode == "Text":
        samples = sample_reports()
        labels = ["— write your own —"] + [f"[{s['language']}] {s['text']}" for s in samples]
        pick = st.selectbox("Load a synthetic sample report", range(len(labels)),
                            format_func=lambda i: labels[i])
        s = samples[pick - 1] if pick else {}
        with st.form("text_report"):
            text = st.text_area("Report (Hausa or English)", value=s.get("text", ""), height=100)
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Customer name", value=s.get("customer_name", ""))
            phone = c2.text_input("Phone", value=s.get("phone", ""))
            area = c3.text_input("Area hint", value=s.get("area_hint", ""))
            submitted = st.form_submit_button("Triage report", type="primary")
        if submitted:
            if not text.strip():
                st.warning("Enter a report first.")
            else:
                with st.spinner("Muse Spark is triaging…"):
                    try:
                        out = api("POST", "/reports", json={
                            "text": text, "customer_name": name, "phone": phone,
                            "area_hint": area, "channel": "text"})
                        st.success(f"Report {out['report_id']} processed")
                        show_result(out["result"])
                    except RuntimeError as e:
                        st.error(str(e))
    else:
        with st.form("voice_report"):
            audio = st.audio_input("Record a voice note (Hausa or English)", sample_rate=16000)
            c1, c2 = st.columns(2)
            area = c1.text_input("Area hint")
            phone = c2.text_input("Phone")
            submitted = st.form_submit_button("Transcribe & triage", type="primary")
        if submitted:
            if not audio:
                st.warning("Record a voice note first.")
            else:
                with st.spinner("Transcribing and triaging…"):
                    try:
                        out = api("POST", "/reports/voice",
                                  files={"audio": ("voice_note.wav", audio.getvalue(), "audio/wav")},
                                  data={"area_hint": area, "phone": phone})
                        st.success(f"Report {out['report_id']} processed")
                        st.write(f"**Transcript:** {out['transcript']}")
                        show_result(out["result"])
                    except RuntimeError as e:
                        st.error(str(e))

# ---------- faults ----------
with tab_faults:
    faults = api("GET", "/faults")
    if not faults:
        st.info("No faults yet. Submit a report to get started.")
    else:
        df = pd.DataFrame(faults)
        df["reports"] = df["report_ids"].apply(len)
        df["areas"] = df["areas"].apply(", ".join)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Open faults", int((df["status"] != "closed").sum()))
        c2.metric("Dispatched", int((df["status"] == "dispatched").sum()))
        c3.metric("Reports clustered", int(df["reports"].sum()))
        c4.metric("Critical", int((df["severity"] == "critical").sum()))
        st.dataframe(
            df[["id", "severity", "fault_type", "feeder_id", "areas", "reports",
                "status", "technician_id", "summary", "updated_at"]],
            width="stretch", hide_index=True)

        fid = st.selectbox("Fault detail", df["id"])
        detail = api("GET", f"/faults/{fid}")
        st.subheader(f"{SEVERITY_ICON.get(detail['severity'], '')} {detail['id']} · {detail['summary']}")
        st.write(f"Feeder **{detail['feeder_id']}** · areas {', '.join(detail['areas'])} · "
                 f"status **{detail['status']}** · technician **{detail['technician_id'] or '—'}**")
        st.markdown("**Customer reports**")
        st.dataframe(pd.DataFrame(detail["reports"]).drop(columns=["result"], errors="ignore"),
                     width="stretch", hide_index=True)
        st.markdown("**Agent steps**")
        for e in detail["events"]:
            if e["kind"] == "tool":
                with st.expander(f"🔧 {e['detail']['name']} · {e['report_id']}"):
                    st.json(e["detail"])
            else:
                with st.expander(f"✅ final answer · {e['report_id']}"):
                    st.json(e["detail"])

# ---------- map ----------
with tab_map:
    geo = api("GET", "/feeders")
    areas = pd.DataFrame(geo["areas"])
    feeders = {f["id"]: f["name"] for f in geo["feeders"]}
    faults = api("GET", "/faults")
    affected = {a.lower() for f in faults if f["status"] != "closed" for a in f["areas"]}
    areas["feeder"] = areas["feeder_id"].map(feeders)
    areas["affected"] = areas["name"].str.lower().isin(affected)
    areas["color"] = areas["affected"].map({True: "#e5484d", False: "#30a46c"})
    areas["size"] = areas["affected"].map({True: 400, False: 200})
    st.caption("🔴 area with an open fault · 🟢 normal supply")
    st.map(areas, latitude="lat", longitude="lon", color="color", size="size")
    st.dataframe(areas[["name", "feeder", "affected"]], width="stretch", hide_index=True)

# ---------- audit log ----------
with tab_log:
    limit = st.slider("Events to show", 10, 500, 100)
    events = api("GET", "/events", params={"limit": limit})
    if not events:
        st.info("No agent activity yet.")
    for e in reversed(events):
        label = e["detail"].get("name", "final") if e["kind"] == "tool" else "final"
        with st.expander(f"{e['at'][11:19]} · {e['report_id']} · {label}"):
            st.json(e["detail"])
