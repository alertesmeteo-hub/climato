"""Bilan climatologique du mois : chaque station active comparée à ses normales et records mensuels Météo-France.

Pour les 13 derniers mois (mois en cours compris, à date), on publie un fichier par mois avec, pour chaque
station active : moyennes Tn/Tx/Tm et écart à la normale 1991-2020 de la station, cumul de pluie et rapport à
la normale, ensoleillement, jours remarquables, extrêmes du mois et records mensuels égalés ou battus. Le
résumé par zone (France, Occitanie, Pyrénées-Orientales) reprend l'indicateur thermique du mois et son rang
depuis 1950, calculés par build_indicateur_thermique.py (à lancer avant).

Une station entre dans le bilan si elle couvre au moins 70 % des jours du mois (80 % des jours écoulés pour le
mois en cours) : beaucoup de postes ne sont publiés par Météo-France qu'avec deux à trois semaines de retard. Pluie
et ensoleillement sont alors comparés à la normale ramenée au nombre de jours mesurés.
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

PIPELINE_VERSION = "1.0.0"
OCCITANIE = {"09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"}
ZONES = {
    "france": {"label": "France métropolitaine", "filtre": lambda dep: True},
    "occitanie": {"label": "Occitanie", "filtre": lambda dep: dep in OCCITANIE},
    "66": {"label": "Pyrénées-Orientales", "filtre": lambda dep: dep == "66"},
}
COUVERTURE = 0.8
COUVERTURE_MOIS_TERMINE = 0.7
N_MOIS = 13
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def r1(v: float | None) -> float | None:
    return None if v is None else round(float(v), 1)


def mois_liste(today: date) -> list[tuple[int, int]]:
    out, y, m = [], today.year, today.month
    for _ in range(N_MOIS):
        out.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def load_year(data_dir: Path, sid: str, year: int) -> dict[str, dict]:
    f = data_dir / "stations" / sid / f"{year}.json.gz"
    if not f.exists():
        return {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        return {row["date"]: row for row in json.load(fh).get("days", [])}


def record_annee(txt: str | None) -> int | None:
    try:
        return int(str(txt).split("-")[-1])
    except (TypeError, ValueError):
        return None


def record_item(typ: str, val: float, ancien: float, ancien_date: str | None, year: int) -> dict:
    # la fiche Météo-France intègre déjà les mois récents : si son record date de cette année, c'est ce mois-ci
    # qui l'a établi et l'ancienne valeur n'est plus connue
    if record_annee(ancien_date) == year:
        return {"type": typ, "valeur": val, "etabli_dans_fiche": True}
    return {"type": typ, "valeur": val, "ancien": ancien, "ancien_date": ancien_date}


def bilan_station(days: list[dict], jours_ecoules: int, jours_mois: int, norm: dict | None, year: int, en_cours: bool) -> dict | None:
    tn = [(d["date"], d["tn"]) for d in days if d.get("tn") is not None]
    tx = [(d["date"], d["tx"]) for d in days if d.get("tx") is not None]
    rr = [(d["date"], d["rr"]) for d in days if d.get("rr") is not None]
    ins = [d["insol_h"] for d in days if d.get("insol_h") is not None]
    need = COUVERTURE * jours_ecoules if en_cours else COUVERTURE_MOIS_TERMINE * jours_mois
    out: dict = {}
    temp_ok = len(tn) >= need and len(tx) >= need
    if temp_ok:
        mtn = sum(v for _, v in tn) / len(tn)
        mtx = sum(v for _, v in tx) / len(tx)
        out.update(tn=r1(mtn), tx=r1(mtx), tm=r1((mtn + mtx) / 2))
        hi = max(tx, key=lambda t: t[1])
        lo = min(tn, key=lambda t: t[1])
        out.update(txmax=hi[1], txmax_date=hi[0], tnmin=lo[1], tnmin_date=lo[0])
        out.update(
            j_tx25=sum(v >= 25 for _, v in tx), j_tx30=sum(v >= 30 for _, v in tx), j_tx35=sum(v >= 35 for _, v in tx),
            j_tn20=sum(v >= 20 for _, v in tn), j_tn0=sum(v <= 0 for _, v in tn), j_tnm5=sum(v <= -5 for _, v in tn),
        )
        if norm and norm.get("tm_moy") is not None:
            out["ecart_tm"] = r1((mtn + mtx) / 2 - norm["tm_moy"])
            out["ecart_tn"] = r1(mtn - norm["tn_moy"]) if norm.get("tn_moy") is not None else None
            out["ecart_tx"] = r1(mtx - norm["tx_moy"]) if norm.get("tx_moy") is not None else None
    if len(rr) >= need:
        tot = sum(v for _, v in rr)
        out["rr"] = r1(tot)
        out["j_rr1"] = sum(v >= 1 for _, v in rr)
        big = max(rr, key=lambda t: t[1])
        out.update(rrmax=big[1], rrmax_date=big[0])
        if norm and norm.get("rr_moy"):
            attendu = norm["rr_moy"] * len(rr) / jours_mois
            out["rr_pct"] = round(100 * tot / attendu) if attendu > 0 else None
    if len(ins) >= need:
        tot = sum(ins)
        out["insol"] = r1(tot)
        if norm and norm.get("insol_moy"):
            out["insol_pct"] = round(100 * tot / (norm["insol_moy"] * len(ins) / jours_mois))
    # records mensuels de la station (période d'archive complète) égalés ou battus ce mois-ci
    rec = []
    if norm and temp_ok:
        for champ, val, sens in (("tx", out["txmax"], 1), ("tn", out["tnmin"], -1)):
            r = norm.get(f"{champ}_record")
            if r is None:
                continue
            bat = val * sens > r * sens or (val == r and record_annee(norm.get(f"{champ}_record_date")) == year)
            if bat:
                rec.append(record_item("tx_max" if champ == "tx" else "tn_min", val, r, norm.get(f"{champ}_record_date"), year))
    if norm and "rrmax" in out and norm.get("rr_record") is not None:
        r = norm["rr_record"]
        if out["rrmax"] > r or (out["rrmax"] == r and record_annee(norm.get("rr_record_date")) == year):
            rec.append(record_item("rr_jour", out["rrmax"], r, norm.get("rr_record_date"), year))
    if rec:
        out["records"] = rec
    if not out:
        return None
    out["jours"] = len(days)
    return out


def indicateur_mois(ind_dir: Path, pid: str, year: int, month: int) -> dict | None:
    """Moyenne mensuelle de l'indicateur thermique, écart et rang parmi le même mois depuis 1950."""
    try:
        head = json.loads((ind_dir / f"{pid}.json").read_text(encoding="utf-8"))
        hist = json.loads((ind_dir / f"{pid}-historique.json").read_text(encoding="utf-8"))["annees"]
    except (OSError, ValueError, KeyError):
        return None
    clim = head["climatologie"]["tm"]
    debut = date(year, month, 1).timetuple().tm_yday - 1
    n_jours = calendar.monthrange(year, month)[1]

    def moy(y: int) -> tuple[float, float, int] | None:
        rows = hist.get(str(y))
        if not rows:
            return None
        off = date(y, month, 1).timetuple().tm_yday - 1
        vals, norms = [], []
        for i in range(calendar.monthrange(y, month)[1]):
            r = rows[off + i] if off + i < len(rows) else None
            if r and r[0] is not None and r[1] is not None:
                vals.append((r[0] + r[1]) / 2)
                k = date(2001, month, min(i + 1, 28 if month == 2 else 31)).timetuple().tm_yday - 1
                norms.append(clim[k])
        return (sum(vals) / len(vals), sum(vals) / len(vals) - sum(norms) / len(norms), len(vals)) if vals else None

    cur = moy(year)
    if not cur:
        return None
    # pour un mois en cours, on compare aux mêmes jours des autres années
    jours = cur[2]
    autres = []
    for ys in hist:
        y = int(ys)
        if y == year:
            continue
        rows = hist[ys]
        off = date(y, month, 1).timetuple().tm_yday - 1
        part = [rows[off + i] for i in range(min(jours, calendar.monthrange(y, month)[1])) if off + i < len(rows)]
        part = [(r[0] + r[1]) / 2 for r in part if r and r[0] is not None and r[1] is not None]
        if len(part) >= 0.9 * jours:
            ns = [clim[date(2001, month, min(i + 1, 28 if month == 2 else 31)).timetuple().tm_yday - 1] for i in range(len(part))]
            autres.append((y, sum(part) / len(part) - sum(ns) / len(ns)))
    rang_chaud = 1 + sum(a > cur[1] + 1e-9 for _, a in autres)
    plus_chaud = max(autres, key=lambda t: t[1]) if autres else None
    plus_froid = min(autres, key=lambda t: t[1]) if autres else None
    jour0 = debut
    return {
        "tm": r1(cur[0]), "ecart": r1(cur[1]), "jours": jours, "jours_mois": n_jours,
        "rang_chaud": rang_chaud, "rang_froid": 1 + sum(a < cur[1] - 1e-9 for _, a in autres), "annees": len(autres) + 1,
        "record_chaud": {"annee": plus_chaud[0], "ecart": r1(plus_chaud[1])} if plus_chaud else None,
        "record_froid": {"annee": plus_froid[0], "ecart": r1(plus_froid[1])} if plus_froid else None,
        "quotidien": [
            None if not (r := hist[str(year)][jour0 + i] if jour0 + i < len(hist[str(year)]) else None) or r[0] is None or r[1] is None
            else [r1((r[0] + r[1]) / 2), clim[date(2001, month, min(i + 1, 28 if month == 2 else 31)).timetuple().tm_yday - 1]]
            for i in range(n_jours)
        ],
        "historique": [{"annee": y, "ecart": r1(a)} for y, a in sorted(autres + [(year, cur[1])])],
    }


