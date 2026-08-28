from __future__ import annotations

import gzip
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_climato_france import (  # noqa: E402
    DEPARTMENTS,
    PIPELINE_VERSION,
    STAT_THRESHOLDS,
    build_resource_index,
    clean_station_name,
    compact_days,
    to_float,
    write_station_years,
)


class ToFloatTests(unittest.TestCase):
    def test_parses_plain_decimal(self) -> None:
        self.assertEqual(to_float("28.7"), 28.7)

    def test_blank_is_none(self) -> None:
        self.assertIsNone(to_float(""))
        self.assertIsNone(to_float("   "))
        self.assertIsNone(to_float(None))

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(to_float("Tr"))


class CleanStationNameTests(unittest.TestCase):
    def test_underscore_becomes_space_and_title_case(self) -> None:
        self.assertEqual(clean_station_name("CHAPELLE-GUILLAUME_SAPC"), "Chapelle-Guillaume Sapc")

    def test_plain_name(self) -> None:
        self.assertEqual(clean_station_name("CHATEAUDUN"), "Chateaudun")


class CompactDaysTests(unittest.TestCase):
    def test_sorts_by_date_key(self) -> None:
        days = {
            "2025-01-02": {"date": "2025-01-02"},
            "2025-01-01": {"date": "2025-01-01"},
        }
        result = compact_days(days)
        self.assertEqual([d["date"] for d in result], ["2025-01-01", "2025-01-02"])


class ResourceIndexTests(unittest.TestCase):
    def test_collects_all_periods_oldest_to_newest(self) -> None:
        dataset = {
            "resources": [
                {"title": "QUOT_departement_28_periode_2025-2026_RR-T-Vent", "url": "latest-rr"},
                {"title": "QUOT_departement_28_periode_avant-1949_RR-T-Vent", "url": "pre1949-rr"},
                {"title": "QUOT_departement_28_periode_1950-2024_RR-T-Vent", "url": "hist-rr"},
                {"title": "QUOT_departement_28_periode_2025-2026_autres-parametres", "url": "latest-autres"},
                {"title": "some_unrelated_resource", "url": "ignored"},
            ]
        }
        index = build_resource_index(dataset)
        # Triées de la plus ancienne à la plus récente, quel que soit l'ordre reçu.
        self.assertEqual(index["28"]["RR-T-Vent"], ["pre1949-rr", "hist-rr", "latest-rr"])
        self.assertEqual(index["28"]["autres-parametres"], ["latest-autres"])

    def test_missing_url_is_skipped(self) -> None:
        dataset = {"resources": [{"title": "QUOT_departement_75_periode_2025-2026_RR-T-Vent"}]}
        index = build_resource_index(dataset)
        self.assertNotIn("75", index)


class WriteStationYearsTests(unittest.TestCase):
    def test_splits_days_by_year(self) -> None:
        days = [
            {"date": "2024-12-31", "tx": 5.0, "tn": 1.0, "rr": 0.0, "insol_h": None},
            {"date": "2025-01-01", "tx": 6.0, "tn": 2.0, "rr": 0.0, "insol_h": None},
            {"date": "2025-01-02", "tx": 7.0, "tn": 3.0, "rr": 1.0, "insol_h": None},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            stations_dir = Path(tmp)
            years = write_station_years(stations_dir, "28198001", days)
            self.assertEqual(years, [2024, 2025])

            year_2024 = gzip.decompress((stations_dir / "28198001" / "2024.json.gz").read_bytes()).decode("utf-8")
            year_2025 = gzip.decompress((stations_dir / "28198001" / "2025.json.gz").read_bytes()).decode("utf-8")
            self.assertIn('"date":"2024-12-31"', year_2024)
            self.assertNotIn("2025-01-01", year_2024)
            self.assertIn('"date":"2025-01-01"', year_2025)
            self.assertIn('"date":"2025-01-02"', year_2025)


class DepartmentsAndThresholdsTests(unittest.TestCase):
    def test_covers_95_metropolitan_departments(self) -> None:
        self.assertEqual(len(DEPARTMENTS), 95)
        self.assertEqual(DEPARTMENTS["20"], "Corse")
        self.assertEqual(DEPARTMENTS["75"], "Paris")

    def test_nine_stat_thresholds_defined(self) -> None:
        self.assertEqual(len(STAT_THRESHOLDS), 9)
        keys = [key for key, *_ in STAT_THRESHOLDS]
        self.assertEqual(len(keys), len(set(keys)), "clés de statistiques dupliquées")

    def test_pipeline_version_is_semver_like(self) -> None:
        parts = PIPELINE_VERSION.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))


if __name__ == "__main__":
    unittest.main()
