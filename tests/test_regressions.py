import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_json_to_sqlite as migrate
import xgkb_state_sqlite as state
import xgkb_sync_full as sync_full


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.old_state_dir = state.STATE_DIR
        state.close_all()
        state.STATE_DIR = self.tmp_path / "state"

    def tearDown(self):
        state.close_all()
        state.STATE_DIR = self.old_state_dir
        self.tmp.cleanup()

    def test_migration_writes_hash_key_db_readable_by_v21_loader(self):
        proj_root = self.tmp_path / "project"
        proj_root.mkdir()
        old_json = self.tmp_path / "old.json"
        old_json.write_text(json.dumps({
            "remoteRoot": "RemoteRoot",
            "projectId": "project-1",
            "serverTime": 123,
            "files": {
                "note.md": {
                    "fileId": 42,
                    "versionNumber": 3,
                    "contentHash": "sha256:abc",
                    "mtime": 11,
                    "lastSyncAt": 22,
                }
            },
        }), encoding="utf-8")

        ok = migrate.migrate_one(
            old_json,
            server_url="https://example.test/open-api/",
            app_key="app-key",
            proj_root=proj_root,
        )

        self.assertTrue(ok)
        state.close_all()
        loaded = state.load_state(
            "RemoteRoot",
            "https://example.test/open-api/",
            "app-key",
            proj_root,
        )
        self.assertIn("note.md", loaded["files"])
        self.assertEqual(loaded["files"]["note.md"]["fileId"], 42)

    def test_pull_dry_run_delete_does_not_mutate_sqlite_state(self):
        proj_root = self.tmp_path / "project"
        proj_root.mkdir()
        local = proj_root / "gone.md"
        local.write_text("old", encoding="utf-8")
        state_data = state.load_state("RemoteRoot", "server", "app", proj_root)
        state.mark_synced(state_data, "gone.md", 7, 1, local)
        state.close_all()
        state_data = state.load_state("RemoteRoot", "server", "app", proj_root)

        children = [{"id": 10, "name": "other.md", "type": 2}]
        with mock.patch.object(sync_full.api, "resolve_path", return_value={"exists": True, "fileId": 1}), \
             mock.patch.object(sync_full.api, "get_child_files", return_value=children), \
             mock.patch.object(sync_full.api, "get_full_text_content", return_value={"content": "other", "versionNumber": 1}):
            result = sync_full.do_pull(
                "server", "app", {}, proj_root, "project-1", "RemoteRoot",
                state_data, dry_run=True,
            )

        self.assertEqual(result["deleted"], 1)
        state.close_all()
        reloaded = state.load_state("RemoteRoot", "server", "app", proj_root)
        self.assertIn("gone.md", reloaded["files"])
        self.assertTrue(local.exists())

    def test_pull_empty_cloud_set_does_not_mass_delete_tracked_files(self):
        proj_root = self.tmp_path / "project"
        proj_root.mkdir()
        local = proj_root / "keep.md"
        local.write_text("old", encoding="utf-8")
        state_data = state.load_state("RemoteRoot", "server", "app", proj_root)
        state.mark_synced(state_data, "keep.md", 7, 1, local)

        with mock.patch.object(sync_full.api, "resolve_path", return_value={"exists": True, "fileId": 1}), \
             mock.patch.object(sync_full.api, "get_child_files", return_value=[]):
            result = sync_full.do_pull(
                "server", "app", {}, proj_root, "project-1", "RemoteRoot",
                state_data, dry_run=False,
            )

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(local.exists())
        self.assertIn("keep.md", state_data["files"])

    def test_pull_walk_failure_does_not_write_partial_results(self):
        proj_root = self.tmp_path / "project"
        proj_root.mkdir()
        state_data = state.load_state("RemoteRoot", "server", "app", proj_root)

        def get_child_files(_server, _app, parent_id, **_kwargs):
            if parent_id == 1:
                return [
                    {"id": 2, "name": "sub", "type": 1},
                    {"id": 3, "name": "root.md", "type": 2},
                ]
            raise RuntimeError("boom")

        with mock.patch.object(sync_full.api, "resolve_path", return_value={"exists": True, "fileId": 1}), \
             mock.patch.object(sync_full.api, "get_child_files", side_effect=get_child_files), \
             mock.patch.object(sync_full.api, "get_full_text_content") as get_content:
            result = sync_full.do_pull(
                "server", "app", {}, proj_root, "project-1", "RemoteRoot",
                state_data, dry_run=False,
            )

        get_content.assert_not_called()
        self.assertEqual(result["skipped"], 1)
        self.assertFalse((proj_root / "root.md").exists())

    def test_pull_conflict_local_preserves_local_edit(self):
        proj_root = self.tmp_path / "project"
        proj_root.mkdir()
        local = proj_root / "note.md"
        local.write_text("base", encoding="utf-8")
        state_data = state.load_state("RemoteRoot", "server", "app", proj_root)
        state.mark_synced(state_data, "note.md", 8, 1, local)
        local.write_text("local edit", encoding="utf-8")

        children = [{"id": 8, "name": "note.md", "type": 2}]
        with mock.patch.object(sync_full.api, "resolve_path", return_value={"exists": True, "fileId": 1}), \
             mock.patch.object(sync_full.api, "get_child_files", return_value=children), \
             mock.patch.object(sync_full.api, "get_full_text_content", return_value={"content": "cloud edit", "versionNumber": 2}):
            result = sync_full.do_pull(
                "server", "app", {}, proj_root, "project-1", "RemoteRoot",
                state_data, dry_run=False, conflict="local",
            )

        self.assertEqual(result["skipped"], 1)
        self.assertEqual(local.read_text(encoding="utf-8"), "local edit")

    def test_pull_skips_non_text_files(self):
        proj_root = self.tmp_path / "project"
        proj_root.mkdir()
        state_data = state.load_state("RemoteRoot", "server", "app", proj_root)
        children = [{"id": 9, "name": "report.pdf", "type": 2}]

        with mock.patch.object(sync_full.api, "resolve_path", return_value={"exists": True, "fileId": 1}), \
             mock.patch.object(sync_full.api, "get_child_files", return_value=children), \
             mock.patch.object(sync_full.api, "get_full_text_content") as get_content:
            result = sync_full.do_pull(
                "server", "app", {}, proj_root, "project-1", "RemoteRoot",
                state_data, dry_run=False,
            )

        get_content.assert_not_called()
        self.assertEqual(result["skipped"], 1)
        self.assertFalse((proj_root / "report.pdf").exists())


if __name__ == "__main__":
    unittest.main()
