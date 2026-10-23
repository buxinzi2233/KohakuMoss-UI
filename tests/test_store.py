"""Persisted JSON stores: atomic writes, notebook CRUD, queue state machine."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kohakumoss_ui.contract import state
from kohakumoss_ui.contract.root import TrainerRoot
from kohakumoss_ui.store import jsonfile, notebook, profile, queue


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452") + b"\x00" * 64


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runs = self.base / "runs"
        self.runs.mkdir()
        self.state = self.base / "state"
        self.env = mock.patch.dict(
            os.environ,
            {
                "KOHAKUMOSS_UI_STATE": str(self.state),
                "KOHAKUMOSS_RUNS": str(self.runs),
            },
        )
        self.env.start()
        self.root = TrainerRoot(self.base)

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()


class JsonFileTest(StoreCase):
    def test_write_is_atomic_private_and_leaves_no_temp_file(self):
        target = self.state / "one.json"
        jsonfile.write_json(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(item.name for item in target.parent.iterdir()), ["one.json"])

    def test_broken_json_falls_back_to_the_default_and_keeps_a_backup(self):
        target = self.state / "one.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{broken", encoding="utf-8")
        got = jsonfile.read_json(target, {"ok": True})
        self.assertEqual(got, {"ok": True})
        self.assertTrue((self.state / "one.json.bak").exists())
        self.assertEqual(jsonfile.read_json(self.state / "missing.json", {"ok": 2}), {"ok": 2})

    def test_wrong_shape_counts_as_broken(self):
        target = self.state / "one.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(jsonfile.read_json(target, {"items": []}), {"items": []})


class NotebookTest(StoreCase):
    def test_crud_round_trip(self):
        added = notebook.add(
            {"title": "first idea", "base": "kohaku.safetensors", "overrides": {"STEPS": 500}}
        )
        item = added["items"][0]
        self.assertEqual(item["title"], "first idea")
        self.assertEqual(item["overrides"]["STEPS"], "500")
        self.assertEqual(item["status"], "idea")
        self.assertTrue(item["id"])
        updated = notebook.update(item["id"], {"status": "todo", "priority": 2})
        self.assertEqual(updated["items"][0]["status"], "todo")
        self.assertEqual(updated["items"][0]["priority"], 2)
        with self.assertRaises(ValueError):
            notebook.update(item["id"], {"status": "nonsense"})
        with self.assertRaises(ValueError):
            notebook.add({"note": "no title"})
        with self.assertRaises(ValueError):
            notebook.remove("missing")
        notebook.remove(item["id"])
        self.assertEqual(notebook.read()["items"], [])

    def test_newest_item_comes_first(self):
        notebook.add({"title": "a"})
        notebook.add({"title": "b"})
        self.assertEqual([row["title"] for row in notebook.read()["items"]], ["b", "a"])

    def test_limit_is_enforced(self):
        with mock.patch.object(notebook, "LIMIT", 2):
            notebook.add({"title": "a"})
            notebook.add({"title": "b"})
            with self.assertRaises(ValueError):
                notebook.add({"title": "c"})

    def test_set_status_from_the_queue(self):
        item_id = notebook.add({"title": "queued idea"})["items"][0]["id"]
        notebook.set_status(item_id, "trained", "moss-1")
        entry = notebook.find(item_id)
        self.assertEqual(entry["status"], "trained")
        self.assertEqual(entry["run_name"], "moss-1")
        self.assertIsNone(notebook.set_status("missing", "trained"))


class ProfileTest(StoreCase):
    def test_pins_migrate_favorite_without_mutating_file(self):
        from kohakumoss_ui.store import jsonfile
        target = profile._file()
        legacy = {"displayName": "moss", "favorite": {"run": "old", "note": "keep"}, "publish": {}}
        jsonfile.write_json(target, legacy)
        before = target.read_bytes()
        self.assertEqual(profile.read()["pins"], [{"run": "old", "note": "keep"}])
        self.assertEqual(target.read_bytes(), before)

    def test_pins_roundtrip_and_explicit_empty(self):
        pins = [{"run": "b", "note": "second"}, {"run": "a", "note": "first"}]
        profile.save({"pins": pins})
        profile.save({"displayName": "new"})
        self.assertEqual(profile.read()["pins"], pins)
        self.assertEqual(profile.read()["favorite"], pins[0])
        profile.save({"pins": []})
        self.assertEqual(profile.read()["pins"], [])
        self.assertEqual(profile.read()["favorite"]["run"], "")

    def test_invalid_pins_do_not_overwrite_saved_selection(self):
        pins = [{"run": str(i), "note": ""} for i in range(6)]
        profile.save({"pins": pins})
        for invalid in (pins + [{"run": "extra"}], [{"run": ""}],
                        [{"run": "a"}, {"run": "a"}], {}, ["a"]):
            with self.assertRaises(ValueError):
                profile.save({"pins": invalid})
            self.assertEqual(profile.read()["pins"], pins)

    def test_display_name_and_favorite(self):
        self.assertEqual(profile.read()["displayName"], "")
        saved = profile.save({"displayName": "moss", "favorite": {"run": "demo-done", "note": "great"}})
        self.assertEqual(saved["displayName"], "moss")
        self.assertEqual(saved["favorite"]["run"], "demo-done")
        self.assertEqual(saved["avatarUrl"], "")

    def test_publish_marks_round_trip(self):
        profile.set_publish(
            "kohaku-style",
            {"state": "published", "url": "https://civitai.com/models/1", "manual": True},
        )
        marks = profile.publish()
        self.assertTrue(marks["kohaku-style"]["manual"])
        self.assertEqual(marks["kohaku-style"]["state"], "published")
        with self.assertRaises(ValueError):
            profile.set_publish("broken", {"state": "nope"})

    def test_avatar_upload_and_removal(self):
        saved = profile.save_avatar(PNG, "image/png")
        self.assertTrue(saved["avatarUrl"].startswith("/api/profile/avatar?v="))
        found = profile.avatar_file()
        self.assertIsNotNone(found)
        path, mime = found
        self.assertEqual(mime, "image/png")
        self.assertEqual(path.read_bytes(), PNG)
        with self.assertRaises(ValueError):
            profile.save_avatar(b"not an image", "image/png")
        with self.assertRaises(ValueError):
            profile.save_avatar(PNG, "image/gif")
        profile.remove_avatar()
        self.assertIsNone(profile.avatar_file())
        self.assertEqual(profile.read()["avatarUrl"], "")


class QueueTest(StoreCase):
    def test_add_move_and_remove(self):
        added = queue.add({"title": "first", "config": "configs/user/a.toml"})
        item = added["items"][0]
        self.assertEqual(item["status"], "waiting")
        self.assertEqual(item["name"], "first")
        self.assertEqual(added["version"], 1)
        queue.add({"title": "second", "config": "configs/user/b.toml"})
        rows = queue.read()["items"]
        second = rows[1]["id"]
        moved = queue.move(second, "up")
        self.assertEqual(moved["items"][0]["id"], second)
        steady = queue.move(second, "up")
        self.assertEqual(steady["version"], moved["version"])
        queue.remove(item["id"])
        self.assertEqual([row["id"] for row in queue.read()["items"]], [second])
        with self.assertRaises(ValueError):
            queue.add({"title": "no config"})

    def test_pause_blocks_the_launcher(self):
        queue.add({"title": "job", "config": "configs/user/a.toml"})
        paused = queue.control("pause")
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["pausedBy"], "user")
        calls = []
        view = queue.tick(
            self.root,
            launcher=lambda *args: calls.append(args),
            status_fn=lambda name: None,
            alive_fn=lambda pid: False,
        )
        self.assertEqual(calls, [])
        self.assertEqual(view["items"][0]["status"], "waiting")
        resumed = queue.control("resume")
        self.assertFalse(resumed["paused"])
        self.assertIsNone(resumed["pausedBy"])

    def test_start_then_settle_done(self):
        queue.add({"title": "job", "config": "configs/user/a.toml", "steps": 10})
        launched = []

        def launcher(root, config, overrides, name):
            launched.append((config, name))
            return {"name": name or "job", "pid": 4242, "alive": True}

        started = queue.tick(
            self.root,
            launcher=launcher,
            status_fn=lambda name: None,
            alive_fn=lambda pid: pid == 4242,
        )
        self.assertEqual(launched, [("configs/user/a.toml", "job")])
        item = started["items"][0]
        self.assertEqual(item["status"], "running")
        self.assertEqual(item["pid"], 4242)
        self.assertEqual(started["current"]["id"], item["id"])

        run_dir = self.runs / "job"
        run_dir.mkdir()
        (run_dir / "metrics.jsonl").write_text(
            json.dumps({"step": 10, "time": time.time()}) + "\n", encoding="utf-8"
        )
        settled = queue.tick(
            self.root,
            launcher=lambda *args: None,
            status_fn=lambda name: {"name": name, "pid": 4242, "alive": False},
            alive_fn=lambda pid: False,
        )
        self.assertEqual(settled["items"][0]["status"], "done")
        self.assertIsNone(settled["current"])

    def test_dead_process_before_the_last_step_fails(self):
        queue.add({"title": "job", "config": "configs/user/a.toml", "steps": 10})
        queue.tick(
            self.root,
            launcher=lambda root, config, overrides, name: {"name": name, "pid": 7, "alive": True},
            status_fn=lambda name: None,
            alive_fn=lambda pid: True,
        )
        settled = queue.tick(
            self.root,
            status_fn=lambda name: {"name": name, "pid": 7, "alive": False},
            alive_fn=lambda pid: False,
        )
        item = settled["items"][0]
        self.assertEqual(item["status"], "failed")
        self.assertIn("exited", item["error"])

    def test_a_live_managed_run_blocks_a_second_start(self):
        state.register("elsewhere", 999, "configs/user/x.toml", "cmd", str(self.state / "x.out"))
        queue.add({"title": "job", "config": "configs/user/a.toml"})
        calls = []
        view = queue.tick(
            self.root,
            launcher=lambda *args: calls.append(args),
            status_fn=lambda name: None,
            alive_fn=lambda pid: pid == 999,
        )
        self.assertEqual(calls, [])
        self.assertEqual(view["items"][0]["status"], "waiting")

    def test_existing_run_directory_is_refused(self):
        queue.add({"name": "taken", "title": "taken", "config": "configs/user/a.toml"})
        (self.runs / "taken").mkdir()
        calls = []
        view = queue.tick(
            self.root,
            launcher=lambda *args: calls.append(args),
            status_fn=lambda name: None,
            alive_fn=lambda pid: False,
        )
        self.assertEqual(calls, [])
        item = view["items"][0]
        self.assertEqual(item["status"], "failed")
        self.assertIn("already exists", item["error"])

    def test_cancel_stops_the_active_process(self):
        queue.add({"title": "job", "config": "configs/user/a.toml"})
        queue.tick(
            self.root,
            launcher=lambda root, config, overrides, name: {"name": name, "pid": 31, "alive": True},
            status_fn=lambda name: None,
            alive_fn=lambda pid: pid == 31,
        )
        item_id = queue.read()["items"][0]["id"]
        stopped = []
        view = queue.control("cancel", item_id, stopper=stopped.append)
        self.assertEqual(stopped, ["job"])
        self.assertEqual(view["items"][0]["status"], "canceled")
        requeued = queue.control("retry", item_id)
        self.assertEqual(requeued["items"][0]["status"], "waiting")
        with self.assertRaises(ValueError):
            queue.control("nonsense")

    def test_clear_done_drops_finished_rows(self):
        queue.add({"title": "job", "config": "configs/user/a.toml", "steps": 3})
        queue.tick(
            self.root,
            launcher=lambda root, config, overrides, name: {"name": name, "pid": 5, "alive": True},
            status_fn=lambda name: None,
            alive_fn=lambda pid: True,
        )
        run_dir = self.runs / "job"
        run_dir.mkdir()
        (run_dir / "metrics.jsonl").write_text(
            json.dumps({"step": 3, "time": time.time()}) + "\n", encoding="utf-8"
        )
        queue.tick(self.root, status_fn=lambda name: None, alive_fn=lambda pid: False)
        self.assertEqual(queue.read()["items"][0]["status"], "done")
        cleared = queue.control("clear-done")
        self.assertEqual(cleared["items"], [])

    def test_manual_start_pauses_a_running_queue(self):
        self.assertFalse(queue.pause_for_manual()["paused"])
        queue.add({"title": "job", "config": "configs/user/a.toml"})
        queue.tick(
            self.root,
            launcher=lambda root, config, overrides, name: {"name": name, "pid": 9, "alive": True},
            status_fn=lambda name: None,
            alive_fn=lambda pid: True,
        )
        paused = queue.pause_for_manual()
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["pausedBy"], "manual")

    def test_restart_keeps_a_fresh_starting_item_then_fails_it(self):
        jsonfile.write_json(
            self.state / "queue.json",
            {
                "paused": False,
                "pausedBy": None,
                "version": 3,
                "items": [
                    {
                        "id": "a1",
                        "name": "ghost",
                        "config": "configs/user/a.toml",
                        "status": "starting",
                        "started_at": time.time(),
                        "pid": 0,
                    }
                ],
            },
        )
        calls = []
        view = queue.tick(
            self.root,
            launcher=lambda *args: calls.append(args),
            status_fn=lambda name: None,
            alive_fn=lambda pid: False,
        )
        self.assertEqual(calls, [])
        self.assertEqual(view["items"][0]["status"], "starting")
        later = queue.tick(
            self.root,
            launcher=lambda *args: calls.append(args),
            status_fn=lambda name: None,
            alive_fn=lambda pid: False,
            now=time.time() + 120,
        )
        self.assertEqual(later["items"][0]["status"], "failed")

    def test_queue_is_capped(self):
        with mock.patch.object(queue, "LIMIT", 2):
            queue.add({"title": "a", "config": "configs/user/a.toml"})
            queue.add({"title": "b", "config": "configs/user/b.toml"})
            with self.assertRaises(ValueError):
                queue.add({"title": "c", "config": "configs/user/c.toml"})


if __name__ == "__main__":
    unittest.main()
