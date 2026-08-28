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

Chaque station est aussi marquée « active » ou non (dernier relevé récent ou
non), pour que le site masque par défaut les stations fermées. Quand une
« fiche climatologique » Météo-France existe pour la station (jeu de données
séparé « fiches-climatologiques », normales 1991-2020 et records), elle est
récupérée et republiée en « stations/<num_poste>/normales.json » — toutes
les stations n'en ont pas (seules les stations de référence en publient une).
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

LOGGER = logging.getLogger("climato.france")
PIPELINE_VERSION = "1.3.0"
USER_AGENT = "alertes-meteo.com/climato-meteofrance-france/1.3.0"

DATASET_API_URL = (
    "https://www.data.gouv.fr/api/1/datasets/6569b51ae64326786e4e8e1a/"
)
DATASET_PAGE = (
    "https://www.data.gouv.fr/datasets/donnees-climatologiques-de-base-quotidiennes"
)

FICHES_DATASET_API_URL = (
    "https://www.data.gouv.fr/api/1/datasets/684c2d56f3861808c0a5d465/"
)
FICHES_DATASET_PAGE = "https://www.data.gouv.fr/datasets/fiches-climatologiques"
FICHE_RESOURCE_RE = re.compile(r"^Fiche_station_(?P<num_poste>\d{8})\.data$")

# Nombre de jours sans nouveau relevé au-delà duquel une station est
# considérée fermée (masquée par défaut côté site).
ACTIVE_THRESHOLD_DAYS = 730

