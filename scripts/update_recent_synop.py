#!/usr/bin/env python3
"""Complète les derniers jours manquants avec l'archive SYNOP OMM.

Les observations ne remplacent jamais une journée issue de la climatologie
quotidienne. Elles sont repérées dans le JSON afin que les exécutions SYNOP
suivantes puissent les rafraîchir ; ce détail technique n'est pas affiché par
le module WordPress.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


DATASET_API_URL = "https://www.data.gouv.fr/api/1/datasets/archive-synop-omm/"
DATASET_PAGE = "https://www.data.gouv.fr/datasets/archive-synop-omm"
USER_AGENT = "alertes-meteo.com/climato-synop/1.0"
MAX_MATCH_DISTANCE_KM = 1.0
MAX_RECENT_DAYS = 7


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--today", type=date.fromisoformat, default=None,
                        help="Date UTC de référence (tests/rejeu, AAAA-MM-JJ).")
    return parser.parse_args()


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_json_gz(path: Path, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(data)


def iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resource_url(dataset: dict[str, Any], year: int) -> str:
    expected = f"synop_{year}"
    for resource in dataset.get("resources", []):
        if resource.get("type") == "main" and resource.get("title") == expected:
            return str(resource["url"])
    raise RuntimeError(f"Ressource {expected} introuvable dans le catalogue SYNOP")


def number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def kelvin_to_celsius(value: str | None) -> float | None:
    parsed = number(value)
    return None if parsed is None else round(parsed - 273.15, 1)


def precipitation(value: str | None) -> float | None:
    parsed = number(value)
    # Dans SYNOP, -0,1 signifie « traces ». Le tableau actuel ne possède pas
    # de rendu distinct pour les traces : on publie donc 0 mm.
    return None if parsed is None else round(max(0.0, parsed), 1)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def aggregate_synop(rows: Iterable[dict[str, str]], target_dates: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[float, float]]]:
    """Agrège les messages selon la journée climatologique Météo-France.

    Tmin vient du tn12 de 06 UTC, Tmax du tx12 de 18 UTC. La pluie quotidienne
    est le rr24 de 06 UTC le lendemain ; tant que ce message n'est pas encore
    arrivé, le dernier rr24 disponible après 18 UTC est utilisé.
    """
    slots: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    locations: dict[str, tuple[float, float]] = {}
    for row in rows:
        wmo = (row.get("geo_id_wmo") or "").strip().zfill(5)
        stamp = (row.get("validity_time") or "").strip()
        if not wmo or len(stamp) < 13:
            continue
        day = stamp[:10]
        previous = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        if day not in target_dates and previous not in target_dates:
            continue
        lat, lon = number(row.get("lat")), number(row.get("lon"))
        if lat is not None and lon is not None:
            locations[wmo] = (lat, lon)
        slots[(wmo, day)][stamp[11:13]] = row

    result: dict[str, dict[str, Any]] = {}
    for (wmo, day), hours in slots.items():
        if day not in target_dates:
            continue
        at06, at18 = hours.get("06"), hours.get("18")
        if not at06 or not at18:
            continue
        next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
        next06 = slots.get((wmo, next_day), {}).get("06")
        rain_row = next06
        if rain_row is None:
            later = [row for hour, row in hours.items() if hour >= "18" and number(row.get("rr24")) is not None]
            rain_row = later[-1] if later else None
        result[f"{wmo}:{day}"] = {
            "date": day,
            "tx": kelvin_to_celsius(at18.get("tx12")),
            "tn": kelvin_to_celsius(at06.get("tn12")),
            "rr": precipitation(rain_row.get("rr24")) if rain_row else None,
            "insol_h": None,
            "source": "synop",
        }
    return result, locations


def nearest_wmo(station: dict[str, Any], locations: dict[str, tuple[float, float]]) -> str | None:
    if station.get("lat") is None or station.get("lon") is None:
        return None
    distances = [
        (haversine_km(float(station["lat"]), float(station["lon"]), lat, lon), wmo)
        for wmo, (lat, lon) in locations.items()
    ]
    if not distances:
        return None
    distance, wmo = min(distances)
    return wmo if distance <= MAX_MATCH_DISTANCE_KM else None


def merge_days(existing: list[dict[str, Any]], additions: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    by_date = {day["date"]: day for day in existing}
    changed = False
    for addition in additions:
        current = by_date.get(addition["date"])
        # Une journée climatologique (sans source=synop) reste prioritaire.
        if current is not None and current.get("source") != "synop":
            continue
        if current != addition:
            by_date[addition["date"]] = addition
            changed = True
    return [by_date[key] for key in sorted(by_date)], changed


def main() -> int:
    args = parse_args()
    data_dir: Path = args.data_dir
    today = args.today or datetime.now(timezone.utc).date()
    latest_complete = today - timedelta(days=1)
    earliest = latest_complete - timedelta(days=MAX_RECENT_DAYS - 1)
    target_dates = {(earliest + timedelta(days=i)).isoformat() for i in range(MAX_RECENT_DAYS)}

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    dataset = session.get(DATASET_API_URL, timeout=(10, 60))
    dataset.raise_for_status()
    synop_url = resource_url(dataset.json(), today.year)
    response = session.get(synop_url, timeout=(10, 180))
    response.raise_for_status()
    with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
        reader = csv.DictReader(io.TextIOWrapper(gz, encoding="utf-8", newline=""), delimiter=";")
        synop_days, locations = aggregate_synop(reader, target_dates)

    stations_path = data_dir / "stations.json.gz"
    index_path = data_dir / "index.json"
    stations_payload = read_json_gz(stations_path)
    changed_files = 0

    for station in stations_payload.get("stations", []):
        if not station.get("active", True):
            continue
        wmo = nearest_wmo(station, locations)
        if wmo is None:
            continue
        additions = [synop_days[f"{wmo}:{day}"] for day in sorted(target_dates)
                     if f"{wmo}:{day}" in synop_days]
        if not additions:
            continue
        year_path = data_dir / "stations" / station["num_poste"] / f"{today.year}.json.gz"
        if not year_path.is_file():
            continue
        year_payload = read_json_gz(year_path)
        merged, changed = merge_days(year_payload.get("days", []), additions)
        if not changed:
            continue
        year_payload["days"] = merged
        write_json_gz(year_path, year_payload)
        station["last_date"] = merged[-1]["date"]
        if today.year not in station.get("years", []):
            station.setdefault("years", []).append(today.year)
            station["years"].sort()
        changed_files += 1

    if not changed_files:
        print("Aucune observation SYNOP nouvelle à publier.")
        return 0

    generated_at = iso_utc()
    stations_payload["generated_at"] = generated_at
    write_json_gz(stations_path, stations_payload)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["generated_at"] = generated_at
    index.setdefault("source", {})["recent_observations_url"] = DATASET_PAGE
    all_last_dates = [s.get("last_date") for s in stations_payload.get("stations", []) if s.get("last_date")]
    index.setdefault("period", {})["last_date"] = max(all_last_dates)
    write_json(index_path, index)
    print(f"SYNOP : {changed_files} fichier(s) station/année mis à jour jusqu'au {latest_complete}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


