"""Prévision des stations de l'indicateur thermique avec nos propres modèles.

Aucun service extérieur : les fichiers par département des dépôts alertesmeteo-hub/cep (ECMWF IFS 0,25°, déterministe,
toutes les 3 h puis toutes les 6 h) et alertesmeteo-hub/AIGEFS-25-km (ensemble de 31 membres : médiane, p10 et p90, toutes les 6 h)
sont lus sur raw.githubusercontent.com (branche data, departements/<département>.json).

Pour chaque station on retient le point de grille qui minimise « distance + écart d'altitude / 100 » (au plus 15 km, sinon le plus
proche), et la température est corrigée de l'écart d'altitude entre ce point et la station (6,5 °C par km).

Tn et Tx sont calculées comme dans les relevés de Météo-France : Tn = minimum de 18 h UTC la veille à 18 h UTC, Tx = maximum de 6 h UTC
à 6 h UTC le lendemain. Les modèles ne donnent que le présent et l'avenir : pour corriger le biais de chaque station sur ses derniers
jours, on garde d'un calcul à l'autre les échantillons de température des premières heures de chaque prévision (archive-modele.json).
"""
from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

RAW = "https://raw.githubusercontent.com/alertesmeteo-hub/"
DETERMINISTE = "cep"  # ECMWF IFS 0,25°
ENSEMBLE = "AIGEFS-25-km"  # ensemble de 31 membres : médiane, p10 et p90
UA = "Mozilla/5.0 (compatible; AlertesMeteo-IndicateurThermique/2.0)"
GRADIENT = 0.0065  # °C par mètre
RAYON_KM = 15.0
RAYON_MAX_KM = 40.0
PAS_MAX_H = 6  # écart maximal entre deux échantillons d'une fenêtre pour la juger couverte
JOURS_ARCHIVE = 12  # jours gardés dans l'archive
H = 3600.0


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = math.pi / 180
    a = math.sin((lat2 - lat1) * r / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def heure(jour: date, h: int = 0) -> int:
    """Heure UTC (depuis l'epoch) d'un jour à h heures."""
    return int(datetime(jour.year, jour.month, jour.day, tzinfo=timezone.utc).timestamp() // H) + h


def departements_candidats(dep: str | None) -> list[str]:
    """Fichier(s) département à essayer : la Corse (20) est coupée en 2A et 2B dans nos fichiers."""
    if not dep:
        return []
    return ["2A", "2B"] if dep == "20" else [dep]


# ———— Lecture des fichiers ————


def _telecharger(url: str, tentatives: int = 3) -> dict | None:
    for essai in range(tentatives):
        try:
            r = requests.get(url, timeout=(15, 90), headers={"User-Agent": UA})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if essai + 1 == tentatives:
                print(f"  {url.split('/')[-4]}/{url.split('/')[-1]} indisponible : {exc}", flush=True)
                return None
            time.sleep(5 * 3**essai)
    return None


class Fichiers:
    """Fichiers département des modèles, lus une seule fois (sur GitHub, ou dans `dossier`/<dépôt>/<département>.json pour les tests)."""

    def __init__(self, dossier: str | None = None):
        self.dossier = dossier
        self.cache: dict[tuple[str, str], dict | None] = {}

    def get(self, depot: str, dep: str) -> dict | None:
        cle = (depot, dep)
        if cle not in self.cache:
            if self.dossier:
                f = Path(self.dossier) / depot / f"{dep}.json"
                doc = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
            else:
                doc = _telecharger(f"{RAW}{depot}/data/departements/{dep}.json")
            ok = isinstance(doc, dict) and doc.get("status") in (None, "ok") and isinstance(doc.get("forecast"), list) and len(doc["forecast"]) > 0
            self.cache[cle] = doc if ok else None
        return self.cache[cle]


def _colonne(doc: dict, nom: str) -> int | None:
    try:
        return doc["columns"]["values"].index(nom)
    except (KeyError, ValueError):
        return None


def choisir_point(doc: dict, lat: float, lon: float, alti: float) -> tuple[int, float, float] | None:
    """(indice du point, altitude du modèle, distance en km) : au plus RAYON_KM le meilleur compromis distance + écart d'altitude / 100,
    sinon le plus proche (à moins de RAYON_MAX_KM)."""
    try:
        cp = doc["columns"]["points"]
        i_lat, i_lon = cp.index("latitude"), cp.index("longitude")
        i_alt = cp.index("altitude_m") if "altitude_m" in cp else cp.index("model_altitude_m")
    except (KeyError, ValueError):
        return None
    meilleur = proche = None
    for k, p in enumerate(doc["points"]):
        d = distance_km(lat, lon, float(p[i_lat]), float(p[i_lon]))
        palt = float(p[i_alt]) if isinstance(p[i_alt], (int, float)) else 0.0
        if proche is None or d < proche[0]:
            proche = (d, k, palt)
        if d <= RAYON_KM:
            score = d + abs(alti - palt) / 100.0
            if meilleur is None or score < meilleur[0]:
                meilleur = (score, k, palt, d)
    if meilleur:
        return meilleur[1], meilleur[2], meilleur[3]
    if proche and proche[0] <= RAYON_MAX_KM:
        return proche[1], proche[2], proche[0]
    return None


def echantillons(doc: dict, point: int, alt_modele: float, alti_station: float, serie: str | None = None) -> dict[int, float]:
    """Températures (heure UTC → °C) d'un point de grille, corrigées de l'écart d'altitude avec la station.

    `serie` : None pour la prévision principale du fichier, ou « median », « p10 », « p90 » pour les statistiques d'un ensemble.
    """
    i_t = _colonne(doc, "temperature_c")
    if i_t is None:
        return {}
    pas = doc["forecast"] if serie is None else (doc.get("forecast_statistics") or {}).get(serie) or []
    corr = GRADIENT * (alti_station - alt_modele)
    out: dict[int, float] = {}
    for t, rows in pas:
        try:
            v = rows[point][i_t]
        except (IndexError, TypeError):
            continue
        if isinstance(v, (int, float)) and math.isfinite(v):
            out[int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() // H)] = float(v) - corr
    return out


# ———— Tn et Tx d'un jour ————


def _couvert(heures: list[int], a: int, b: int, debut_tolere: int = PAS_MAX_H) -> bool:
    """La fenêtre [a, b] (heures UTC) est couverte : des échantillons à moins de `debut_tolere` du début et de PAS_MAX_H de la fin, et
    sans trou de plus de PAS_MAX_H."""
    h = sorted(x for x in heures if a <= x <= b)
    if not h or h[0] > a + debut_tolere or h[-1] < b - PAS_MAX_H:
        return False
    return all(y - x <= PAS_MAX_H for x, y in zip(h, h[1:]))


def extremes_jour(ech: dict[int, float], jour: date) -> tuple[float | None, float | None, bool]:
    """(Tn, Tx, grossier) du jour : Tn = minimum de 18 h UTC la veille à 18 h UTC, Tx = maximum de 6 h UTC à 6 h UTC le lendemain.

    `grossier` : un trou de plus de 3 h dans une des fenêtres (échantillons toutes les 6 h) : les extrêmes sont alors un peu atténués,
    voir `correction_pas`.
    """
    h0 = heure(jour)
    tn = tx = None
    grossier = False
    for nom, a, b in (("tn", h0 - 6, h0 + 18), ("tx", h0 + 6, h0 + 30)):
        # Tn : le soir de la veille (début de la fenêtre) compte peu, le minimum est à l'aube ; une prévision qui démarre à 0 h UTC suffit
        if not _couvert(list(ech), a, b, debut_tolere=12 if nom == "tn" else PAS_MAX_H):
            continue
        h = sorted(x for x in ech if a <= x <= b)
        if max(y - x for x, y in zip(h, h[1:])) > 3:
            grossier = True
        vals = [ech[x] for x in h]
        if nom == "tn":
            tn = min(vals)
        else:
            tx = max(vals)
    return tn, tx, grossier


def correction_pas(ech: dict[int, float], jours: list[date]) -> tuple[float, float]:
    """Correction (sur Tn, sur Tx) des extrêmes tirés d'échantillons toutes les 6 h : moyenne, sur les jours où l'on dispose d'échantillons
    toutes les 3 h, de l'écart entre les extrêmes de tous les échantillons et ceux des seuls échantillons de 0, 6, 12 et 18 h UTC."""
    dn: list[float] = []
    dx: list[float] = []
    six = {h: v for h, v in ech.items() if h % 6 == 0}
    for j in jours:
        h0 = heure(j)
        if not all((h0 + k) in ech for k in range(-6, 31, 3)):
            continue
        tn3, tx3, _ = extremes_jour(ech, j)
        tn6, tx6, _ = extremes_jour(six, j)
        if None not in (tn3, tx3, tn6, tx6):
            dn.append(tn3 - tn6)  # type: ignore[operator]
            dx.append(tx3 - tx6)  # type: ignore[operator]
    if len(dn) < 2:
        return 0.0, 0.0
    return sum(dn) / len(dn), sum(dx) / len(dx)


def jours_extremes(ech: dict[int, float], jours: list[date]) -> dict[date, tuple[float | None, float | None]]:
    """Tn et Tx de chaque jour demandé (None quand la fenêtre n'est pas couverte), extrêmes « grossiers » corrigés du pas de 6 h."""
    cn, cx = correction_pas(ech, jours)
    out: dict[date, tuple[float | None, float | None]] = {}
    for j in jours:
        tn, tx, grossier = extremes_jour(ech, j)
        if grossier:
            tn = None if tn is None else tn + cn
            tx = None if tx is None else tx + cx
        out[j] = (tn, tx)
    return out


# ———— Archive des premières heures de chaque prévision ————


def lire_archive(doc: dict | None) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    try:
        for sid, paires in (doc or {}).get("stations", {}).items():
            out[sid] = {int(h): float(v) for h, v in paires}
    except (TypeError, ValueError, AttributeError):
        return {}
    return out


def ecrire_archive(archive: dict[str, dict[int, float]], maintenant: datetime) -> dict:
    return {
        "schema_version": 1,
        "generated_at": maintenant.replace(microsecond=0).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "modele": DETERMINISTE,
        "stations": {sid: [[h, round(v, 2)] for h, v in sorted(a.items())] for sid, a in sorted(archive.items())},
    }


def garder(ech: dict[int, float], aujourdhui: date, maintenant: datetime) -> dict[int, float]:
    """Ce qu'on garde dans l'archive : les JOURS_ARCHIVE derniers jours, et l'avenir seulement jusqu'à 36 h (la prochaine prévision le remplacera)."""
    debut = heure(aujourdhui - timedelta(days=JOURS_ARCHIVE))
    fin = int(maintenant.timestamp() // H) + 36
    return {h: v for h, v in ech.items() if debut <= h <= fin}


# ———— Prévision d'une station ————


def prevision_stations(meta: dict[str, dict], aujourdhui: date, archive_prec: dict[str, dict[int, float]], fichiers: Fichiers, maintenant: datetime | None = None) -> tuple[dict, dict, dict]:
    """(det, ens, archive) pour les stations de `meta` (clés : id ; valeurs : lat, lon, alti, departement).

    det[station][jour] = (Tn, Tx) : jours passés tirés de l'archive, jours à venir de la prévision (jusqu'à J+9), None quand la fenêtre
    n'est pas couverte. ens[station][jour] = {"p10": (Tn, Tx), "p50": …, "p90": …, "n": 31} sur 15 jours. archive = archive à publier.
    """
    maintenant = maintenant or datetime.now(timezone.utc)
    det: dict[str, dict[date, tuple[float | None, float | None]]] = {}
    ens: dict[str, dict[date, dict]] = {}
    archive: dict[str, dict[int, float]] = {}
    jours_det = [aujourdhui + timedelta(days=k) for k in range(-JOURS_ARCHIVE + 2, 10)]
    jours_ens = [aujourdhui + timedelta(days=k) for k in range(0, 15)]
    for sid, m in meta.items():
        lat, lon, alti = m.get("lat"), m.get("lon"), m.get("alti") or 0
        if lat is None or lon is None:
            continue
        choix = None  # (distance, département, fichier, (point, altitude du modèle, distance))
        for dep in departements_candidats(m.get("departement")):
            doc = fichiers.get(DETERMINISTE, dep)
            c = choisir_point(doc, lat, lon, alti) if doc else None
            if c and (choix is None or c[2] < choix[0]):
                choix = (c[2], dep, doc, c)
        if choix is None:
            print(f"{sid} {m.get('nom', '')} : pas de prévision {DETERMINISTE}", flush=True)
            continue
        _, dep, doc, (point, alt_modele, _d) = choix
        ech = echantillons(doc, point, alt_modele, alti)
        if not ech:
            continue
        fusion = dict(archive_prec.get(sid, {}))
        fusion.update(ech)  # les échantillons de la prévision du jour remplacent ceux de même heure
        archive[sid] = garder(fusion, aujourdhui, maintenant)
        det[sid] = jours_extremes(fusion, jours_det)

        edoc = fichiers.get(ENSEMBLE, dep)
        ec = choisir_point(edoc, lat, lon, alti) if edoc else None
        if edoc and ec:
            series = {nom: echantillons(edoc, ec[0], ec[1], alti, nom) for nom in ("median", "p10", "p90")}
            # même correction du pas de 6 h que pour la prévision déterministe : l'ensemble est donné toutes les 6 h
            cn, cx = correction_pas(fusion, jours_det)
            brut: dict[date, dict] = {}
            for j in jours_ens:
                item: dict = {}
                for cle, nom in (("p10", "p10"), ("p50", "median"), ("p90", "p90")):
                    tn, tx, _ = extremes_jour(series[nom], j)
                    if tn is not None and tx is not None:
                        item[cle] = (tn + cn, tx + cx)
                if len(item) == 3:
                    brut[j] = item
            # L'ensemble n'a pas la même représentation du terrain que l'IFS (mer ou terre, altitude) : on le recale sur la prévision
            # déterministe par l'écart moyen de sa médiane sur les jours communs (J à J+9), puis on en garde la dispersion et l'évolution.
            ecarts = [(e["p50"][0] - det[sid][j][0], e["p50"][1] - det[sid][j][1]) for j, e in brut.items() if j in det[sid] and None not in det[sid][j]]
            if len(ecarts) >= 5:
                off_n = sum(x for x, _ in ecarts) / len(ecarts)
                off_x = sum(y for _, y in ecarts) / len(ecarts)
                n_membres = int(edoc.get("members") or 0)
                ens[sid] = {j: {"n": n_membres, **{c: (v[0] - off_n, v[1] - off_x) for c, v in e.items()}} for j, e in brut.items()}
    return det, ens, archive
