#!/usr/bin/env python3
"""Construit les fichiers de climatologie mensuelle par station pour la France.

Source : Météo-France, jeu de données publiques « Données climatologiques de
base - quotidiennes » (data.gouv.fr, Licence Ouverte / Etalab 2.0). Aucune clé
API n'est nécessaire : les fichiers CSV compressés sont téléchargés directement
par département, puis fusionnés par station et par jour.

Historique complet : toutes les périodes publiées par Météo-France sont
téléchargées pour chaque département (« avant-1949 », « 1950-2024 », «
2025-2026 », etc. — les bornes exactes glissent chaque année et sont résolues
dynamiquement via l'API data.gouv.fr, jamais codées en dur). Certaines
stations parisiennes remontent ainsi à 1816.

Pour rester publiable (des dizaines de millions de relevés au total) et
publiable sans exploser la mémoire du runner, chaque département est traité
puis écrit sur disque avant de passer au suivant, et chaque station est
éclatée en un fichier JSON par année (« stations/<num_poste>/<année>.json.gz »)
plutôt qu'un unique fichier contenant tout l'historique : le site n'a besoin
de télécharger que l'année réellement consultée.

Ces fichiers par année, ainsi que le catalogue « stations.json », sont
compressés gzip (« .json.gz ») : en clair, l'historique complet dépasserait
le seuil d'alerte de taille de dépôt GitHub (~5 Go). Le site les décompresse
côté navigateur avec l'API native DecompressionStream (aucune dépendance JS
ajoutée). « departements.json » et « index.json » restent en clair : trop
petits pour que ça vaille la peine.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

LOGGER = logging.getLogger("climato.france")
PIPELINE_VERSION = "1.2.0"
USER_AGENT = "alertes-meteo.com/climato-meteofrance-france/1.2.0"

DATASET_API_URL = (
    "https://www.data.gouv.fr/api/1/datasets/6569b51ae64326786e4e8e1a/"
)
DATASET_PAGE = (
    "https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes"
)

# Départements de la France métropolitaine. Le jeu de données Météo-France
# regroupe la Corse sous le code historique "20" (pas de scission 2A/2B).
DEPARTMENTS: dict[str, str] = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "20": "Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne",
    "25": "Doubs", "26": "Drôme", "27": "Eure", "28": "Eure-et-Loir",
    "29": "Finistère", "30": "Gard", "31": "Haute-Garonne", "32": "Gers",
    "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre",
    "37": "Indre-et-Loire", "38": "Isère", "39": "Jura", "40": "Landes",
    "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire", "44": "Loire-Atlantique",
    "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère",
    "49": "Maine-et-Loire", "50": "Manche", "51": "Marne", "52": "Haute-Marne",
    "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan",
    "57": "Moselle", "58": "Nièvre", "59": "Nord", "60": "Oise",
    "61": "Orne", "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin", "68": "Haut-Rhin",
    "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe",
    "73": "Savoie", "74": "Haute-Savoie", "75": "Paris", "76": "Seine-Maritime",
    "77": "Seine-et-Marne", "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme",
    "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var", "84": "Vaucluse",
    "85": "Vendée", "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges",
    "89": "Yonne", "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine",
    "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
}

# Reconnaît aussi bien "..._periode_avant-1949_..." que "..._periode_1950-2024_...".
RESOURCE_RE = re.compile(
    r"^QUOT_departement_(?P<dept>\d{2})_periode_"
    r"(?:avant-(?P<avant_end>\d{4})|(?P<start>\d{4})-(?P<end>\d{4}))"
    r"_(?P<kind>RR-T-Vent|autres-parametres)$"
)

# Seuils des statistiques du mois (mêmes définitions que les tableaux de
# climatologie mensuelle usuels : Météo-France / Meteociel).
STAT_THRESHOLDS = (
    ("jours_chaleur", "Jours de chaleur (Tmax >= 25°C)", "tx", ">=", 25.0),
    ("jours_forte_chaleur", "Jours de forte chaleur (Tmax >= 30°C)", "tx", ">=", 30.0),
    ("jours_tres_forte_chaleur", "Jours de très forte chaleur (Tmax >= 35°C)", "tx", ">=", 35.0),
    ("jours_nuit_tropicale", "Jours avec nuit tropicale (Tmin >= 20°C)", "tn", ">=", 20.0),
    ("jours_gelee", "Jours avec gelée (Tmin <= 0°C)", "tn", "<=", 0.0),
    ("jours_forte_gelee", "Jours avec forte gelée (Tmin <= -5°C)", "tn", "<=", -5.0),
    ("jours_tres_forte_gelee", "Jours avec très forte gelée (Tmin <= -10°C)", "tn", "<=", -10.0),
    ("jours_sans_degel", "Jours sans dégel (Tmax <= 0°C)", "tx", "<=", 0.0),
    ("jours_pluie", "Jours avec pluie (RR >= 1 mm)", "rr", ">=", 1.0),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("build/national"),
        help="Dossier de sortie pour les fichiers JSON publiés.",
    )
    parser.add_argument(
        "--departments", nargs="*", default=None,
        help="Sous-ensemble de départements à traiter (ex: 28 75 92). "
        "Par défaut : toute la France métropolitaine.",
    )
    parser.add_argument(
        "--current-metadata-url", default=None,
        help="URL de l'index.json déjà publié, pour ignorer une republication "
        "inutile si aucune nouvelle journée n'est disponible.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def iso_utc(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def fetch_with_retries(
    session: requests.Session, url: str, *, retries: int = 4, timeout: tuple[int, int] = (10, 180)
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            wait = min(30, 2 ** attempt)
            LOGGER.warning("Échec (%s/%s) pour %s : %s — nouvelle tentative dans %ss",
                            attempt, retries, url, exc, wait)
            time.sleep(wait)
    assert last_error is not None
    raise last_error


def fetch_json(session: requests.Session, url: str) -> Any:
    return fetch_with_retries(session, url).json()


def build_resource_index(dataset: dict) -> dict[str, dict[str, list[str]]]:
    """Associe à chaque département TOUTES les périodes disponibles (triées
    de la plus ancienne à la plus récente), pour chaque type de fichier."""
    found: dict[str, dict[str, list[tuple[int, str]]]] = {}
    for resource in dataset.get("resources", []):
        title = (resource.get("title") or "").strip()
        match = RESOURCE_RE.match(title)
        url = resource.get("url")
        if not match or not url:
            continue
        dept = match.group("dept")
        kind = match.group("kind")
        end_year = int(match.group("end") or match.group("avant_end"))
        found.setdefault(dept, {}).setdefault(kind, []).append((end_year, url))

    resolved: dict[str, dict[str, list[str]]] = {}
    for dept, kinds in found.items():
        resolved[dept] = {
            kind: [url for _year, url in sorted(urls, key=lambda item: item[0])]
            for kind, urls in kinds.items()
        }
    return resolved


def to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def download_csv_rows(session: requests.Session, url: str) -> Iterator[dict[str, str]]:
    response = fetch_with_retries(session, url)
    with gzip.GzipFile(fileobj=io.BytesIO(response.content)) as gz:
        text = io.TextIOWrapper(gz, encoding="latin-1", newline="")
        reader = csv.DictReader(text, delimiter=";")
        yield from reader


def clean_station_name(raw: str) -> str:
    return raw.strip().replace("_", " ").title()


def process_department(
    session: requests.Session, dept: str, urls_by_kind: dict[str, list[str]]
) -> dict[str, dict[str, Any]]:
    """Retourne {num_poste: {..., "days": {"YYYY-MM-DD": {...}}}} en fusionnant
    toutes les périodes disponibles (des plus anciennes aux plus récentes)."""
    stations: dict[str, dict[str, Any]] = {}

    rrtvent_urls = urls_by_kind.get("RR-T-Vent") or []
    if not rrtvent_urls:
        LOGGER.warning("Département %s : fichier RR-T-Vent introuvable, ignoré.", dept)
        return stations

    for url in rrtvent_urls:
        row_count = 0
        for row in download_csv_rows(session, url):
            row_count += 1
            num_poste = (row.get("NUM_POSTE") or "").strip()
            raw_date = (row.get("AAAAMMJJ") or "").strip()
            if not num_poste or len(raw_date) != 8:
                continue
            date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            station = stations.get(num_poste)
            if station is None:
                station = {
                    "num_poste": num_poste,
                    "nom": clean_station_name(row.get("NOM_USUEL") or num_poste),
                    "departement": dept,
                    "lat": to_float(row.get("LAT")),
                    "lon": to_float(row.get("LON")),
                    "alti": to_float(row.get("ALTI")),
                    "days": {},
                }
                stations[num_poste] = station
            station["days"][date] = {
                "date": date,
                "tx": to_float(row.get("TX")),
                "tn": to_float(row.get("TN")),
                "rr": to_float(row.get("RR")),
                "insol_h": None,
            }
        LOGGER.debug("  %s : %s lignes RR-T-Vent", url, row_count)

    autres_urls = urls_by_kind.get("autres-parametres") or []
    if not autres_urls:
        LOGGER.info("Département %s : pas de fichier autres-parametres (pas d'ensoleillement).", dept)

    for url in autres_urls:
        for row in download_csv_rows(session, url):
            num_poste = (row.get("NUM_POSTE") or "").strip()
            station = stations.get(num_poste)
            if station is None:
                continue
            raw_date = (row.get("AAAAMMJJ") or "").strip()
            if len(raw_date) != 8:
                continue
            date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            insol = to_float(row.get("INST"))
            if insol is None:
                continue
            day = station["days"].get(date)
            if day is None:
                day = {"date": date, "tx": None, "tn": None, "rr": None, "insol_h": None}
                station["days"][date] = day
            day["insol_h"] = round(insol / 60.0, 1)

    return stations


def write_json(path: Path, payload: Any) -> None:
    """Écrit un petit fichier JSON non compressé (departements.json, index.json :
    consultés une seule fois, taille négligeable, pas besoin de gzip)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_json_gz(path: Path, payload: Any) -> None:
    """Écrit un fichier JSON compressé gzip (extension .json.gz). Le volume total
    de l'historique complet (plusieurs Go en clair sur 95 départements)
    dépasserait le seuil d'alerte de taille de dépôt GitHub (5 Go) ; compressé,
    on reste largement en dessous. Le site le décompresse côté navigateur avec
    DecompressionStream (natif, pas de dépendance JS)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=6) as fh:
        fh.write(data)


def write_station_years(stations_dir: Path, num_poste: str, days: list[dict[str, Any]]) -> list[int]:
    """Éclate la série d'un poste en un fichier JSON.gz par année. Retourne les
    années réellement écrites (triées)."""
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for day in days:
        by_year[int(day["date"][0:4])].append(day)

    for year, year_days in by_year.items():
        write_json_gz(
            stations_dir / num_poste / f"{year}.json.gz",
            {"num_poste": num_poste, "year": year, "days": year_days},
        )
    return sorted(by_year.keys())


def already_published(current_metadata_url: str | None, last_date: str | None, station_count: int) -> bool:
    if not current_metadata_url or not last_date:
        return False
    try:
        session = new_session()
        previous = fetch_json(session, current_metadata_url)
    except Exception as exc:  # noqa: BLE001 - une erreur réseau ne doit jamais bloquer une republication
        LOGGER.info("Impossible de lire l'index déjà publié (%s), republication.", exc)
        return False
    period = previous.get("period") or {}
    coverage = previous.get("coverage") or {}
    return (
        period.get("last_date") == last_date
        and coverage.get("stations") == station_count
        and previous.get("pipeline_version") == PIPELINE_VERSION
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    wanted_depts = set(args.departments) if args.departments else set(DEPARTMENTS)
    unknown = wanted_depts - set(DEPARTMENTS)
    if unknown:
        LOGGER.error("Départements inconnus : %s", ", ".join(sorted(unknown)))
        return 2

    session = new_session()

    LOGGER.info("Récupération du catalogue des ressources data.gouv.fr…")
    dataset = fetch_json(session, DATASET_API_URL)
    resource_index = build_resource_index(dataset)

    output_dir: Path = args.output_dir
    stations_dir = output_dir / "stations"

    station_catalog: list[dict[str, Any]] = []
    last_dates: list[str] = []
    first_dates: list[str] = []
    processed = 0

    for dept in sorted(wanted_depts):
        urls_by_kind = resource_index.get(dept)
        if not urls_by_kind:
            LOGGER.warning("Département %s : aucune ressource trouvée, ignoré.", dept)
            continue
        periods = len(urls_by_kind.get("RR-T-Vent") or [])
        LOGGER.info("Département %s (%s) — %s période(s)…", dept, DEPARTMENTS[dept], periods)

        stations = process_department(session, dept, urls_by_kind)
        for num_poste, station in sorted(stations.items()):
            days = compact_days(station["days"])
            if not days:
                continue
            first_date = days[0]["date"]
            last_date = days[-1]["date"]
            first_dates.append(first_date)
            last_dates.append(last_date)

            years = write_station_years(stations_dir, num_poste, days)

            station_catalog.append({
                "num_poste": num_poste,
                "nom": station["nom"],
                "departement": station["departement"],
                "lat": station["lat"],
                "lon": station["lon"],
                "alti": station["alti"],
                "first_date": first_date,
                "last_date": last_date,
                "years": years,
            })

        # Libère la mémoire du département avant de passer au suivant : avec
        # l'historique complet, la garder pour les 95 départements à la fois
        # dépasserait largement la RAM d'un runner GitHub Actions standard.
        del stations
        processed += 1

    if not station_catalog:
        LOGGER.error("Aucune station récupérée, arrêt sans publication.")
        return 1

    overall_last_date = max(last_dates) if last_dates else None
    overall_first_date = min(first_dates) if first_dates else None

    if already_published(args.current_metadata_url, overall_last_date, len(station_catalog)):
        LOGGER.info("Aucune nouvelle journée depuis la dernière publication, on s'arrête ici.")
        return 0

    write_json(output_dir / "departements.json", DEPARTMENTS)
    write_json_gz(
        output_dir / "stations.json.gz",
        {
            "generated_at": iso_utc(),
            "pipeline_version": PIPELINE_VERSION,
            "stations": station_catalog,
        },
    )
    write_json(
        output_dir / "index.json",
        {
            "status": "ok",
            "generated_at": iso_utc(),
            "pipeline_version": PIPELINE_VERSION,
            "source": {
                "provider": "Météo-France",
                "dataset": "Données climatologiques de base - quotidiennes",
                "license": "Licence Ouverte / Etalab 2.0",
                "dataset_url": DATASET_PAGE,
            },
            "coverage": {
                "departments": processed,
                "stations": len(station_catalog),
            },
            "period": {
                "first_date": overall_first_date,
                "last_date": overall_last_date,
            },
            "stats_definitions": [
                {"key": key, "label": label, "field": field_name, "op": op, "threshold": threshold}
                for key, label, field_name, op, threshold in STAT_THRESHOLDS
            ],
        },
    )

    LOGGER.info(
        "Publication prête : %s stations sur %s départements (%s → %s).",
        len(station_catalog), processed, overall_first_date, overall_last_date,
    )
    return 0


def compact_days(days: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [days[key] for key in sorted(days.keys())]


if __name__ == "__main__":
    sys.exit(main())
