"""Evaluation runner for the Kavir Motor assistant (measurement only).

Sends each question of a JSONL file to the running server, waits for the final
answer and checks the expected numbers. Governor telemetry (route, steps,
decisions) is joined from ``pilot/meta_data/run_telemetry/<date>.jsonl``.
The question file is NEVER read by the assistant itself — it only measures.

Question file (one JSON object per line):
    {"id": "sales_2019_2026", "question": "...", "expected_numbers": ["3409670719977.40"]}

Usage (from the project folder, server running):
    python scripts/eval/run_eval.py --db sorooshan1040 \
        --model "deepseek/deepseek-v4.1-flash (liara)" \
        --file scripts/eval/questions.jsonl --repeat 2 --tag baseline

Output: one summary table on screen + ``scripts/eval/results/<tag>-<time>.jsonl``.
Compare two runs (e.g. DBGPT_GOV_ENABLE=0 vs 1) by their tags.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time
import urllib.request
import uuid

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _digits(text: str) -> str:
    """Canonical digit string: Persian/Arabic digits → ASCII, separators removed."""
    t = (text or "").translate(_FA)
    t = t.replace("٫", ".").replace("٬", "").replace(",", "")
    return t


def _number_found(expected: str, answer: str) -> bool:
    """True when the expected number appears in the answer as a whole number."""
    exp = _digits(str(expected)).strip()
    int_part, _, frac = exp.partition(".")
    frac = frac.rstrip("0")
    tail = (r"\." + re.escape(frac) + r"0*") if frac else r"(?:\.0+)?"
    pattern = r"(?<![\d.])" + re.escape(int_part) + tail + r"(?![\d])"
    return re.search(pattern, _digits(answer)) is not None


def ask(base: str, db: str, model: str, question: str, timeout: int) -> dict:
    conv = str(uuid.uuid4())
    body = {
        "user_input": f"[Database: {db}] {question}" if db else question,
        "conv_uid": conv,
        "chat_mode": "chat_react_agent",
        "model_name": model,
        "select_param": "",
        "temperature": 0,
        "max_new_tokens": 4000,
        "ext_info": {"database_name": db} if db else {},
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/api/v1/chat/react-agent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.time()
    final, steps, error = "", 0, None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                kind = ev.get("type")
                if kind == "step.start":
                    steps += 1
                elif kind == "final":
                    final = ev.get("content") or ""
                elif kind == "done":
                    break
    except Exception as e:  # network / timeout
        error = f"{type(e).__name__}: {e}"
    return {"conv_uid": conv, "final": final, "ui_steps": steps,
            "elapsed_s": round(time.time() - started, 1), "error": error}


def telemetry_for(conv_uid: str, root: str) -> dict:
    for path in sorted(glob.glob(os.path.join(root, "*.jsonl")), reverse=True)[:3]:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if conv_uid in line:
                        rec = json.loads(line)
                        if rec.get("conv_id") == conv_uid:
                            return rec
        except OSError:
            continue
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:5670")
    ap.add_argument("--db", default="")
    ap.add_argument("--model", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--telemetry", default=os.path.join("pilot", "meta_data", "run_telemetry"))
    a = ap.parse_args()

    with open(a.file, encoding="utf-8") as f:
        items = [json.loads(l) for l in f if l.strip() and not l.lstrip().startswith("#")]
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{a.tag}-{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl")

    rows = []
    with open(out_path, "w", encoding="utf-8") as out:
        for item in items:
            for rep in range(a.repeat):
                res = ask(a.base, a.db, a.model, item["question"], a.timeout)
                time.sleep(1.0)
                tel = telemetry_for(res["conv_uid"], a.telemetry)
                expected = item.get("expected_numbers") or []
                hits = [n for n in expected if _number_found(n, res["final"])]
                rec = {
                    "id": item.get("id"), "rep": rep, **res,
                    "correct": bool(expected) and len(hits) == len(expected),
                    "expected": expected, "found": hits,
                    "route": tel.get("route"), "profile": tel.get("profile"),
                    "agent_steps": tel.get("steps"), "sql_ok": tel.get("sql_ok"),
                    "jev_calls": tel.get("jev_calls"), "repairs": tel.get("repairs"),
                    "early_exit": tel.get("early_exit_hinted"),
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                rows.append(rec)
                print(f"{rec['id']:<28} rep={rep} correct={rec['correct']!s:<5} "
                      f"steps={rec['agent_steps']} t={rec['elapsed_s']}s route={rec['route']} "
                      f"{'ERR ' + rec['error'] if rec['error'] else ''}")

    scored = [r for r in rows if r["expected"]]
    acc = sum(r["correct"] for r in scored) / len(scored) if scored else float("nan")
    times = sorted(r["elapsed_s"] for r in rows)
    steps = [r["agent_steps"] for r in rows if isinstance(r["agent_steps"], int)]
    p = lambda q: times[min(len(times) - 1, int(q * len(times)))] if times else float("nan")  # noqa: E731
    print("\n=== summary:", a.tag, "===")
    print(f"runs={len(rows)}  accuracy={acc:.0%}  time p50={p(0.5)}s p90={p(0.9)}s  "
          f"avg agent steps={sum(steps) / len(steps) if steps else float('nan'):.1f}  "
          f"errors={sum(1 for r in rows if r['error'])}")
    print("details:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
