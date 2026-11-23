import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cc_transcripts import config as config_mod  # noqa: E402
from cc_transcripts import hook, render, stitch  # noqa: E402
from cc_transcripts.loki import LokiError, build_body  # noqa: E402
from cc_transcripts.redact import MARK, redact  # noqa: E402
from cc_transcripts.sync import Syncer, first_lines_links, surface_from_env  # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"
NOW = time.time()


def iso(offset_s=0):
    return datetime.fromtimestamp(NOW + offset_s, timezone.utc).isoformat().replace("+00:00", "Z")


def fixture_lines():
    return [
        {"type": "queue-operation", "sessionId": SID, "parentSessionId": "cse_01ABCdef"},
        {"type": "user", "uuid": "u1", "timestamp": iso(-30), "cwd": "/home/me/repos/lentago/betula",
         "message": {"role": "user", "content": "LEAK-user-prompt please deploy"}},
        {"type": "assistant", "uuid": "a1", "timestamp": iso(-20), "message": {"content": [
            {"type": "thinking", "thinking": "LEAK-thinking"},
            {"type": "text", "text": "Checking the config. token: glc_abcdefghijklmnopqrstuvwxyz0123"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat LEAK-tool-input"}},
        ]}},
        {"type": "user", "uuid": "u2", "timestamp": iso(-10), "message": {"content": [
            {"type": "tool_result", "content": "LEAK-tool-result GRAFANA_CLOUD_LOGS_TOKEN=glc_zzzzzzzzzzzzzzzzzzzzzzzz"}]}},
        {"type": "assistant", "uuid": "a2", "timestamp": iso(-5), "message": {"content": [
            {"type": "thinking", "thinking": "LEAK-only-thinking"}]}},
        {"type": "assistant", "uuid": "a3", "timestamp": iso(-1), "message": {"content": [
            {"type": "text", "text": "Done."}]}},
    ]


class FakePusher:
    def __init__(self, fail=False):
        self.fail = fail
        self.events = []

    def push(self, events):
        if self.fail:
            raise LokiError(503, "unavailable")
        self.events.extend(events)
        return "ok"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.projects = os.path.join(root, "claude", "projects")
        self.archive = os.path.join(root, "nas")
        os.makedirs(os.path.join(self.projects, "-home-me-repos"))
        os.makedirs(self.archive)
        self.path = os.path.join(self.projects, "-home-me-repos", SID + ".jsonl")
        with open(self.path, "w") as fh:
            for obj in fixture_lines():
                fh.write(json.dumps(obj) + "\n")
        self.cfg = config_mod.Config(
            host="hostA", state_dir=os.path.join(root, "state"), projects_dir=self.projects,
            archive_dir=self.archive, loki_url="https://logs.example", loki_user="1", loki_token="t",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def archived(self, host="hostA"):
        with open(os.path.join(self.archive, host, "-home-me-repos", SID + ".jsonl")) as fh:
            return fh.read().splitlines()


class TestRedact(unittest.TestCase):
    def test_tokens_redacted_and_json_survives(self):
        line = json.dumps({"a": "key glc_abcdefghijklmnopqrstuvwxyz0123 and ghp_" + "x" * 36,
                           "b": "Authorization: Bearer abc.def.ghijkl", "c": "AKIAABCDEFGHIJKLMNOP",
                           "d": "https://user:hunter22@host/x", "e": "password=\"s3cretvalue\""})
        out, n = redact(line)
        obj = json.loads(out)
        self.assertNotIn("glc_abc", out)
        self.assertNotIn("ghp_xxx", out)
        self.assertNotIn("AKIAABC", out)
        self.assertNotIn("hunter22", out)
        self.assertNotIn("s3cretvalue", out)
        self.assertIn("Bearer " + MARK, obj["b"])
        self.assertGreaterEqual(n, 5)

    def test_private_key_block(self):
        line = json.dumps({"x": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"})
        out, _ = redact(line)
        self.assertEqual(json.loads(out)["x"], MARK)

    def test_plain_prose_untouched(self):
        line = json.dumps({"t": "We rotate the logs nightly."})
        self.assertEqual(redact(line), (line, 0))


class TestRender(unittest.TestCase):
    def test_posture(self):
        lines = [render.narration(json.dumps(o)) for o in fixture_lines()]
        shipped = [l for l in lines if l]
        self.assertEqual(len(shipped), 2)
        text = " ".join(l[1] for l in shipped)
        self.assertNotIn("LEAK", text)
        self.assertNotIn("glc_", text)
        self.assertIn("[TOOL] Bash", text)
        self.assertIn("[TEXT] Done.", text)


class TestSync(Base):
    def test_ships_and_archives_once(self):
        pusher = FakePusher()
        s = Syncer(self.cfg, pusher=pusher)
        s.sync_session(self.path, {"session_id": SID, "surface": "cli"})
        self.assertEqual(len(pusher.events), 2)
        labels = pusher.events[0][0]
        self.assertEqual(labels["job"], "claude_transcript")
        self.assertEqual(labels["project"], "betula")
        self.assertEqual(labels["worker"], "hostA")
        body = json.dumps(build_body(pusher.events))
        self.assertNotIn("LEAK", body)
        arch = self.archived()
        self.assertEqual(len(arch), 6)
        self.assertNotIn("glc_abc", "\n".join(arch))
        for l in arch:
            json.loads(l)
        # A second sync with nothing new ships nothing.
        s.sync_session(self.path, {"session_id": SID})
        self.assertEqual(len(pusher.events), 2)
        self.assertEqual(len(self.archived()), 6)
        # Appended line ships on the next sync.
        with open(self.path, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "uuid": "a4", "timestamp": iso(),
                                 "message": {"content": [{"type": "text", "text": "More."}]}}) + "\n")
            fh.write('{"type": "assistant", "partial')  # incomplete line: must wait
        s.sync_session(self.path, {"session_id": SID})
        self.assertEqual(len(pusher.events), 3)
        self.assertEqual(len(self.archived()), 7)

    def test_loki_failure_keeps_cursor(self):
        s = Syncer(self.cfg, pusher=FakePusher(fail=True))
        res = s.sync_session(self.path, {"session_id": SID})
        self.assertTrue(res[0]["errors"])
        ok = FakePusher()
        Syncer(self.cfg, pusher=ok).sync_session(self.path, {"session_id": SID})
        self.assertEqual(len(ok.events), 2)
        self.assertEqual(len(self.archived()), 6)  # archive already done, not repeated

    def test_unmounted_archive_waits(self):
        self.cfg.archive_dir = os.path.join(self.tmp.name, "not-mounted")
        res = Syncer(self.cfg, pusher=FakePusher()).sync_session(self.path, {"session_id": SID})
        self.assertFalse(os.path.exists(self.cfg.archive_dir))
        self.assertTrue(any("archive" in e for e in res[0]["errors"]))

    def test_excluded_cwd_ships_nothing(self):
        self.cfg.exclude = ["/home/me/repos/lentago"]
        pusher = FakePusher()
        Syncer(self.cfg, pusher=pusher).sync_session(self.path, {"session_id": SID})
        self.assertEqual(pusher.events, [])
        self.assertFalse(os.path.exists(os.path.join(self.archive, "hostA")))

    def test_dry_run_changes_nothing(self):
        out = io.StringIO()
        Syncer(self.cfg, dry_run=True, out=out).sync_session(self.path, {"session_id": SID})
        self.assertIn("archive: 6 lines", out.getvalue())
        self.assertFalse(os.path.exists(self.cfg.state_dir))
        self.assertFalse(os.path.exists(os.path.join(self.archive, "hostA")))

    def test_seed_skips_history(self):
        s = Syncer(self.cfg, pusher=FakePusher())
        s.seed(self.path)
        s.sync_session(self.path, {"session_id": SID})
        self.assertEqual(s.pusher.events, [])

    def test_old_lines_not_sent_to_loki(self):
        self.cfg.loki_max_age_s = 15
        pusher = FakePusher()
        Syncer(self.cfg, pusher=pusher).sync_session(self.path, {"session_id": SID})
        self.assertEqual(len(pusher.events), 1)  # only the line 1s old
        self.assertEqual(len(self.archived()), 6)  # the archive keeps everything


class TestStitch(Base):
    def test_groups_across_hosts_and_dedupes(self):
        Syncer(self.cfg, pusher=FakePusher()).sync_session(
            self.path, {"session_id": SID, "surface": "cloud", "remote_session_id": "cse_01ABCdef"})
        # Host B ran the same thread under Remote Control: a different local id,
        # linked by the bridge id; it repeats line a3 (at-least-once).
        sid_b = "99999999-2222-3333-4444-555555555555"
        path_b = os.path.join(self.projects, "-home-me-repos", sid_b + ".jsonl")
        with open(path_b, "w") as fh:
            fh.write(json.dumps({"type": "assistant", "uuid": "a3", "timestamp": iso(-1),
                                 "message": {"content": [{"type": "text", "text": "Done."}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "uuid": "b1", "timestamp": iso(),
                                 "message": {"content": [{"type": "text", "text": "Back on B."}]}}) + "\n")
        self.cfg.host = "hostB"
        Syncer(self.cfg, pusher=FakePusher()).sync_session(
            path_b, {"session_id": sid_b, "surface": "remote-control", "bridge_session_id": "cse_01ABCdef"})
        members = stitch.group(stitch.load_index(self.archive), SID)
        self.assertEqual({m["host"] for m in members}, {"hostA", "hostB"})
        merged = stitch.merge(members)
        uuids = [o.get("uuid") for o in merged if o.get("uuid")]
        self.assertEqual(len(uuids), len(set(uuids)))
        self.assertEqual(merged[-1]["uuid"], "b1")
        self.assertEqual(merged[-1]["_betula"]["host"], "hostB")

    def test_first_line_links(self):
        self.assertIn("cse_01ABCdef", first_lines_links(self.path))


class TestHook(Base):
    def test_fail_open_on_garbage(self):
        self.assertEqual(hook.run(stdin=io.StringIO("not json"), environ={}, spawn=False), 0)
        self.assertEqual(hook.run(stdin=io.StringIO("{}"), environ={}, spawn=False), 0)

    def test_records_pending(self):
        env = {"BETULA_CC_STATE_DIR": self.cfg.state_dir, "BETULA_CC_ARCHIVE_DIR": self.archive,
               "CLAUDE_CODE_BRIDGE_SESSION_ID": "cse_bridge1", "BETULA_CC_ENV": "/nonexistent"}
        payload = {"session_id": SID, "transcript_path": self.path, "cwd": "/x", "hook_event_name": "Stop"}
        hook.run(stdin=io.StringIO(json.dumps(payload)), environ=env, spawn=False)
        cfg = config_mod.load(env, env_files=())
        with open(hook.pending_path(cfg, SID)) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["surface"], "remote-control")
        self.assertEqual(rec["bridge_session_id"], "cse_bridge1")

    def test_surface(self):
        self.assertEqual(surface_from_env({"CLAUDE_CODE_REMOTE": "true"}), "cloud")
        self.assertEqual(surface_from_env({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}), "headless")
        self.assertEqual(surface_from_env({"CLAUDE_CODE_ENTRYPOINT": "claude-desktop"}), "desktop")
        self.assertEqual(surface_from_env({}), "cli")


if __name__ == "__main__":
    unittest.main()
