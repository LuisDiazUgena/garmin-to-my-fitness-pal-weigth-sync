import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import weight_sync as w  # noqa: E402

# Made-up values
GARMIN_PAYLOAD = {"dailyWeightSummaries": [
    {"summaryDate": "2026-01-02", "allWeightMetrics": [
        {"weight": 80120.0, "timestampGMT": 1, "sourceType": "INDEX_SCALE"},
        {"weight": 79880.0, "timestampGMT": 2, "sourceType": "MANUAL"},       # latest of the day
    ]},
    {"summaryDate": "2026-01-03", "allWeightMetrics": [
        {"weight": 81000.0, "timestampGMT": 3, "sourceType": "MFP"},          # sent by MyFitnessPal
    ]},
]}


def entry(day, value, unit="kilograms", id_="1"):
    return {"id": id_, "type": "Weight", "date": day, "value": value, "unit": unit}


class GarminTest(unittest.TestCase):
    def test_latest_weigh_in_of_each_day_without_the_ones_from_myfitnesspal(self):
        self.assertEqual(w.daily_weights(GARMIN_PAYLOAD), {"2026-01-02": 79.9})


class PlanTest(unittest.TestCase):
    def test_missing_different_and_duplicated_days(self):
        garmin = {"2026-01-01": 80.0, "2026-01-02": 79.9, "2026-01-03": 79.5, "2026-01-04": 79.0}
        mfp = {
            "2026-01-01": [entry("2026-01-01", 80.0)],                                   # same: nothing
            "2026-01-02": [entry("2026-01-02", 80.5)],                                   # different
            "2026-01-04": [entry("2026-01-04", 79.0, id_="a"), entry("2026-01-04", 79.0, id_="b")],  # twice
        }
        changes = w.plan(garmin, mfp)
        self.assertEqual([(d, kg, [e["id"] for e in old]) for d, kg, old in changes],
                         [("2026-01-02", 79.9, ["1"]), ("2026-01-03", 79.5, []), ("2026-01-04", 79.0, ["a", "b"])])

    def test_pounds(self):
        self.assertEqual(w.to_kg(entry("2026-01-01", 176.4, "pounds")), 80.0)
        self.assertEqual(w.plan({"2026-01-01": 80.0}, {"2026-01-01": [entry("2026-01-01", 176.4, "pounds")]}), [])


class SessionTest(unittest.TestCase):
    def test_renewed_cookie_kept_until_another_one_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "session.json"
            self.assertEqual(w.current_cookie("first", f), "first")
            w.save_cookie("first", "renewed", f)
            self.assertEqual(w.current_cookie("first", f), "renewed")
            self.assertEqual(w.current_cookie("pasted again", f), "pasted again")
            self.assertEqual(json.loads(f.read_text())["cookie"], "renewed")

    def test_cookie_header_or_bare_token(self):
        self.assertEqual(w.parse_cookie("abc"), {w.MFP_SESSION_COOKIE: "abc"})
        self.assertEqual(w.parse_cookie("Cookie: a=1; b=2"), {"a": "1", "b": "2"})


if __name__ == "__main__":
    unittest.main()
