"""Check that every model provider answers with the credentials configured in .env or the environment.

For each provider in the model menu (Gemini, DeepSeek, GLM, Qwen, GPT), one tiny request goes to the models of its
chain, cheapest first, until one answers. The report says which model answered and how fast, or why none did (daily
quota used up, key rejected, unreachable). Keys are never printed. Qwen on TACC needs no key, only your own TACC
account and the course SDK; that is reported, not tested.

  python3 scripts/check_apis.py          # or, from the repository root: ./start.sh --check

Exit status 0 when every listed provider has a model that answers.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import server  # noqa: E402
from app import tacc_live  # noqa: E402
from flagpilot import client as llm_client  # noqa: E402

PROMPT = [{"role": "user", "content": "Reply with the single word OK."}]


_KEYLIKE = re.compile(r"(sk-|AIza|gh[pousr]_)[A-Za-z0-9_\-]{8,}")


def why(err: str) -> str:
    """A short reason. Provider errors can echo the key back ("invalid key sk-..."): anything key-shaped is masked."""
    try:
        text = llm_client.failover_reason(err) or str(err)
    except Exception:
        text = str(err)
    return _KEYLIKE.sub(lambda m: m.group(1) + "…", text)[:80]


def check_model(name: str) -> tuple[bool, str, float]:
    spec = server._spec(name, server._base_for(name))
    c = llm_client.build_client(spec, chain=False)
    c.busy_retries = 0                       # one quick try: this is a check, not a run
    t0 = time.time()
    try:
        comp = c.complete(PROMPT, max_tokens=32)
    except Exception as e:                   # noqa: BLE001 - report it, do not crash the check
        return False, f"{type(e).__name__}: {e}"[:80], time.time() - t0
    return (not comp.error), (why(comp.error) if comp.error else ""), time.time() - t0


def main() -> int:
    provs = server.provider_list()
    if not provs:
        print("No model provider has a key in this copy: only the offline demo model and replays work.")
        print("Configure a provider key in the environment or in .env (see env.example).")
        return 1
    bad = 0
    for p in provs:
        pid = p["id"].split(":", 1)[1]
        chain = [m for m in server.provider_chain(pid) if server.provider_of(m) == pid]
        tried = []
        for m in chain:
            ok, reason, dt = check_model(m)
            if ok:
                print(f"  OK    {p['label']:9} answered by {m} in {dt:.1f} s")
                break
            tried.append(f"{m}: {reason}")
        else:
            bad += 1
            print(f"  FAIL  {p['label']:9} " + ("; ".join(tried) or "no model listed"))
    fb = server._fallback_model()
    print(f"  when a provider fails, answers fall back to: {fb or '(none set)'}")
    sdk = tacc_live.tc_status()             # the same three places the page looks in: TCLOUD_BIN, the remembered
    print("  Qwen on TACC: needs no key; needs your own TACC account and the course SDK"   # path, then PATH
          + (f" (tcloud at {sdk['path']})" if sdk["ok"]
             else " (tcloud not found yet: the page asks for its path on the first connect)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
