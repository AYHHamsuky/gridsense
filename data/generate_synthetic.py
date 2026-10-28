"""Generate SYNTHETIC demo data for GridSense.

All feeders, customers, technicians and reports are invented. Area names are
public Kaduna neighbourhood names used only as locations; no real utility data.

Usage: python data/generate_synthetic.py          (writes data/*.json)
       python data/generate_synthetic.py --storm  (only rewrites data/storm.json)
"""
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)
OUT = Path(__file__).parent

FEEDERS = [
    {"id": "FDR-01", "name": "Barnawa 11kV", "substation": "South Injection"},
    {"id": "FDR-02", "name": "Kawo 11kV", "substation": "North Injection"},
    {"id": "FDR-03", "name": "Sabon Tasha 11kV", "substation": "South Injection"},
    {"id": "FDR-04", "name": "Tudun Wada 11kV", "substation": "Central Injection"},
    {"id": "FDR-05", "name": "Malali 11kV", "substation": "North Injection"},
]

# area -> (feeder, lat, lon, landmarks)
AREAS = {
    "Barnawa": ("FDR-01", 10.4806, 7.4436, ["Barnawa market", "Kamazou junction"]),
    "Narayi": ("FDR-01", 10.4669, 7.4560, ["Narayi high cost", "Narayi roundabout"]),
    "Television": ("FDR-01", 10.4930, 7.4220, ["Television garage", "NTA road"]),
    "Kakuri": ("FDR-01", 10.4870, 7.4120, ["Kakuri market", "UNTL road"]),
    "Kawo": ("FDR-02", 10.5822, 7.4470, ["Kawo bridge", "Kawo motor park"]),
    "Hayin Banki": ("FDR-02", 10.5730, 7.4380, ["Hayin Banki mosque", "Rafin Guza"]),
    "Mando": ("FDR-02", 10.6100, 7.4300, ["Mando junction", "Mando garage"]),
    "Unguwan Dosa": ("FDR-02", 10.5650, 7.4550, ["Dosa water works", "Isa Kaita road"]),
    "Sabon Tasha": ("FDR-03", 10.4500, 7.4760, ["Sabon Tasha market", "Station road"]),
    "Gonin Gora": ("FDR-03", 10.4320, 7.4420, ["Gonin Gora tollgate", "Gonin Gora junction"]),
    "Kudenda": ("FDR-03", 10.4600, 7.4000, ["Kudenda bridge", "Kudenda industrial"]),
    "Tudun Wada": ("FDR-04", 10.5050, 7.4150, ["Tudun Wada market", "Sokoto road"]),
    "Rigasa": ("FDR-04", 10.5250, 7.3800, ["Rigasa station", "Rigasa market"]),
    "Badiko": ("FDR-04", 10.5000, 7.4300, ["Badiko primary school", "Badiko junction"]),
    "Malali": ("FDR-05", 10.5450, 7.4620, ["Malali GRA", "Malali village"]),
    "Ungwan Rimi": ("FDR-05", 10.5350, 7.4500, ["Rimi GRA", "Kanta road"]),
    "Kabala": ("FDR-05", 10.5150, 7.4700, ["Kabala Costain", "Kabala West"]),
}

FIRST = ["Aisha", "Musa", "Fatima", "Ibrahim", "Hauwa", "Usman", "Zainab", "Sani",
         "Maryam", "Abdullahi", "Grace", "John", "Halima", "Yusuf", "Amina", "Bello"]
LAST = ["Abubakar", "Danjuma", "Ibrahim", "Suleiman", "Bala", "Garba", "Audu",
        "Mohammed", "Yakubu", "Isah", "Shehu", "Aliyu"]

# (language, template, fault_type)
TEMPLATES = [
    ("ha", "Babu wuta a {area} tun {hrs} awa da suka wuce.", "no_supply"),
    ("ha", "Transformer ya kone kusa da {lm}, akwai hayaki.", "transformer"),
    ("ha", "Waya ta tsinke ta fadi a hanya kusa da {lm}. Akwai hadari!", "line_down"),
    ("ha", "Wuta tana zuwa tana tafiya a {area}, voltage ya yi kasa sosai.", "low_voltage"),
    ("ha", "Mita ta ba ta aiki, ta nuna error a gidana a {area}.", "meter"),
    ("ha", "Gaba daya unguwar {area} babu wuta, don Allah ku taimaka.", "no_supply"),
    ("en", "No light in {area} since {hrs} hours ago.", "no_supply"),
    ("en", "Transformer exploded near {lm}, there is fire and smoke.", "transformer"),
    ("en", "A cable has fallen on the road by {lm}. Very dangerous!", "line_down"),
    ("en", "Light keeps blinking in {area}, voltage very low, fridge cannot work.", "low_voltage"),
    ("en", "My prepaid meter shows error, house in {area}.", "meter"),
    ("en", "The whole of {area} is in darkness, please help.", "no_supply"),
]

