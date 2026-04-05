from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "youtrack" / "scripts" / "youtrack_api.py"


def load_youtrack_api():
    spec = importlib.util.spec_from_file_location("youtrack_api_under_test", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


youtrack_api = load_youtrack_api()


class ConfigPathTests(unittest.TestCase):
    def test_get_config_path_uses_youtrack_directory(self) -> None:
        env = {"HOME": "/tmp/codex-home"}

        path = youtrack_api.get_config_path(env)

        self.assertEqual(path, Path("/tmp/codex-home/.config/youtrack/config.json"))

    def test_save_config_writes_to_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"HOME": tmpdir}

            path = youtrack_api.save_config(
                base_url="https://example.youtrack.cloud/",
                token="perm:token",
                env=env,
            )

            self.assertEqual(path, Path(tmpdir) / ".config" / "youtrack" / "config.json")
            self.assertTrue(path.exists())

    def test_load_saved_config_prefers_new_path_when_both_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_root = Path(tmpdir) / ".config"
            new_path = config_root / "youtrack" / "config.json"
            legacy_path = config_root / "youtrack-rest" / "config.json"
            new_path.parent.mkdir(parents=True)
            legacy_path.parent.mkdir(parents=True)
            new_path.write_text('{"base_url": "https://new.example", "token": "new-token"}')
            legacy_path.write_text('{"base_url": "https://old.example", "token": "old-token"}')

            config = youtrack_api.load_saved_config({"HOME": tmpdir})

            self.assertEqual(
                config,
                {"base_url": "https://new.example", "token": "new-token"},
            )

    def test_load_saved_config_falls_back_to_legacy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_path = Path(tmpdir) / ".config" / "youtrack-rest" / "config.json"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_text('{"base_url": "https://old.example", "token": "old-token"}')

            config = youtrack_api.load_saved_config({"HOME": tmpdir})

            self.assertEqual(
                config,
                {"base_url": "https://old.example", "token": "old-token"},
            )


if __name__ == "__main__":
    unittest.main()