def resume_zone(stations: list[dict], meta: dict) -> dict:
    ec = [s["ecart_tm"] for s in stations if s.get("ecart_tm") is not None]
    rp = [s["rr_pct"] for s in stations if s.get("rr_pct") is not None]
    ip = [s["insol_pct"] for s in stations if s.get("insol_pct") is not None]
    rr = [s["rr"] for s in stations if s.get("rr") is not None]

    def top(key: str, rev: bool, n: int = 5, date_key: str | None = None) -> list[dict]:
        cand = [s for s in stations if s.get(key) is not None]
        cand.sort(key=lambda s: s[key], reverse=rev)
        return [{"id": s["id"], "nom": meta[s["id"]]["nom"], "dep": meta[s["id"]]["departement"], "valeur": s[key], **({"date": s.get(date_key)} if date_key else {})} for s in cand[:n]]

    recs = []
    for s in stations:
        for r in s.get("records", []):
            recs.append({"id": s["id"], "nom": meta[s["id"]]["nom"], "dep": meta[s["id"]]["departement"], **r})
    return {
        "stations": len(stations),
        "ecart_tm_moyen": r1(sum(ec) / len(ec)) if ec else None,
        "ecart_tm_stations": len(ec),
        "part_chaud": round(100 * sum(e > 0 for e in ec) / len(ec)) if ec else None,
        "rr_pct_median": round(median(rp)) if rp else None,
        "rr_pct_stations": len(rp),
        "part_sec": round(100 * sum(p < 75 for p in rp) / len(rp)) if rp else None,
        "part_humide": round(100 * sum(p > 125 for p in rp) / len(rp)) if rp else None,
        "rr_median": r1(median(rr)) if rr else None,
        "insol_pct_median": round(median(ip)) if ip else None,
        "insol_stations": len(ip),
        "extremes": {
            "tx_max": top("txmax", True, date_key="txmax_date"),
            "tn_min": top("tnmin", False, date_key="tnmin_date"),
            "rr_jour": top("rrmax", True, date_key="rrmax_date"),
            "rr_mois_max": top("rr", True),
            "rr_mois_min": top("rr", False),
            "ecart_chaud": top("ecart_tm", True),
            "ecart_froid": top("ecart_tm", False),
        },
        "records": recs,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="published-data")
    ap.add_argument("--indicateur-dir", default="build/indicateur")
    ap.add_argument("--output-dir", default="build/indicateur/bilan")
    ap.add_argument("--print-patterns", action="store_true", help="motifs sparse-checkout des fichiers nécessaires")
    args = ap.parse_args()
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    mois = mois_liste(today)
    if args.print_patterns:
        print("/stations/*/normales.json")
        for y in sorted({y for y, _ in mois}):
            print(f"/stations/*/{y}.json.gz")
        return 0

    data_dir = Path(args.data_dir)
    with gzip.open(data_dir / "stations.json.gz", "rt", encoding="utf-8") as fh:
        catalogue = [s for s in json.load(fh)["stations"] if s.get("active")]
    meta = {s["num_poste"]: s for s in catalogue}
    normales: dict[str, list[dict]] = {}
    for sid in meta:
        f = data_dir / "stations" / sid / "normales.json"
        if f.exists():
            try:
                normales[sid] = json.loads(f.read_text(encoding="utf-8")).get("months", [])
            except ValueError:
                pass
    cache: dict[tuple[str, int], dict[str, dict]] = {}
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ind_dir = Path(args.indicateur_dir)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    index = {"schema_version": 1, "pipeline_version": PIPELINE_VERSION, "generated_at": now, "mois": []}

    for y, m in mois:
        n_mois = calendar.monthrange(y, m)[1]
        jours_ecoules = n_mois if (y, m) != (today.year, today.month) else today.day - 1
        if jours_ecoules < 1:
            continue
        prefix = f"{y:04d}-{m:02d}-"
        stations = []
        derniere = None
        for sid in meta:
            if (sid, y) not in cache:
                cache[(sid, y)] = load_year(data_dir, sid, y)
            days = [v for k, v in cache[(sid, y)].items() if k.startswith(prefix)]
            if not days:
                continue
            norm = next((x for x in normales.get(sid, []) if x.get("mois") == m), None)
            b = bilan_station(days, jours_ecoules, n_mois, norm, y, (y, m) == (today.year, today.month))
            if b:
                b["id"] = sid
                stations.append(b)
                last = max(d["date"] for d in days)
                derniere = last if derniere is None or last > derniere else derniere
        if not stations:
            continue
        zones = {}
        for zid, z in ZONES.items():
            sel = [s for s in stations if z["filtre"](meta[s["id"]]["departement"])]
            zones[zid] = {"label": z["label"], **resume_zone(sel, meta), "indicateur": indicateur_mois(ind_dir, zid, y, m)}
        cle = f"{y:04d}-{m:02d}"
        en_cours = (y, m) == (today.year, today.month)
        doc = {
            "schema_version": 1,
            "generated_at": now,
            "mois": cle,
            "libelle": f"{MOIS_FR[m - 1]} {y}",
            "en_cours": en_cours,
            "jours_mois": n_mois,
            "derniere_donnee": derniere,
            "zones": zones,
            "stations": [{k: v for k, v in s.items() if v is not None} for s in stations],
        }
        (out / f"{cle}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index["mois"].append({"mois": cle, "libelle": doc["libelle"], "en_cours": en_cours, "derniere_donnee": derniere, "stations": len(stations)})
        z = zones["france"]
        print(f"{cle} : {len(stations)} stations, France écart {z['ecart_tm_moyen']}°C, pluie {z['rr_pct_median']} %, {len(z['records'])} records", flush=True)

    stations_meta = [
        {"id": sid, "nom": s["nom"], "dep": s["departement"], "lat": s["lat"], "lon": s["lon"], "alti": s.get("alti"), "normales": sid in normales}
        for sid, s in meta.items()
    ]
    (out / "stations.json").write_text(json.dumps({"generated_at": now, "stations": stations_meta}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
