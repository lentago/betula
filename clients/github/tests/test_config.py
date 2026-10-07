"""Config file + secrets validation."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from github_collector.config import Config, ConfigError, load_secrets, parse_duration  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigTest(unittest.TestCase):
    def test_committed_config_is_valid(self):
        cfg = Config.load(os.path.join(HERE, "config.json"), env={})
        self.assertEqual(cfg.owners, [{"owner": "lentago", "cluster": "lentago"}])
        self.assertEqual((cfg.interval_s, cfg.lookback_s), (300, 86400))
        self.assertEqual(cfg.state_dir, "/var/lib/betula-github")

    def test_defaults_and_state_directory_env(self):
        cfg = Config.from_dict({"owners": [{"owner": "lentago", "cluster": "lentago"}]},
                               env={"STATE_DIRECTORY": "/tmp/x"})
        self.assertEqual((cfg.interval_s, cfg.lookback_s, cfg.state_dir), (300, 86400, "/tmp/x"))

    def test_rejects_bad_entries(self):
        bad = [
            {},
            {"owners": []},
            {"owners": [{"owner": "lentago"}]},
            {"owners": [{"owner": "lentago", "cluster": "Lentago"}]},
            {"owners": [{"owner": "bad/owner", "cluster": "x"}]},
            {"owners": [{"owner": "a", "cluster": "a"}, {"owner": "A", "cluster": "b"}]},
            {"owners": [{"owner": "a", "cluster": "a"}], "interval": "5 minutes"},
            {"owners": [{"owner": "a", "cluster": "a"}], "interval": "1h", "lookback": "30m"},
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                Config.from_dict(raw, env={})

    def test_unreadable_config(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
        self.addCleanup(os.unlink, fh.name)
        with self.assertRaises(ConfigError):
            Config.load(fh.name, env={})

    def test_parse_duration(self):
        self.assertEqual(parse_duration("90s", "x"), 90)
        self.assertEqual(parse_duration("2d", "x"), 172800)
        with self.assertRaises(ConfigError):
            parse_duration("0m", "x")


class SecretsTest(unittest.TestCase):
    def test_names_every_missing_secret(self):
        with self.assertRaises(ConfigError) as ctx:
            load_secrets({"BETULA_GITHUB_TOKEN": "x"})
        self.assertIn("GRAFANA_CLOUD_LOGS_TOKEN", str(ctx.exception))
        self.assertNotIn("BETULA_GITHUB_TOKEN", str(ctx.exception))

    def test_returns_all_four(self):
        env = {k: "v" for k in ("BETULA_GITHUB_TOKEN", "GRAFANA_CLOUD_LOGS_URL",
                                "GRAFANA_CLOUD_LOGS_USER", "GRAFANA_CLOUD_LOGS_TOKEN")}
        self.assertEqual(load_secrets(env), env)


if __name__ == "__main__":
    unittest.main()
