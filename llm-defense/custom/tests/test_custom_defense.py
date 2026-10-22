import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import custom_defense as custom


def response(request, code):
    guard = {"source_sha256": request["source_sha256"], "region_id": request["region_id"],
             "guard_code": code, "rationale": "Reject resource requests above application quota.",
             "security_condition": "length <= 4096"}
    return {"id": "offline-fixture", "model": "fixture-not-a-model", "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(guard)}]}],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}


@unittest.skipUnless(shutil.which("clang"), "Clang required")
class CustomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "quota.c"
        # Non-ASCII prefix verifies that Clang byte offsets are not treated as
        # Python character offsets. The sibling function must remain untouched.
        self.source.write_text("/* 한글 */\nint sibling(void) { return 7; }\n"
                               "int accept_request(int length) { return length >= 0; }\n")
        self.request = custom.inspect(self.source, "accept_request", "Enforce quota 4096; return 0 if exceeded")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, code="if (length > 4096) return 0;"):
        with mock.patch("urllib.request.build_opener", side_effect=AssertionError("network in replay")):
            return custom.generate(self.request, "fixture", replay=response(self.request, code))

    def test_replay_apply_and_behavior(self):
        before = self.source.read_bytes()
        plan = self.plan()
        target = self.root / "protected.c"
        result = custom.apply(plan, self.source, target)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(result["status"], "syntax_checked")
        at = self.request["region"]["begin"] + 1
        patched = target.read_bytes()
        self.assertEqual(patched[:at], before[:at])
        self.assertEqual(patched[at + result["added_bytes"]:], before[at:])
        # Execute only this authored, trusted fixture, never arbitrary API output.
        harness = self.root / "test.c"
        harness.write_text("int accept_request(int); int sibling(void);\nint main(void) {\n"
                           "return !(accept_request(-1)==0 && accept_request(0)==1 && "
                           "accept_request(4096)==1 && accept_request(4097)==0 && sibling()==7);}\n")
        exe = self.root / "test"
        subprocess.run(["clang", "-O2", str(target), str(harness), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        self.assertEqual(plan["generation"]["mode"], "replay")

    def test_source_mutation_rejected_without_output(self):
        plan = self.plan()
        self.source.write_bytes(self.source.read_bytes() + b"\n")
        with self.assertRaisesRegex(custom.Rejected, "Source changed"):
            custom.apply(plan, self.source, self.root / "output.c")
        self.assertFalse((self.root / "output.c").exists())

    def test_bad_c_rejected_without_output(self):
        with self.assertRaisesRegex(custom.Rejected, "Clang rejected"):
            custom.apply(self.plan("not valid C !"), self.source, self.root / "output.c")
        self.assertFalse((self.root / "output.c").exists())

    def test_scope_escape_rejected(self):
        code = "} int injected(void) { return 9; } int other(int length) {"
        with self.assertRaisesRegex(custom.Rejected, "escaped"):
            custom.apply(self.plan(code), self.source, self.root / "output.c")
        self.assertFalse((self.root / "output.c").exists())

    def test_nested_block_and_local_include(self):
        (self.root / "limit.h").write_text("#define QUOTA 4096\n")
        self.source.write_text('#include "limit.h"\nint accept_request(int length) {\n'
                               'if (length >= 0) { return 1; } return 0; }\n')
        self.request = custom.inspect(self.source, "accept_request", "Apply quota", block_index=1)
        result = custom.apply(self.plan("if (length > QUOTA) return 0;"), self.source, self.root / "output.c")
        self.assertEqual(result["status"], "syntax_checked")

    def test_no_overwrite(self):
        target = self.root / "output.c"
        target.write_text("keep")
        with self.assertRaises(custom.Rejected):
            custom.apply(self.plan(), self.source, target)
        self.assertEqual(target.read_text(), "keep")
        with self.assertRaises(custom.Rejected):
            custom.apply(self.plan(), self.source, self.source)

    def test_stale_region_and_tampered_offsets_rejected(self):
        plan = self.plan()
        plan["request"]["region"]["begin"] = 0
        with self.assertRaisesRegex(custom.Rejected, "Region identity"):
            custom.apply(plan, self.source, self.root / "output.c")

    def test_missing_key_never_sends_request(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("urllib.request.build_opener") as opener:
            with self.assertRaisesRegex(custom.Rejected, "OPENAI_API_KEY"):
                custom.generate(self.request, "explicit-model-id")
            opener.assert_not_called()

    def test_live_transport_contract_without_external_call(self):
        wire_response = mock.MagicMock()
        wire_response.__enter__.return_value.read.return_value = json.dumps(
            response(self.request, "if (length > 4096) return 0;")).encode()
        opener = mock.Mock()
        opener.open.return_value = wire_response
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "fake-unit-test-key"}), \
                mock.patch("urllib.request.build_opener", return_value=opener):
            plan = custom.generate(self.request, "explicit-model-id")
        sent = opener.open.call_args[0][0]
        body = json.loads(sent.data)
        self.assertEqual(sent.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertFalse(body["store"])
        self.assertEqual(body["model"], "explicit-model-id")
        self.assertNotIn("fake-unit-test-key", json.dumps(plan))

    def test_refusal_incomplete_and_wrong_target(self):
        for status in ("incomplete", "failed"):
            with self.assertRaises(custom.Rejected):
                custom.parse_response({"status": status})
        with self.assertRaisesRegex(custom.Rejected, "refused"):
            custom.parse_response({"status": "completed", "output": [
                {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]})
        guard = copy.deepcopy(self.plan()["guard"])
        guard["region_id"] = "other"
        with self.assertRaises(custom.Rejected):
            custom.validate_guard(self.request, guard)

    def test_directive_and_abstention_rejected(self):
        for code in ("#include <stdio.h>", "", "```c\nreturn 0;\n```", "_Pragma(\"once\")"):
            with self.assertRaises(custom.Rejected):
                self.plan(code)


if __name__ == "__main__":
    unittest.main()
