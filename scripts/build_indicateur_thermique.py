"""Indicateur thermique : température moyenne d'un panel de stations Météo-France, comparée aux normales 1991-2020.

Trois panels : France métropolitaine (30 stations de référence), Occitanie (une ou deux stations par
département) et Pyrénées-Orientales (10 postes, du littoral à la montagne).

Méthode « par anomalies » : pour chaque station on calcule ses normales quotidiennes 1991-2020 (Tn et Tx,
lissées par une série de Fourier à 3 harmoniques), puis l'écart du jour à ces normales. L'indicateur du jour
= moyenne des normales du panel + moyenne des écarts des stations disponibles. Une station manquante ne
biaise donc pas la valeur (contrairement à une moyenne brute des températures).

Sources :
- relevés quotidiens Météo-France (branche « data » du dépôt, déjà complétée par les SYNOP récents) ;
- prévision Open-Meteo (meilleur modèle disponible, débiaisée station par station sur les 10 derniers jours
  observés) et ensemble ECMWF IFS 0,25° (51 membres) pour l'incertitude à 15 jours.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import requests

PIPELINE_VERSION = "1.0.0"
FIRST_YEAR = 1950
NORMALES = (1991, 2020)
MIN_COVERAGE = 0.6  # part des stations nécessaire pour publier un jour
UA = "Mozilla/5.0 (compatible; AlertesMeteo-IndicateurThermique/1.0)"

PANELS: dict[str, dict] = {
    "france": {
        "label": "France métropolitaine",
        "description": "30 stations Météo-France de référence réparties sur la métropole",
        "stations": [
            "80001001", "20004002", "68297001", "20148001", "64024001", "33281001", "18033001", "29075001",
            "14137001", "63113001", "21473001", "05046001", "46127001", "59343001", "87085006", "69029001",
            "13054001", "12145001", "26198001", "34154001", "54526001", "44020001", "06088001", "75114001",
            "66136001", "86027001", "35281001", "67124001", "31069001", "37179001",
        ],
    },
    "occitanie": {
        "label": "Occitanie",
        "description": "15 stations Météo-France, au moins une par département de la région",
        "stations": [
            "66136001", "11069001", "09289001", "12145001", "12254001", "30189001", "31069001", "32013005",
            "34154001", "34301002", "46127001", "48095005", "65344001", "81284001", "82121002",
        ],
    },
    "66": {
        "label": "Pyrénées-Orientales",
        "description": "10 postes Météo-France, du littoral roussillonnais au Capcir",
        "stations": [
            "66136001", "66148001", "66212001", "66024001", "66074002", "66137003", "66194002", "66233001",
            "66187006", "66082004",
        ],
    },
}


def all_station_ids() -> list[str]:
    return sorted({s for p in PANELS.values() for s in p["stations"]})


def doy365(d: date) -> int:
    """Rang du jour dans une année non bissextile (0-364) ; le 29 février prend le rang du 28."""
    base = date(2001, d.month, 28 if (d.month == 2 and d.day == 29) else d.day)
    return base.timetuple().tm_yday - 1


def fourier(doys: np.ndarray, k: int = 3) -> np.ndarray:
    w = 2 * np.pi * doys / 365.0
    cols = [np.ones_like(w)]
    for i in range(1, k + 1):
        cols += [np.cos(i * w), np.sin(i * w)]
    return np.stack(cols, axis=1)


def load_station(data_dir: Path, sid: str) -> dict[date, tuple[float | None, float | None]]:
    out: dict[date, tuple[float | None, float | None]] = {}
    folder = data_dir / "stations" / sid
    for f in sorted(folder.glob("*.json.gz")):
        try:
            year = int(f.name.split(".")[0])
        except ValueError:
            continue
        if year < FIRST_YEAR:
            continue
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for row in json.load(fh).get("days", []):
                tn, tx = row.get("tn"), row.get("tx")
                if tn is None and tx is None:
                    continue
                out[date.fromisoformat(row["date"])] = (tn, tx)
    return out


def normals_for(series: dict[date, tuple[float | None, float | None]]) -> tuple[np.ndarray, np.ndarray] | None:
    """Normales quotidiennes Tn, Tx (365 valeurs) ajustées sur 1991-2020."""
    res = []
    for idx in (0, 1):
        ds = [(doy365(d), v[idx]) for d, v in series.items() if NORMALES[0] <= d.year <= NORMALES[1] and v[idx] is not None]
        if len(ds) < 365 * 15:
            return None
        x = np.array([a for a, _ in ds], dtype=float)
        y = np.array([b for _, b in ds], dtype=float)
        coef, *_ = np.linalg.lstsq(fourier(x), y, rcond=None)
        res.append(fourier(np.arange(365, dtype=float)) @ coef)
    return res[0], res[1]


def get_json(url: str, params: dict) -> object:
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=(15, 90), headers={"User-Agent": UA})
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 4:
                raise
            wait = 10 * 2**attempt
            print(f"Open-Meteo : {exc}, reprise dans {wait} s", flush=True)
            time.sleep(wait)
    raise RuntimeError("inaccessible")


def fetch_forecasts(meta: dict[str, dict]) -> tuple[dict, dict]:
    """Prévision déterministe (10 jours passés + 10 à venir) et ensemble ECMWF (15 jours), par station."""
    ids = list(meta)
    lat = ",".join(f"{meta[s]['lat']:.4f}" for s in ids)
    lon = ",".join(f"{meta[s]['lon']:.4f}" for s in ids)
    elev = ",".join(f"{meta[s]['alti']:.0f}" for s in ids)
    base = {"latitude": lat, "longitude": lon, "elevation": elev, "timezone": "Europe/Paris", "daily": "temperature_2m_max,temperature_2m_min"}
    det_raw = get_json("https://api.open-meteo.com/v1/forecast", {**base, "past_days": 10, "forecast_days": 10})
    det_list = det_raw if isinstance(det_raw, list) else [det_raw]
    det = {}
    for sid, block in zip(ids, det_list):
        d = block["daily"]
        det[sid] = {date.fromisoformat(t): (n, x) for t, n, x in zip(d["time"], d["temperature_2m_min"], d["temperature_2m_max"])}
    ens: dict[str, dict] = {}
    try:
        ens_raw = get_json("https://ensemble-api.open-meteo.com/v1/ensemble", {**base, "models": "ecmwf_ifs025", "forecast_days": 15})
        ens_list = ens_raw if isinstance(ens_raw, list) else [ens_raw]
        for sid, block in zip(ids, ens_list):
            d = block["daily"]
            members = sorted(k[len("temperature_2m_max"):] for k in d if k.startswith("temperature_2m_max"))
            per_day: dict[date, list[tuple[float, float]]] = {}
            for i, t in enumerate(d["time"]):
                vals = []
                for m in members:
                    x, n = d.get("temperature_2m_max" + m, [None])[i], d.get("temperature_2m_min" + m, [None])[i]
                    vals.append((n, x))
                per_day[date.fromisoformat(t)] = vals
            ens[sid] = per_day
    except Exception as exc:  # l'ensemble est un plus : son absence ne bloque pas la publication
        print(f"Ensemble ECMWF indisponible : {exc}", flush=True)
    return det, ens


def r1(v: float | None) -> float | None:
    return None if v is None or not np.isfinite(v) else round(float(v), 1)


def build_panel(pid: str, panel: dict, series: dict, normals: dict, meta: dict, det: dict, ens: dict, today: date) -> tuple[dict, dict]:
    sids = [s for s in panel["stations"] if s in normals]
    if len(sids) < max(3, len(panel["stations"]) // 2):
        raise RuntimeError(f"{pid} : seulement {len(sids)} stations exploitables")
    n_tn = np.mean([normals[s][0] for s in sids], axis=0)
    n_tx = np.mean([normals[s][1] for s in sids], axis=0)
    need = max(2, int(np.ceil(MIN_COVERAGE * len(sids))))

    last_obs = max(max(series[s]) for s in sids)
    # indicateur observé, jour par jour
    obs: dict[date, tuple[float, float, int]] = {}
    d = date(FIRST_YEAR, 1, 1)
    while d <= last_obs:
        k = doy365(d)
        an_n, an_x = [], []
        for s in sids:
            v = series[s].get(d)
            if v and v[0] is not None and v[1] is not None:
                an_n.append(v[0] - normals[s][0][k])
                an_x.append(v[1] - normals[s][1][k])
        # les derniers jours, seules les stations SYNOP sont à jour : on exige au moins 2 stations
        floor = need if (last_obs - d).days > 20 else min(need, max(2, len(sids) // 4))
        if len(an_n) >= floor:
            obs[d] = (n_tn[k] + float(np.mean(an_n)), n_tx[k] + float(np.mean(an_x)), len(an_n))
        d += timedelta(days=1)
    # date de référence « observée » : dernier jour où le panel est suffisamment couvert
    last_full = max(obs) if obs else last_obs

    # prévision déterministe débiaisée par station
    bias: dict[str, tuple[float, float]] = {}
    for s in sids:
        pairs = [(det[s][dd], series[s][dd]) for dd in det.get(s, {}) if dd in series[s] and dd <= last_full]
        pn = [m[0] - o[0] for m, o in pairs if m[0] is not None and o[0] is not None]
        px = [m[1] - o[1] for m, o in pairs if m[1] is not None and o[1] is not None]
        bias[s] = (float(np.clip(np.mean(pn), -5, 5)) if len(pn) >= 3 else 0.0, float(np.clip(np.mean(px), -5, 5)) if len(px) >= 3 else 0.0)

    fc = []
    all_days = sorted({dd for s in sids for dd in det.get(s, {})} | {dd for s in sids for dd in ens.get(s, {})})
    for dd in all_days:
        if dd <= last_full:
            continue
        k = doy365(dd)
        an_n, an_x = [], []
        for s in sids:
            v = det.get(s, {}).get(dd)
            if v and v[0] is not None and v[1] is not None:
                an_n.append(v[0] - bias[s][0] - normals[s][0][k])
                an_x.append(v[1] - bias[s][1] - normals[s][1][k])
        item: dict = {"date": dd.isoformat(), "estimation": dd <= today}
        if len(an_n) >= need:
            tn, tx = n_tn[k] + np.mean(an_n), n_tx[k] + np.mean(an_x)
            item.update(tn=r1(tn), tx=r1(tx), tm=r1((tn + tx) / 2))
        # ensemble : indicateur par membre puis quantiles
        mem_vals = []
        n_members = min((len(ens[s][dd]) for s in sids if s in ens and dd in ens[s]), default=0)
        for m in range(n_members):
            an = []
            for s in sids:
                v = ens.get(s, {}).get(dd)
                if v and v[m][0] is not None and v[m][1] is not None:
                    an.append(((v[m][0] - bias[s][0] - normals[s][0][k]) + (v[m][1] - bias[s][1] - normals[s][1][k])) / 2)
            if len(an) >= need:
                mem_vals.append((n_tn[k] + n_tx[k]) / 2 + float(np.mean(an)))
        if len(mem_vals) >= 10:
            q = np.percentile(mem_vals, [10, 50, 90])
            item.update(ens_p10=r1(q[0]), ens_p50=r1(q[1]), ens_p90=r1(q[2]), ens_n=len(mem_vals))
        if "tm" in item or "ens_p50" in item:
            fc.append(item)

    # climatologie quotidienne de l'indicateur
    tm_by_doy: dict[int, list[tuple[int, float]]] = {}
    for dd, (tn, tx, _) in obs.items():
        tm_by_doy.setdefault(doy365(dd), []).append((dd.year, (tn + tx) / 2))
    p10, p90, rmax, rmaxy, rmin, rminy = [], [], [], [], [], []
    for k in range(365):
        window = []
        for j in range(k - 7, k + 8):
            window += [v for y, v in tm_by_doy.get(j % 365, []) if NORMALES[0] <= y <= NORMALES[1]]
        p10.append(r1(np.percentile(window, 10)) if window else None)
        p90.append(r1(np.percentile(window, 90)) if window else None)
        day = tm_by_doy.get(k, [])
        hi = max(day, key=lambda t: t[1]) if day else (None, None)
        lo = min(day, key=lambda t: t[1]) if day else (None, None)
        rmax.append(r1(hi[1])); rmaxy.append(hi[0]); rmin.append(r1(lo[1])); rminy.append(lo[0])

    # séries annuelles [tn, tx, n] alignées sur le 1er janvier (366 cases, 29/02 compris)
    years: dict[str, list] = {}
    for y in range(FIRST_YEAR, last_full.year + 1):
        n_days = (date(y + 1, 1, 1) - date(y, 1, 1)).days
        rows = []
        for i in range(n_days):
            v = obs.get(date(y, 1, 1) + timedelta(days=i))
            rows.append([r1(v[0]), r1(v[1]), v[2]] if v else None)
        while rows and rows[-1] is None:
            rows.pop()
        if rows:
            years[str(y)] = rows

    n_tm = (n_tn + n_tx) / 2
    annual = []
    for y in range(FIRST_YEAR, last_full.year + 1):
        vals = [((v[0] + v[1]) / 2, doy365(dd)) for dd, v in obs.items() if dd.year == y]
        if not vals:
            continue
        an = float(np.mean([v - n_tm[k] for v, k in vals]))
        ytd = [((v[0] + v[1]) / 2) - n_tm[doy365(dd)] for dd, v in obs.items() if dd.year == y and doy365(dd) <= doy365(last_full)]
        annual.append({
            "year": y,
            "tm": r1(float(np.mean([v for v, _ in vals]))),
            "anomalie": r1(an),
            "jours": len(vals),
            "complete": len(vals) >= 360,
            "anomalie_cumul": r1(float(np.mean(ytd))) if ytd else None,
        })

    stations_out = []
    for s in panel["stations"]:
        m = meta.get(s, {})
        item = {"id": s, "nom": m.get("nom", s), "departement": m.get("departement"), "lat": m.get("lat"), "lon": m.get("lon"), "alti": m.get("alti"), "exploitee": s in normals}
        if s in series and series[s]:
            ld = max(series[s])
            tn, tx = series[s][ld]
            item["dernier"] = {"date": ld.isoformat(), "tn": tn, "tx": tx}
            if s in normals:
                k = doy365(ld)
                item["dernier"]["ecart_tn"] = r1(tn - normals[s][0][k]) if tn is not None else None
                item["dernier"]["ecart_tx"] = r1(tx - normals[s][1][k]) if tx is not None else None
            if s in normals:
                last7 = [series[s][dd] for dd in sorted(series[s])[-7:]]
                ks = [doy365(dd) for dd in sorted(series[s])[-7:]]
                ecarts = [((v[0] + v[1]) / 2 - (normals[s][0][k] + normals[s][1][k]) / 2) for v, k in zip(last7, ks) if v[0] is not None and v[1] is not None]
                item["ecart_7j"] = r1(float(np.mean(ecarts))) if ecarts else None
            item["normale_tn"], item["normale_tx"] = (r1(normals[s][0][doy365(today)]), r1(normals[s][1][doy365(today)])) if s in normals else (None, None)
        stations_out.append(item)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    head = {
        "schema_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "status": "ok",
        "generated_at": now,
        "panel": {"id": pid, "label": panel["label"], "description": panel["description"], "stations_utilisees": len(sids)},
        "normales_periode": f"{NORMALES[0]}-{NORMALES[1]}",
        "premiere_annee": FIRST_YEAR,
        "derniere_observation": last_full.isoformat(),
        "climatologie": {
            "tn": [r1(v) for v in n_tn], "tx": [r1(v) for v in n_tx], "tm": [r1(v) for v in n_tm],
            "p10": p10, "p90": p90, "record_max": rmax, "record_max_annee": rmaxy, "record_min": rmin, "record_min_annee": rminy,
        },
        "annees_recentes": {y: years[y] for y in sorted(years)[-2:]},
        "annuel": annual,
        "prevision": {
            "source": "Open-Meteo (meilleur modèle, débiaisé sur 10 jours d'observations) · ensemble ECMWF IFS 0,25°",
            "jours": fc,
        },
        "stations": stations_out,
        "sources": {
            "observations": "Météo-France — Données climatologiques de base quotidiennes et archive SYNOP (Licence Ouverte 2.0)",
            "prevision": "Open-Meteo.com (CC BY 4.0)",
        },
    }
    hist = {"schema_version": 1, "generated_at": now, "panel": pid, "annees": years}
    return head, hist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="published-data")
    ap.add_argument("--output-dir", default="build/indicateur")
    ap.add_argument("--print-paths", action="store_true", help="liste des dossiers de stations à extraire (sparse-checkout)")
    args = ap.parse_args()
    if args.print_paths:
        print("\n".join(f"stations/{s}" for s in all_station_ids()))
        return 0

    data_dir = Path(args.data_dir)
    with gzip.open(data_dir / "stations.json.gz", "rt", encoding="utf-8") as fh:
        catalogue = {s["num_poste"]: s for s in json.load(fh)["stations"]}
    meta = {s: catalogue[s] for s in all_station_ids() if s in catalogue}
    missing = sorted(set(all_station_ids()) - set(meta))
    if missing:
        print("Stations absentes du catalogue :", ", ".join(missing), flush=True)

    series, normals = {}, {}
    for s in meta:
        series[s] = load_station(data_dir, s)
        nr = normals_for(series[s]) if series[s] else None
        if nr is None:
            print(f"{s} {meta[s]['nom']} : pas assez de données 1991-2020, station ignorée", flush=True)
        else:
            normals[s] = nr
    print(f"{len(normals)}/{len(meta)} stations avec normales", flush=True)

    det, ens = fetch_forecasts({s: meta[s] for s in normals})
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = {"schema_version": 1, "pipeline_version": PIPELINE_VERSION, "generated_at": None, "panels": {}}
    for pid, panel in PANELS.items():
        head, hist = build_panel(pid, panel, series, normals, meta, det, ens, today)
        (out / f"{pid}.json").write_text(json.dumps(head, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (out / f"{pid}-historique.json").write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        index["generated_at"] = head["generated_at"]
        last = head["annees_recentes"][max(head["annees_recentes"])]
        index["panels"][pid] = {"label": panel["label"], "fichier": f"{pid}.json", "historique": f"{pid}-historique.json", "derniere_observation": head["derniere_observation"], "stations": head["panel"]["stations_utilisees"]}
        print(f"{pid} : {head['panel']['stations_utilisees']} stations, dernière observation {head['derniere_observation']}, {len(head['prevision']['jours'])} jours de prévision, dernier jour {[v for v in last if v][-1]}", flush=True)
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
