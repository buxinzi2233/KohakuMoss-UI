"""Heatmap aggregation from run metrics: dates, streaks, cache, empty root."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from kohakumoss_ui.contract import history
from kohakumoss_ui.contract.root import TrainerRoot


TODAY = datetime.now().date()


def at(days_ago, hour=12, minute=0):
    day = TODAY - timedelta(days=days_ago)
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


def iso(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def write_metrics(run_dir, rows):
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"step": step, "time": moment, "loss": 0.5}) for moment, step in rows]
    (run_dir / "metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


class HistoryTest(unittest.TestCase):
    def test_calendar_year_is_complete_and_old_years_remain_available(self):
        past = TODAY.year - 2
        stamp = datetime(past, 3, 10, 9).timestamp()
        write_metrics(self.runs / "old", [(stamp, 0), (stamp + 60, 10)])
        recent = history.daily_activity(self.root)
        self.assertIn(past, recent["years"])
        self.assertIn(past + 1, recent["years"])
        self.assertEqual(recent["totals"]["steps"], 0)
        calendar = history.daily_activity(self.root, year=past)
        self.assertEqual(calendar["start"], str(past) + "-01-01")
        self.assertEqual(calendar["end"], str(past) + "-12-31")
        self.assertEqual(calendar["totals"]["steps"], 10)
        self.assertEqual(history.daily_activity(self.root, year=2024)["days"][59]["date"], "2024-02-29")
        self.assertEqual(len(history.daily_activity(self.root, year=2024)["days"]), 366)
        for year in (1969, TODAY.year + 1, "invalid"):
            with self.assertRaises(ValueError):
                history.daily_activity(self.root, year=year)

    def test_saved_datasets_are_normalized_and_missing_configs_are_explicit(self):
        run = self.runs / "mixed"
        write_metrics(run, [(at(0, 9), 0), (at(0, 10), 20)])
        config = {"ALGO": "lora", "DIM": 16, "LR": 0.0001,
                  "DATASETS": [{"source": "folder", "path": "data/a"},
                               {"source": "folder", "path": "./data/a"},
                               {"source": "jsonl", "path": "/tmp/train.jsonl"}, "legacy/images", None]}
        (run / "config.json").write_text(json.dumps(config))
        write_metrics(self.runs / "unknown", [(at(0, 9), 0), (at(0, 10), 10)])
        rows = {row["name"]: row for row in history.run_history(self.root)["runs"]}
        self.assertEqual(len(rows["mixed"]["datasets"]), 3)
        self.assertEqual(rows["mixed"]["datasets"][2]["name"], "images")
        self.assertEqual(rows["mixed"]["datasets"][0]["path"], str(self.base / "data/a"))
        self.assertEqual(rows["mixed"]["dim"], 16)
        self.assertEqual(rows["mixed"]["started_at"], at(0, 9))
        self.assertEqual(rows["unknown"]["datasets"], [])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runs = self.base / "runs"
        self.runs.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {
                "KOHAKUMOSS_UI_STATE": str(self.base / "state"),
                "KOHAKUMOSS_RUNS": str(self.runs),
            },
        )
        self.env.start()
        self.root = TrainerRoot(self.base)
        history.cache_clear()

    def tearDown(self):
        history.cache_clear()
        self.env.stop()
        self._tmp.cleanup()

    def test_empty_root_covers_the_whole_window(self):
        payload = history.daily_activity(self.root, days=365)
        self.assertEqual(len(payload["days"]), 365)
        self.assertEqual(payload["days"][-1]["date"], TODAY.isoformat())
        self.assertEqual(payload["totals"], {"days": 0, "steps": 0, "seconds": 0, "runs": 0})
        self.assertEqual(payload["streak"], 0)
        self.assertEqual(payload["longest"], 0)
        self.assertTrue(all(day["steps"] == 0 for day in payload["days"]))
        self.assertIn(TODAY.year, payload["years"])

    def test_two_days_aggregate_steps_and_seconds(self):
        write_metrics(self.runs / "demo", [(at(0, 9), 0), (at(0, 9, 10), 50)])
        write_metrics(self.runs / "other", [(at(1, 9), 0), (at(1, 9, 30), 30)])
        payload = history.daily_activity(self.root, days=365)
        by_date = {day["date"]: day for day in payload["days"]}
        self.assertEqual(by_date[iso(0)]["steps"], 50)
        self.assertEqual(by_date[iso(0)]["seconds"], 600)
        self.assertEqual(by_date[iso(0)]["runs"], 1)
        self.assertEqual(by_date[iso(1)]["steps"], 30)
        self.assertEqual(by_date[iso(1)]["seconds"], 1800)
        self.assertEqual(payload["totals"]["days"], 2)
        self.assertEqual(payload["totals"]["steps"], 80)
        self.assertEqual(payload["totals"]["seconds"], 2400)
        self.assertEqual(payload["totals"]["runs"], 2)
        self.assertEqual(payload["longest"], 2)
        self.assertEqual(payload["streak"], 2)
        self.assertIn(TODAY.year, payload["years"])

    def test_negative_step_delta_and_gap_are_clamped(self):
        write_metrics(self.runs / "demo", [(at(3, 8), 900), (at(3, 20), 400)])
        payload = history.daily_activity(self.root, days=10)
        day = [row for row in payload["days"] if row["date"] == iso(3)][0]
        self.assertEqual(day["steps"], 0)
        self.assertEqual(day["seconds"], history.GAP_CAP)

    def test_streak_ignores_an_idle_day(self):
        write_metrics(
            self.runs / "demo",
            [(at(0, 9), 0), (at(0, 9, 5), 10), (at(1, 9), 0), (at(1, 9, 5), 10)],
        )
        payload = history.daily_activity(self.root, days=30)
        self.assertEqual(payload["streak"], 2)
        self.assertEqual(payload["longest"], 2)

    def test_second_call_is_cached_and_new_samples_are_seen(self):
        run_dir = self.runs / "demo"
        write_metrics(run_dir, [(at(0, 9), 0), (at(0, 9, 10), 50)])
        first = history.daily_activity(self.root, days=30)
        self.assertTrue(history._CACHE)
        second = history.daily_activity(self.root, days=30)
        self.assertEqual(first["totals"], second["totals"])
        with open(run_dir / "metrics.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"step": 120, "time": at(0, 9, 20)}) + "\n")
        third = history.daily_activity(self.root, days=30)
        self.assertEqual(third["totals"]["steps"], 120)

    def test_sub_second_steps_still_add_up(self):
        rows = [(at(0, 9) + index * 0.4, index) for index in range(6)]
        write_metrics(self.runs / "demo", rows)
        payload = history.daily_activity(self.root, days=10)
        day = [row for row in payload["days"] if row["date"] == iso(0)][0]
        self.assertEqual(day["steps"], 5)
        self.assertEqual(day["seconds"], 2)
        self.assertEqual(payload["totals"]["seconds"], 2)

    def test_run_history_lists_runs_with_progress(self):
        write_metrics(self.runs / "demo", [(at(0, 9), 0), (at(0, 9, 10), 50)])
        payload = history.run_history(self.root)
        self.assertIn("server_time", payload)
        row = [item for item in payload["runs"] if item["name"] == "demo"][0]
        self.assertEqual(row["steps"], 50)
        self.assertGreater(row["started_at"], 0)
        self.assertIn("status", row)


if __name__ == "__main__":
    unittest.main()
