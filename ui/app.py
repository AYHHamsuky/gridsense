"""GridSense Streamlit UI — customer reporting, dispatch dashboard, storm mode.

Run (with the API already running on :8000):
  streamlit run ui/app.py
"""
import os
import time

import httpx
import pandas as pd
import pydeck as pdk
import streamlit as st

API = os.getenv("GRIDSENSE_API", "http://127.0.0.1:8000")
SEV_COLOR = {"critical": [220, 38, 38], "high": [234, 88, 12],
             "medium": [234, 179, 8], "low": [59, 130, 246]}

st.set_page_config(page_title="GridSense", page_icon="⚡", layout="wide")


def api(method, path, **kw):
    try:
        r = httpx.request(method, f"{API}{path}", timeout=120, **kw)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text[:300]}")
    except httpx.HTTPError as e:
        st.error(f"Cannot reach the GridSense API at {API}. Is uvicorn running? ({e})")
    return None


@st.cache_data(ttl=300)
def geo():
    return api("GET", "/feeders") or {"feeders": [], "areas": []}


def fault_frame(faults):
    areas = {a["name"]: a for a in geo()["areas"]}
    rows = []
    for f in faults:
        a = areas.get(f["areas"][0], {})
        rows.append({
            "id": f["id"], "type": f["fault_type"], "severity": f["severity"],
            "status": f["status"], "feeder": f["feeder_id"], "areas": ", ".join(f["areas"]),
            "reports": len(f["report_ids"]), "technician": f["technician_id"] or "—",
            "summary": f["summary"], "lat": a.get("lat"), "lon": a.get("lon"),
            "color": SEV_COLOR.get(f["severity"], [120, 120, 120]),
            "radius": 250 + 120 * len(f["report_ids"]),
        })
    return pd.DataFrame(rows)


def fault_map(df):
    if df.empty or df["lat"].isna().all():
        st.info("No faults on the map yet.")
        return
    layer = pdk.Layer("ScatterplotLayer", df.dropna(subset=["lat"]),
                      get_position=["lon", "lat"], get_fill_color="color",
                      get_radius="radius", opacity=0.6, pickable=True)
    st.pydeck_chart(pdk.Deck(
        layers=[layer], map_style=None,
        initial_view_state=pdk.ViewState(latitude=10.52, longitude=7.44, zoom=11),
        tooltip={"text": "{id} · {type} ({severity})\n{areas}\nReports: {reports} · Tech: {technician}"}))


# ---------------- sidebar ----------------
st.sidebar.title("⚡ GridSense")
st.sidebar.caption("Hausa/English outage & fault agent")
page = st.sidebar.radio("View", ["📱 Report an outage", "🗺️ Dispatch dashboard",
                                 "🔧 Technician inspection", "🌩️ Storm mode"])
health = api("GET", "/health")
st.sidebar.write("API:", "🟢 online" if health else "🔴 offline")
if st.sidebar.button("Reset demo data"):
    api("POST", "/demo/reset")
    st.sidebar.success("Reset.")

# ---------------- customer ----------------
if page.startswith("📱"):
    st.header("Report an outage")
    st.caption("Write in Hausa or English — e.g. *Babu wuta a Barnawa tun safe.*")
    if "chat" not in st.session_state:
        st.session_state.chat = []
    for role, text in st.session_state.chat:
        st.chat_message(role).write(text)

    with st.expander("🎙️ Send a voice note (WAV)"):
        audio = st.audio_input("Record") if hasattr(st, "audio_input") else None
        up = audio or st.file_uploader("…or upload a WAV file", type=["wav"])
        if up and st.button("Send voice note"):
            with st.spinner("Transcribing and triaging…"):
                res = api("POST", "/reports/voice",
                          files={"audio": ("note.wav", up.getvalue(), "audio/wav")})
            if res:
                st.session_state.chat += [("user", f"🎙️ {res['transcript']}"),
                                          ("assistant", res["result"].get("reply", ""))]
                st.rerun()

    if msg := st.chat_input("Describe the problem and your area…"):
        st.session_state.chat.append(("user", msg))
        with st.spinner("GridSense is checking the network…"):
            res = api("POST", "/reports", json={"text": msg})
        if res:
            r = res["result"]
            reply = r.get("reply", "")
            if r.get("fault_id"):
                reply += f"\n\n`{r['fault_id']}` · {r.get('fault_type', '')} · {r.get('severity', '')}"
            st.session_state.chat.append(("assistant", reply))
        st.rerun()

