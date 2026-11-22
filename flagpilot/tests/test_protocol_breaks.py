"""scripts/protocol_breaks.py: the counts behind README claim C4, from traces."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("protocol_breaks", Path(__file__).resolve().parent.parent / "scripts" / "protocol_breaks.py")
pb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pb)

LONG = json.dumps({"thought": "copy n", "action": "python", "code": "n = " + "7" * 2500})
CUT = '{"thought": "decrypt", "action": "python", "code": "n = 123456'


class TestProtocolBreaks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fp-pb-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def attempt(self, name, replies):
        d = self.tmp / "batch-B0" / name
        d.mkdir(parents=True)
        rows = [{"type": "meta"}]
        for turn, (text, ok) in enumerate(replies, 1):
            rows.append({"type": "model", "turn": turn, "text": text, "finish_reason": "stop"})
            if not ok:
                rows.append({"type": "invalid_action", "turn": turn, "kind": "protocol"})
        (d / "trace.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_counts(self):
        self.attempt("a-s0", [(LONG, True), (CUT, False)])              # a long reply reached the history cut, then a cut one
        self.attempt("b-s0", [(CUT, False), ('{"thought": "x"} and more', False)])   # no long reply before; one not cut
        c = pb.scan(self.tmp)["batch-B0"]
        self.assertEqual([c[f] for f in pb.FIELDS], [2, 3, 3, 2, 1])
        self.assertEqual(pb.main([str(self.tmp)]), 0)


if __name__ == "__main__":
    unittest.main()