# Sections reconnues dans une fiche climatologique (titre en minuscules,
# recherché en sous-chaîne — les fiches n'ont pas toutes les mêmes sections,
# certaines stations ne mesurent ni l'insolation ni le rayonnement).
FICHE_SECTION_MARKERS = (
    ("tx_moy", "température maximale (moyenne"),
    ("tm_moy", "température moyenne (moyenne"),
    ("tn_moy", "température minimale (moyenne"),
    ("tx_record", "la température la plus élevée"),
    ("tn_record", "la température la plus basse"),
    ("rr_record", "hauteur quotidienne maximale"),
    ("rr_moy", "hauteur moyenne mensuelle"),
    ("insol_moy", "durée d'insolation"),
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


def build_fiches_index(dataset: dict) -> dict[str, str]:
    """Associe à chaque poste (s'il en publie une) l'URL de sa fiche
    climatologique « .data » (normales 1991-2020 et records)."""
    index: dict[str, str] = {}
    for resource in dataset.get("resources", []):
        title = (resource.get("title") or "").strip()
        match = FICHE_RESOURCE_RE.match(title)
        url = resource.get("url")
        if not match or not url:
            continue
        index[match.group("num_poste")] = url
    return index


def parse_fiche_climatologique(text: str) -> dict[str, Any] | None:
    """Extrait les normales/records mensuels d'une fiche climatologique brute.

    Le fichier n'est pas un CSV propre : c'est un rapport texte, blocs séparés
    par des lignes vides, chaque bloc commençant par un titre de section, une
    éventuelle ligne de note entre parenthèses, une ligne de valeurs (12 mois
    + année), et pour les records une ligne « Date » supplémentaire.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)

    values: dict[str, list[float | None]] = {}
    dates: dict[str, list[str | None]] = {}

    for block in blocks:
        title_line = block[0].strip().rstrip(";").lower()
        matched_key = next(
            (key for key, marker in FICHE_SECTION_MARKERS if marker in title_line),
            None,
        )
        if not matched_key:
            continue

        value_line: str | None = None
        date_line: str | None = None
        for line in block[1:]:
            stripped = line.strip()
            if stripped.startswith("Date"):
                date_line = line
            elif not stripped.startswith("(") and ";" in line:
                value_line = line

        if value_line:
            cells = [c.strip() for c in value_line.split(";")]
            values[matched_key] = [to_float(c) for c in cells[1:13]]
        if date_line:
            cells = [c.strip() for c in date_line.split(";")]
            dates[matched_key] = [c or None for c in cells[1:13]]

    if not values:
        return None
    return {"values": values, "dates": dates}


def build_normales_payload(num_poste: str, parsed: dict[str, Any]) -> dict[str, Any]:
    values = parsed["values"]
    dates = parsed["dates"]

    def at(key: str, index: int) -> Any:
        series = values.get(key)
        return series[index] if series and index < len(series) else None

    def date_at(key: str, index: int) -> Any:
        series = dates.get(key)
        return series[index] if series and index < len(series) else None

    months = []
    for i in range(12):
        months.append({
            "mois": i + 1,
            "tx_moy": at("tx_moy", i),
            "tm_moy": at("tm_moy", i),
            "tn_moy": at("tn_moy", i),
            "tx_record": at("tx_record", i),
            "tx_record_date": date_at("tx_record", i),
            "tn_record": at("tn_record", i),
            "tn_record_date": date_at("tn_record", i),
            "rr_moy": at("rr_moy", i),
            "rr_record": at("rr_record", i),
            "rr_record_date": date_at("rr_record", i),
            "insol_moy": at("insol_moy", i),
        })

    return {
        "num_poste": num_poste,
        "periode_normales": "1991-2020",
        "months": months,
    }


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


def fetch_and_write_normales(
    session: requests.Session, stations_dir: Path, num_poste: str, fiches_index: dict[str, str]
) -> bool:
    """Récupère et republie la fiche climatologique (normales 1991-2020 et
    records) d'une station si Météo-France en publie une. Retourne True si un
    fichier normales.json a été écrit."""
    url = fiches_index.get(num_poste)
    if not url:
        return False
    try:
        response = fetch_with_retries(session, url)
        text = response.content.decode("utf-8", errors="replace")
        parsed = parse_fiche_climatologique(text)
    except requests.RequestException as exc:
        LOGGER.warning("Fiche climatologique %s indisponible (%s), ignorée.", num_poste, exc)
        return False
    if parsed is None:
        return False
    write_json(stations_dir / num_poste / "normales.json", build_normales_payload(num_poste, parsed))
    return True


def compute_active_flags(station_catalog: list[dict[str, Any]], overall_last_date: str | None) -> None:
    """Marque chaque station « active » (relevé récent) ou fermée, en place.
    Une station fermée depuis longtemps reste dans le catalogue (pour
    l'historique) mais le site la masque par défaut."""
    if not overall_last_date:
        for entry in station_catalog:
            entry["active"] = True
        return
    reference = date.fromisoformat(overall_last_date)
    threshold = (reference - timedelta(days=ACTIVE_THRESHOLD_DAYS)).isoformat()
    for entry in station_catalog:
        entry["active"] = entry["last_date"] >= threshold


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

    LOGGER.info("Récupération du catalogue des fiches climatologiques (normales/records)…")
    try:
        fiches_dataset = fetch_json(session, FICHES_DATASET_API_URL)
        fiches_index = build_fiches_index(fiches_dataset)
        LOGGER.info("%s fiches climatologiques disponibles.", len(fiches_index))
    except requests.RequestException as exc:
        LOGGER.warning("Fiches climatologiques indisponibles (%s) — normales non publiées cette fois.", exc)
        fiches_index = {}

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
            has_normales = fetch_and_write_normales(session, stations_dir, num_poste, fiches_index)

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
                "has_normales": has_normales,
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
    compute_active_flags(station_catalog, overall_last_date)

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
                "stations_active": sum(1 for s in station_catalog if s["active"]),
                "stations_with_normales": sum(1 for s in station_catalog if s["has_normales"]),
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
        "Publication prête : %s stations (%s actives, %s avec normales) sur %s départements (%s → %s).",
        len(station_catalog),
        sum(1 for s in station_catalog if s["active"]),
        sum(1 for s in station_catalog if s["has_normales"]),
        processed, overall_first_date, overall_last_date,
    )
    return 0


def compact_days(days: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [days[key] for key in sorted(days.keys())]


if __name__ == "__main__":
    sys.exit(main())