# ---------------- dashboard ----------------
elif page.startswith("🗺️"):
    st.header("Dispatch dashboard")
    faults = api("GET", "/faults") or []
    df = fault_frame(faults)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open faults", int((df["status"] != "closed").sum()) if not df.empty else 0)
    c2.metric("Reports merged", int(df["reports"].sum()) if not df.empty else 0)
    c3.metric("Dispatched", int((df["status"] == "dispatched").sum()) if not df.empty else 0)
    c4.metric("Critical", int((df["severity"] == "critical").sum()) if not df.empty else 0)

    left, right = st.columns([3, 2])
    with left:
        fault_map(df)
    with right:
        if not df.empty:
            st.dataframe(df[["id", "type", "severity", "status", "areas", "reports", "technician"]],
                         hide_index=True, width="stretch")
    if not df.empty:
        st.subheader("Agent reasoning")
        fid = st.selectbox("Fault", df["id"].tolist())
        detail = api("GET", f"/faults/{fid}")
        if detail:
            st.write(f"**{detail['summary']}** — feeder {detail['feeder_id']}, "
                     f"areas: {', '.join(detail['areas'])}")
            for e in detail["events"]:
                d = e["detail"]
                if e["kind"] == "tool":
                    st.code(f"{e['report_id']} → {d['name']}({d['args']})\n   ↳ {d['result']}",
                            language="text")
                elif e["kind"] == "inspection":
                    v = d["verdict"]
                    st.warning(f"🔧 Photo inspection ({d['phrase']}): **{v.get('verdict')}** "
                               f"({v.get('confidence')}) — {'; '.join(v.get('findings', []))}")
                else:
                    st.success(f"{e['report_id']}: {d.get('reasoning', '')}")
    if st.button("🔄 Refresh"):
        st.rerun()

# ---------------- technician ----------------
elif page.startswith("🔧"):
    st.header("Technician inspection")
    st.caption("Upload the repair photo. SAM 3.1 finds the equipment; Muse Spark checks "
               "the repair is complete and safe, then closes the fault.")
    faults = [f for f in (api("GET", "/faults") or []) if f["status"] != "closed"]
    if not faults:
        st.info("No open faults. Send some reports first.")
    else:
        labels = {f"{f['id']} · {f['fault_type']} · {', '.join(f['areas'])}": f["id"] for f in faults}
        fid = labels[st.selectbox("Fault", list(labels))]
        c1, c2 = st.columns(2)
        before = c1.file_uploader("Before photo (optional)", type=["jpg", "jpeg", "png", "webp"])
        after = c2.file_uploader("After-repair photo", type=["jpg", "jpeg", "png", "webp"])
        if after and st.button("Run inspection"):
            files = {"after": (after.name, after.getvalue(), after.type)}
            if before:
                files["before"] = (before.name, before.getvalue(), before.type)
            with st.spinner("SAM 3.1 segmenting · Muse Spark inspecting…"):
                res = api("POST", f"/faults/{fid}/inspection", files=files)
            if res:
                v = res["verdict"]
                icon = {"resolved": "✅", "unsafe": "⛔", "not_resolved": "❌"}.get(v.get("verdict"), "❔")
                st.subheader(f"{icon} {v.get('verdict', '?').replace('_', ' ').title()} "
                             f"· confidence {v.get('confidence')} · fault now **{res['status']}**")
                cols = st.columns(len(res["overlays"]))
                for col, (name, url) in zip(cols, sorted(res["overlays"].items(), reverse=True)):
                    n = len(res["detections"].get(name, []))
                    col.image(url, caption=f"{name}: {n} × {res['phrase']} found by SAM 3.1")
                if res.get("sam_errors"):
                    st.caption(f"SAM issues: {res['sam_errors']}")
                st.write("**Findings:**", v.get("findings", []))
                if v.get("safety_issues"):
                    st.error("Safety: " + "; ".join(v["safety_issues"]))
                st.info(f"📣 {v.get('customer_message_en', '')}\n\n🗣️ {v.get('customer_message_ha', '')}")

# ---------------- storm ----------------
else:
    st.header("Storm mode")
    st.caption("Replay a burst of synthetic customer reports and watch the agent "
               "merge them into a handful of faults.")
    n = st.slider("Number of reports", 5, 150, 20)
    if st.button(f"🌩️ Send {n} reports"):
        samples = api("GET", "/demo/samples", params={"n": n, "seed": int(time.time())}) or []
        bar, feed = st.progress(0.0), st.empty()
        for i, s in enumerate(samples, 1):
            res = api("POST", "/reports", json=s)
            feed.write(f"**{i}/{len(samples)}** · {s['text']}  →  "
                       f"`{(res or {}).get('result', {}).get('fault_id', 'error')}`")
            bar.progress(i / len(samples))
        faults = api("GET", "/faults") or []
        st.success(f"{len(samples)} reports → {len(faults)} faults")
        fault_map(fault_frame(faults))
