from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from update_recent_synop import aggregate_synop, merge_days, resource_url  # noqa: E402


def row(day: str, hour: str, **values: str) -> dict[str, str]:
    payload = {
        "geo_id_wmo": "07156", "lat": "48.8217", "lon": "2.3378",
        "validity_time": f"{day}T{hour}:00:00Z", "tn12": "", "tx12": "", "rr24": "",
    }
    payload.update(values)
    return payload


class SynopAggregationTests(unittest.TestCase):
    def test_paris_montsouris_values(self) -> None:
        rows = [
            row("2026-08-30", "06", tn12="289.25", rr24="5.2"),
            row("2026-08-30", "18", tx12="297.75", rr24="0.2"),
            row("2026-08-30", "21", rr24="0.2"),
            row("2026-08-31", "06", tn12="289.55", rr24="-0.1"),
            row("2026-08-31", "18", tx12="296.95", rr24="0.4"),
            row("2026-08-31", "21", rr24="0.4"),
        ]
        days, _ = aggregate_synop(rows, {"2026-08-30", "2026-08-31"})
        self.assertEqual(days["07156:2026-08-30"]["tx"], 24.6)
        self.assertEqual(days["07156:2026-08-30"]["tn"], 16.1)
        self.assertEqual(days["07156:2026-08-30"]["rr"], 0.0)
        self.assertEqual(days["07156:2026-08-31"]["tx"], 23.8)
        self.assertEqual(days["07156:2026-08-31"]["tn"], 16.4)
        self.assertEqual(days["07156:2026-08-31"]["rr"], 0.4)

    def test_official_day_is_never_replaced(self) -> None:
        official = [{"date": "2026-08-30", "tx": 25.0, "tn": 16.0, "rr": 0.0, "insol_h": 4.2}]
        synop = [{"date": "2026-08-30", "tx": 24.6, "tn": 16.1, "rr": 0.0,
                  "insol_h": None, "source": "synop"}]
        merged, changed = merge_days(official, synop)
        self.assertFalse(changed)
        self.assertEqual(merged, official)

    def test_resolves_current_year_resource(self) -> None:
        dataset = {"resources": [{"title": "synop_2026", "type": "main", "url": "current"}]}
        self.assertEqual(resource_url(dataset, 2026), "current")


if __name__ == "__main__":
    unittest.main()


