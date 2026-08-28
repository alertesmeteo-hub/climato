from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_climato_france import (  # noqa: E402
    DEPARTMENTS,
    STAT_THRESHOLDS,
    build_resource_index,
    clean_station_name,
    compact_days,
    to_float,
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
    def test_picks_most_recent_period_and_ignores_avant(self) -> None:
        dataset = {
            "resources": [
                {"title": "QUOT_departement_28_periode_avant-1949_RR-T-Vent", "url": "old-pre1949"},
                {"title": "QUOT_departement_28_periode_1950-2024_RR-T-Vent", "url": "old-1950-2024"},
                {"title": "QUOT_departement_28_periode_2025-2026_RR-T-Vent", "url": "latest-rr"},
                {"title": "QUOT_departement_28_periode_2025-2026_autres-parametres", "url": "latest-autres"},
                {"title": "some_unrelated_resource", "url": "ignored"},
            ]
        }
        index = build_resource_index(dataset)
        self.assertEqual(index["28"]["RR-T-Vent"], "latest-rr")
        self.assertEqual(index["28"]["autres-parametres"], "latest-autres")

    def test_missing_url_is_skipped(self) -> None:
        dataset = {"resources": [{"title": "QUOT_departement_75_periode_2025-2026_RR-T-Vent"}]}
        index = build_resource_index(dataset)
        self.assertNotIn("75", index)


class DepartmentsAndThresholdsTests(unittest.TestCase):
    def test_covers_95_metropolitan_departments(self) -> None:
        self.assertEqual(len(DEPARTMENTS), 95)
        self.assertEqual(DEPARTMENTS["20"], "Corse")
        self.assertEqual(DEPARTMENTS["75"], "Paris")

    def test_nine_stat_thresholds_defined(self) -> None:
        self.assertEqual(len(STAT_THRESHOLDS), 9)
        keys = [key for key, *_ in STAT_THRESHOLDS]
        self.assertEqual(len(keys), len(set(keys)), "clés de statistiques dupliquées")


if __name__ == "__main__":
    unittest.main()