# Storm scenario: a realistic burst built around a few real incidents.
# Most callers just say "no light"; only people near the equipment mention fire
# or a fallen wire. Each incident: (weight, [(area, landmark-or-None)], [(weight, fault_type, danger)])
NO_LIGHT = [
    ("ha", "Babu wuta a {area} tun {hrs} awa da suka wuce."),
    ("ha", "Gaba daya unguwar {area} babu wuta, don Allah ku taimaka."),
    ("ha", "Wuta ta dauke a {area}, ba mu da wuta ko kadan."),
    ("en", "No light in {area} since {hrs} hours ago."),
    ("en", "The whole of {area} is in darkness, please help."),
    ("en", "Power just went off in {area}, everywhere is dark."),
]
STORM_TEMPLATES = {
    "no_supply": NO_LIGHT,
    "transformer": [
        ("ha", "Transformer ya kone kusa da {lm}, akwai hayaki."),
        ("ha", "Mun ji kara mai karfi, transformer na {lm} yana ci da wuta!"),
        ("en", "Transformer exploded near {lm}, there is fire and smoke."),
        ("en", "Big bang at the transformer by {lm}, it is burning."),
    ],
    "line_down": [
        ("ha", "Waya ta tsinke ta fadi a hanya kusa da {lm}. Akwai hadari!"),
        ("ha", "Wayar wuta ta fadi a kasa a {lm}, yara suna wasa kusa."),
        ("en", "A cable has fallen on the road by {lm}. Very dangerous!"),
        ("en", "Live wire lying on the ground at {lm}, sparks everywhere."),
    ],
    "low_voltage": [
        ("ha", "Wuta tana zuwa tana tafiya a {area}, voltage ya yi kasa sosai."),
        ("ha", "Wutar {area} ba ta da karfi, fanka ma ba ya juyawa."),
        ("en", "Light keeps blinking in {area}, voltage very low, fridge cannot work."),
        ("en", "Voltage is too low in {area}, bulbs are dim."),
    ],
    "meter": [
        ("ha", "Mita ta ba ta aiki, ta nuna error a gidana a {area}."),
        ("en", "My prepaid meter shows error, house in {area}."),
    ],
}
INCIDENTS = [
    # Burnt transformer at Kawo bridge: FDR-02 goes dark
    (0.40, ["Kawo", "Hayin Banki", "Mando", "Unguwan Dosa"], "Kawo bridge",
     [(0.85, "no_supply"), (0.15, "transformer")]),
    # Fallen line on Barnawa market road: FDR-01 out
    (0.30, ["Barnawa", "Narayi", "Barnawa"], "Barnawa market",
     [(0.85, "no_supply"), (0.15, "line_down")]),
    # Low voltage across Malali feeder
    (0.20, ["Malali", "Ungwan Rimi", "Kabala"], None, [(1.0, "low_voltage")]),
    # Scattered noise: individual meter faults
    (0.10, ["Sabon Tasha", "Rigasa", "Kudenda"], None, [(1.0, "meter")]),
]


def storm(n=200, seed=7):
    """Reports for one evening's outage spike, labelled with ground truth."""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    reports = []
    for i in range(n):
        _, areas, lm, mix = rng.choices(INCIDENTS, weights=[x[0] for x in INCIDENTS])[0]
        ftype = rng.choices([m[1] for m in mix], weights=[m[0] for m in mix])[0]
        # Danger reports come from people at the equipment; outage calls from all over the feeder
        area = areas[0] if ftype in ("transformer", "line_down") else rng.choice(areas)
        lang, tpl = rng.choice(STORM_TEMPLATES[ftype])
        text = tpl.format(area=area, lm=lm or rng.choice(AREAS[area][3]), hrs=rng.randint(1, 3))
        reports.append({
            "id": f"STM-{i+1:04d}",
            "customer_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "phone": f"+234701000{i:04d}",
            "channel": rng.choice(["text", "text", "voice"]),
            "language": lang,
            "text": text,
            "area_hint": area,
            "expected_fault_type": ftype,
            "created_at": (now - timedelta(minutes=rng.randint(0, 45))).isoformat(),
        })
    reports.sort(key=lambda r: r["created_at"])
    (OUT / "storm.json").write_text(json.dumps(reports, indent=2, ensure_ascii=False))
    print(f"wrote data/storm.json ({len(reports)})")


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    areas = [
        {"name": a, "feeder_id": f, "lat": lat, "lon": lon, "landmarks": lms}
        for a, (f, lat, lon, lms) in AREAS.items()
    ]
    techs = []
    for i in range(10):
        home = random.choice(list(AREAS))
        techs.append({
            "id": f"TECH-{i+1:02d}",
            "name": f"{random.choice(FIRST)} {random.choice(LAST)}",
            "phone": f"+234800000{i:04d}",
            "base_area": home,
            "skills": random.sample(["lines", "transformer", "meter", "protection"], 2),
            "available": True,
        })
    reports = []
    for i in range(200):
        area = random.choice(list(AREAS))
        lang, tpl, ftype = random.choice(TEMPLATES)
        text = tpl.format(area=area, lm=random.choice(AREAS[area][3]), hrs=random.randint(1, 9))
        reports.append({
            "id": f"RPT-{i+1:04d}",
            "customer_name": f"{random.choice(FIRST)} {random.choice(LAST)}",
            "phone": f"+234700000{i:04d}",
            "channel": random.choice(["text", "text", "voice"]),
            "language": lang,
            "text": text,
            "area_hint": area,
            "expected_fault_type": ftype,  # ground truth for evaluating the agent
            "created_at": (now - timedelta(minutes=random.randint(0, 600))).isoformat(),
        })
    reports.sort(key=lambda r: r["created_at"])
    for name, obj in [("feeders", FEEDERS), ("areas", areas), ("technicians", techs), ("reports", reports)]:
        (OUT / f"{name}.json").write_text(json.dumps(obj, indent=2, ensure_ascii=False))
        print(f"wrote data/{name}.json ({len(obj)})")


if __name__ == "__main__":
    if "--storm" not in sys.argv:
        main()
    storm()
