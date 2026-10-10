from __future__ import annotations

import math
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import previsions_modeles as pm  # noqa: E402

UTC = timezone.utc
J = date(2026, 10, 10)


def cycle(t_utc: int, moyenne: float = 16.0, amplitude: float = 5.0, pic_utc: float = 14.0) -> float:
    """Température d'un cycle diurne sinusoïdal : maximum à `pic_utc`, minimum 12 h plus tôt ou plus tard."""
    return moyenne + amplitude * math.cos(2 * math.pi * (t_utc % 24 - pic_utc) / 24)


def echantillons_cycle(debut: int, fin: int, pas: int, **kw) -> dict[int, float]:
    return {h: cycle(h, **kw) for h in range(debut, fin + 1, pas)}


def doc_modele(debut: datetime, pas: list[tuple[int, int]], altitudes=(10.0,), lat=42.7, lon=2.9, statistiques=False, decalage=0.0, membres=None) -> dict:
    """Fichier département factice : un point par altitude, pas = [(nombre de pas, écart en h)], température suivant un cycle diurne."""
    instants: list[datetime] = [debut]
    for n, ecart in pas:
        for _ in range(n):
            instants.append(instants[-1] + timedelta(hours=ecart))
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    h_utc = lambda d: int(d.timestamp() // 3600)  # noqa: E731
    doc: dict = {
        "status": "ok",
        "columns": {"points": ["model_index", "latitude", "longitude", "altitude_m"], "values": ["temperature_c"]},
        "points": [[k, lat + 0.01 * k, lon, a] for k, a in enumerate(altitudes)],
        "forecast": [[iso(d), [[cycle(h_utc(d)) + decalage] for _ in altitudes]] for d in instants],
    }
    if statistiques:
        doc["members"] = membres or 31
        doc["forecast_statistics"] = {
            nom: [[iso(d), [[cycle(h_utc(d)) + decalage + delta] for _ in altitudes]] for d in instants]
            for nom, delta in (("median", 0.0), ("p10", -1.5), ("p90", 1.5))
        }
    return doc


class Fixes(pm.Fichiers):
    def __init__(self, docs: dict[tuple[str, str], dict]):
        super().__init__(None)
        self.docs = docs

    def get(self, depot: str, dep: str):  # type: ignore[override]
        return self.docs.get((depot, dep))


class Points(unittest.TestCase):
    def test_bonne_altitude_avant_proximite(self):
        doc = doc_modele(datetime(2026, 10, 10, tzinfo=UTC), [(2, 3)], altitudes=(100.0, 1500.0))
        self.assertEqual(pm.choisir_point(doc, 42.7, 2.9, 1480)[0], 1)  # un peu plus loin mais à la bonne altitude
        self.assertEqual(pm.choisir_point(doc, 42.7, 2.9, 90)[0], 0)

    def test_trop_loin(self):
        doc = doc_modele(datetime(2026, 10, 10, tzinfo=UTC), [(2, 3)])
        self.assertIsNone(pm.choisir_point(doc, 45.5, 2.9, 10))

    def test_correction_d_altitude(self):
        doc = doc_modele(datetime(2026, 10, 10, tzinfo=UTC), [(8, 3)], altitudes=(100.0,))
        base = pm.echantillons(doc, 0, 100.0, 100.0)
        haut = pm.echantillons(doc, 0, 100.0, 1100.0)
        h = next(iter(base))
        self.assertAlmostEqual(base[h] - haut[h], 6.5, places=6)

    def test_departements_candidats(self):
        self.assertEqual(pm.departements_candidats("20"), ["2A", "2B"])
        self.assertEqual(pm.departements_candidats("66"), ["66"])
        self.assertEqual(pm.departements_candidats(None), [])


class Extremes(unittest.TestCase):
    def test_fenetres_de_meteo_france(self):
        # cycle horaire : Tn vers 2 h UTC (minimum du cycle), Tx à 14 h UTC
        ech = echantillons_cycle(pm.heure(J) - 30, pm.heure(J) + 40, 1)
        tn, tx, grossier = pm.extremes_jour(ech, J)
        self.assertAlmostEqual(tn, 11.0, places=1)
        self.assertAlmostEqual(tx, 21.0, places=1)
        self.assertFalse(grossier)

    def test_la_nuit_du_lendemain_n_entre_pas_dans_tn(self):
        # froid exceptionnel à 3 h UTC le lendemain : hors fenêtre de Tn (qui finit à 18 h UTC)
        ech = echantillons_cycle(pm.heure(J) - 30, pm.heure(J) + 40, 1)
        ech[pm.heure(J) + 27] = -20.0
        self.assertAlmostEqual(pm.extremes_jour(ech, J)[0], 11.0, places=1)
        # mais il compte pour Tx ? non plus : un minimum n'est pas un maximum
        self.assertAlmostEqual(pm.extremes_jour(ech, J)[1], 21.0, places=1)

    def test_fenetre_non_couverte(self):
        ech = echantillons_cycle(pm.heure(J) + 3, pm.heure(J) + 9, 1)  # s arrête avant la fin tolérée des deux fenêtres
        tn, tx, _ = pm.extremes_jour(ech, J)
        self.assertIsNone(tx)
        self.assertIsNone(tn)

    def test_prevision_qui_demarre_a_zero_heure_donne_tn_du_jour(self):
        ech = echantillons_cycle(pm.heure(J), pm.heure(J) + 60, 3)
        tn, tx, grossier = pm.extremes_jour(ech, J)
        self.assertAlmostEqual(tn, 11.0, delta=0.2)  # le minimum est à 2 h UTC : échantillons de 0 h et 3 h
        self.assertFalse(grossier)
        self.assertIsNotNone(tx)

    def test_pas_de_6_heures_est_grossier_et_corrige(self):
        # 3 h pendant 6 jours puis 6 h : l'extrême tiré du pas de 6 h est un peu atténué, la correction la rattrape en partie
        fin3 = pm.heure(J) + 6 * 24
        ech = echantillons_cycle(pm.heure(J) - 30, fin3, 3)
        ech.update(echantillons_cycle(fin3 + 6, pm.heure(J) + 14 * 24, 6))
        tard = J + timedelta(days=8)
        tn, tx, grossier = pm.extremes_jour(ech, tard)
        self.assertTrue(grossier)
        corrige = pm.jours_extremes(ech, [tard])[tard]
        vrai = (cycle(pm.heure(tard) + 2), cycle(pm.heure(tard) + 14))
        self.assertLess(abs(corrige[1] - vrai[1]), abs(tx - vrai[1]) + 1e-9)
        self.assertLess(abs(corrige[0] - vrai[0]), abs(tn - vrai[0]) + 1e-9)

    def test_correction_sans_jours_a_3_heures(self):
        self.assertEqual(pm.correction_pas(echantillons_cycle(pm.heure(J), pm.heure(J) + 200, 6), [J]), (0.0, 0.0))


class Archive(unittest.TestCase):
    def test_aller_retour(self):
        arch = {"07156001": {100: 12.3456, 103: 11.0}, "66136001": {100: 20.0}}
        doc = pm.ecrire_archive(arch, datetime(2026, 10, 10, 6, tzinfo=UTC))
        self.assertEqual(pm.lire_archive(doc)["07156001"], {100: 12.35, 103: 11.0})
        self.assertEqual(pm.lire_archive(None), {})
        self.assertEqual(pm.lire_archive({"stations": "x"}), {})

    def test_garder(self):
        maintenant = datetime(2026, 10, 10, 6, tzinfo=UTC)
        h_now = int(maintenant.timestamp() // 3600)
        ech = {h_now - 24 * 20: 1.0, h_now - 24 * 3: 2.0, h_now + 12: 3.0, h_now + 100: 4.0}
        self.assertEqual(sorted(pm.garder(ech, J, maintenant).values()), [2.0, 3.0])


class Stations(unittest.TestCase):
    META = {"66136001": {"nom": "Perpignan", "lat": 42.7, "lon": 2.9, "alti": 43, "departement": "66"}}

    def docs(self, run: datetime, decalage_ens=-3.0):
        # prévision déterministe : 3 h pendant 6 jours puis 6 h jusqu'à 15 jours ; ensemble toutes les 6 h sur 16 jours
        det = doc_modele(run, [(48, 3), (36, 6)], altitudes=(43.0,))
        ens = doc_modele(run, [(64, 6)], altitudes=(43.0,), statistiques=True, decalage=decalage_ens)
        return Fixes({(pm.DETERMINISTE, "66"): det, (pm.ENSEMBLE, "66"): ens})

    def test_premiere_execution_sans_archive(self):
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        det, ens, archive = pm.prevision_stations(self.META, J, {}, self.docs(run), maintenant=run + timedelta(hours=9))
        d = det["66136001"]
        self.assertEqual(d[J - timedelta(days=1)], (None, None))  # hier : pas d'archive, pas de valeur
        tn, tx = d[J + timedelta(days=2)]
        self.assertAlmostEqual(tn, cycle(pm.heure(J) + 2 + 48) - 0, delta=0.3)
        self.assertAlmostEqual(tx, 21.0, delta=0.3)
        self.assertIsNotNone(d[J][0])  # aujourd'hui : la prévision démarre à 0 h UTC, assez pour Tn
        self.assertIn(J + timedelta(days=9), d)
        self.assertNotIn(J + timedelta(days=10), d)
        self.assertTrue(archive["66136001"])

    def test_l_ensemble_est_recale_sur_la_prevision_deterministe(self):
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        det, ens, _ = pm.prevision_stations(self.META, J, {}, self.docs(run, decalage_ens=-3.0), maintenant=run + timedelta(hours=9))
        e = ens["66136001"]
        j = J + timedelta(days=3)
        tn_d, tx_d = det["66136001"][j]
        # la médiane de l'ensemble (décalée de -3 °C dans le fichier) retombe sur la prévision déterministe, p10 et p90 l'encadrent de 1,5 °C
        self.assertAlmostEqual(e[j]["p50"][1], tx_d, delta=0.3)
        self.assertAlmostEqual(e[j]["p10"][1], tx_d - 1.5, delta=0.3)
        self.assertAlmostEqual(e[j]["p90"][1], tx_d + 1.5, delta=0.3)
        self.assertEqual(e[j]["n"], 31)
        self.assertIn(J + timedelta(days=14), e)

    def test_l_archive_fournit_les_jours_passes(self):
        run1 = datetime(2026, 10, 9, 0, tzinfo=UTC)
        run2 = datetime(2026, 10, 10, 0, tzinfo=UTC)
        _, _, archive1 = pm.prevision_stations(self.META, date(2026, 10, 9), {}, self.docs(run1), maintenant=run1 + timedelta(hours=9))
        det, _, archive2 = pm.prevision_stations(self.META, J, archive1, self.docs(run2), maintenant=run2 + timedelta(hours=9))
        hier = det["66136001"][date(2026, 10, 9)]
        self.assertAlmostEqual(hier[0], 11.0, delta=0.3)
        self.assertAlmostEqual(hier[1], 21.0, delta=0.3)
        # les échantillons de la veille (pas de la prévision du jour) restent dans l'archive publiée
        self.assertIn(pm.heure(date(2026, 10, 9), 12), archive2["66136001"])

    def test_prevision_du_jour_remplace_celle_de_l_archive_pour_une_meme_heure(self):
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        h = pm.heure(J, 12)
        det, _, archive = pm.prevision_stations(self.META, J, {"66136001": {h: -40.0}}, self.docs(run), maintenant=run + timedelta(hours=9))
        self.assertGreater(archive["66136001"][h], 0)

    def test_station_sans_fichier(self):
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        det, ens, archive = pm.prevision_stations({"1": {"nom": "x", "lat": 48.0, "lon": 2.0, "alti": 10, "departement": "75"}}, J, {}, self.docs(run), maintenant=run)
        self.assertEqual((det, ens, archive), ({}, {}, {}))

    def test_corse_choisit_le_fichier_le_plus_proche(self):
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        meta = {"20004002": {"nom": "Ajaccio", "lat": 41.9, "lon": 8.8, "alti": 5, "departement": "20"}}
        docs = {
            (pm.DETERMINISTE, "2A"): doc_modele(run, [(48, 3)], lat=41.9, lon=8.8, altitudes=(5.0,)),
            (pm.DETERMINISTE, "2B"): doc_modele(run, [(48, 3)], lat=42.6, lon=9.4, altitudes=(5.0,), decalage=50.0),
        }
        det, _, _ = pm.prevision_stations(meta, J, {}, Fixes(docs), maintenant=run + timedelta(hours=9))
        self.assertLess(det["20004002"][J + timedelta(days=1)][1], 30)  # 2A (cycle normal), pas 2B (+50 °C)


if __name__ == "__main__":
    unittest.main()
