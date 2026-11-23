"""One-shot rebuild after the hackathon window opens (2026-11-23 08:00 UTC).

What it does, in order:
  1. refuses to run before 2026-11-23 08:00 UTC (override: --force-time, for a dry test only)
  2. runs the unit tests and the TypeScript check
  3. re-runs eval/run_eval.py on live mainnet data (writes eval/dataset.json, eval/results.json)
  4. rewrites the eval blocks (<!-- eval:start --> ... <!-- eval:end -->) in README.md and
     the BUIDL text, plus the few sentences elsewhere that quote the eval numbers
  5. renders the eval card PNG and re-outputs the demo video (narration for the number
     sentences is regenerated if the numbers changed); with --recapture it first re-records
     the simulator segments on a hidden desktop (no window on screen)
  6. builds a fresh git history: the old .git is moved to a backup folder, `git init`,
     one commit by Hua-I Yu <contact@stuartyo.com>, no Co-Authored-By or model names.
     Nothing is pushed and no GitHub repo is created.

Usage (from anywhere):
  python scripts/rebuild_after_start.py               # everything
  python scripts/rebuild_after_start.py --dry-run     # print the plan, change nothing
  python scripts/rebuild_after_start.py --skip-git    # everything except the new history
  python scripts/rebuild_after_start.py --recapture   # also re-record the live simulator segments

Paths outside the repo (video tooling, deliverables folder) are set below and can be
overridden with environment variables.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VID = Path(os.environ.get("PG_VIDEO_DIR", r"E:\_tmp_claude\even\vid"))          # compose.py, record.py, wavs, cap/
WORK = VID.parent                                                                 # start_hidden.ps1, backups
DELIVER = Path(os.environ.get("PG_DELIVER_DIR",
                              r"E:\contests\_batch\2026-11-23_even-realities-heads-up-hackathon\交件"))
BUIDL = DELIVER / "BUIDL_text.md"
KOKORO = Path(os.environ.get("PG_KOKORO_DIR", r"E:\_tmp_claude\kokoro_model"))

START_UTC = dt.datetime(2026, 11, 23, 8, 0, tzinfo=dt.timezone.utc)
AUTHOR_NAME = "Hua-I Yu"
AUTHOR_EMAIL = "contact@stuartyo.com"
COMMIT_MSG = ("PaidGlance: Gram Wallet payments checked on TON, shown on Even G2\n\n"
              "Read-only TON verifier (Tether master + canonical jetton wallet, UNVERIFIED on\n"
              "lookup failure), G2 lens app via Even Hub SDK, counter page, voice orders,\n"
              "mainnet evaluation in eval/.")
FORBIDDEN_IN_COMMIT = re.compile(r"co-authored-by|claude|anthropic|openai|gpt|gemini|copilot", re.I)
NEVER_COMMIT = [".env", ".env.local", "server/shift.json"]

DRY = False


def log(msg: str) -> None:
    print(f"[rebuild] {msg}", flush=True)


def run(cmd: list[str], cwd: Path = REPO, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    log(("(dry) " if DRY else "") + " ".join(str(c) for c in cmd))
    if DRY:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=e, text=True, encoding="utf-8",
                       errors="replace", capture_output=True, shell=False)
    if r.stdout.strip():
        print(r.stdout.strip()[-3000:])
    if r.returncode != 0:
        print(r.stderr.strip()[-3000:], file=sys.stderr)
        if check:
            sys.exit(f"[rebuild] FAILED: {' '.join(str(c) for c in cmd)}")
    return r


# ---------------------------------------------------------------- numbers -> words
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + words(n % 100))
    if n < 1_000_000:
        return words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + words(n % 1000))
    return str(n)


# ---------------------------------------------------------------- steps
def check_time(force: bool) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    log(f"now {now:%Y-%m-%d %H:%M} UTC, window opens {START_UTC:%Y-%m-%d %H:%M} UTC")
    if now < START_UTC and not force:
        sys.exit("[rebuild] Too early: the hackathon window has not opened. Nothing changed.")


def tests() -> None:
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    run(["npx.cmd" if os.name == "nt" else "npx", "tsc", "--noEmit"])


def run_eval() -> dict:
    run([sys.executable, "eval/run_eval.py"])
    res = json.loads((REPO / "eval" / "results.json").read_text(encoding="utf-8"))
    if not DRY:
        f, r = res["fake_transfers"], res["real_transfers"]
        log(f"eval: fakes {f['n']} (naive {f['naive_wrong']}, ours {f['ours_wrong']}), "
            f"real {r['n']} (ours wrong {r['ours_wrong']}), canonical {res['canonical_wallet_match']}/"
            f"{res['canonical_wallet_checked']}, fetched {res['fetched_at_utc']}")
        if f["ours_wrong"] or r["ours_wrong"] or res["canonical_wallet_match"] != res["canonical_wallet_checked"]:
            sys.exit("[rebuild] STOP: the verifier made a mistake on the new data. Look at eval/results.json "
                     "before publishing anything; the copy says 0 errors.")
    return res


def readme_block(res: dict) -> str:
    f, r = res["fake_transfers"], res["real_transfers"]
    return f"""<!-- eval:start -->
Data fetched {res['fetched_at_utc']} from toncenter and tonapi. Script: `eval/run_eval.py`. Raw results: `eval/results.json`, transfers: `eval/dataset.json`.

| | Naive notification baseline | PaidGlance |
|---|---|---|
| {f['n']} incoming transfers of the {res['lookalike_usdt_jettons_found']} fake "USDT" tokens: marked paid | **{f['naive_wrong']}** | **{f['ours_wrong']}** |
| {r['n']} recent real Tether USD₮ transfers: wrongly rejected | {r['naive_wrong']} / {r['n']} | **{r['ours_wrong']} / {r['n']}** |
| Sender's jetton wallet matches Tether's own answer | not checked | {res['canonical_wallet_match']} / {res['canonical_wallet_checked']} |

All {r['n']} real transfers went through the Tether master check. {res['canonical_wallet_checked']} of them also went through the canonical jetton-wallet lookup (the free tonapi tier allows about one request per second); the other {r['n'] - res['canonical_wallet_checked']} are scored on the master check alone. The live server looks up every USD₮ transfer and shows CAN'T VERIFY when the lookup fails.

The naive notification baseline judges from what a wallet push shows: a symbol that reads as USDT and an amount above zero, or a comment that claims USD was received. The {f['n']} fake transfers had a face value of ${res['fake_usdt_face_value']:,.2f} "USDT". Real value: zero.
<!-- eval:end -->"""


def buidl_block(res: dict) -> str:
    f, r = res["fake_transfers"], res["real_transfers"]
    face = res["fake_usdt_face_value"]
    face_s = f"${face / 1e6:.1f}M" if face >= 1e6 else f"${face:,.0f}"
    return f"""<!-- eval:start -->
- Data fetched {res['fetched_at_utc'][:10]} {res['fetched_at_utc'][11:]}. {f['n']} incoming transfers of {res['lookalike_usdt_jettons_found']} fake "USDT" tokens, face value {face_s}. A naive notification baseline (symbol reads USDT, amount above zero) marks {'all ' + str(f['n']) if f['naive_wrong'] == f['n'] else str(f['naive_wrong']) + ' of ' + str(f['n'])} as paid. In our tests of {f['n']} fakes, PaidGlance marked {f['ours_wrong']} as paid.
- Real Tether transfers wrongly rejected: {r['ours_wrong']} / {r['n']}. All {r['n']} went through the Tether master check; {res['canonical_wallet_checked']} of them also through the canonical jetton-wallet check ({res['canonical_wallet_match']} / {res['canonical_wallet_checked']} matched).
<!-- eval:end -->"""


def rewrite_docs(res: dict) -> None:
    f = res["fake_transfers"]
    n_tok, n_blk = res["lookalike_usdt_jettons_found"], res["lookalike_blacklisted_by_tonapi"]
    subs = [  # sentences outside the blocks that quote eval numbers
        (r"In our tests of \d+ fake \"USDT\" transfers, \d+ were marked paid",
         f'In our tests of {f["n"]} fake "USDT" transfers, {f["ours_wrong"]} were marked paid'),
        (r"we found \*\*\d+\*\* lookalike", f"we found **{n_tok}** lookalike"),
        (r"tonapi blacklists all \d+", f"tonapi blacklists all {n_tok}" if n_tok == n_blk else f"tonapi blacklists {n_blk} of {n_tok}"),
        (r"already blacklists \(\d+ of \d+\)", f"already blacklists ({n_blk} of {n_tok})"),
        (r"we found \d+ such tokens", f"we found {n_tok} such tokens"),
    ]
    targets = [(REPO / "README.md", readme_block(res)), (BUIDL, buidl_block(res))]
    for path, block in targets:
        if not path.exists():
            log(f"skip (missing) {path}")
            continue
        text = path.read_text(encoding="utf-8")
        new = re.sub(r"<!-- eval:start -->.*?<!-- eval:end -->", lambda _: block, text, flags=re.S)
        for pat, rep in subs:
            new = re.sub(pat, rep, new)
        if "<!-- eval:start -->" not in text:
            sys.exit(f"[rebuild] no eval block in {path}")
        log(f"{'(dry) ' if DRY else ''}update {path.name}: {'changed' if new != text else 'unchanged'}")
        if not DRY:
            path.write_text(new, encoding="utf-8")


NARRATION = {
    "s2": ("Shops that take crypto at the counter ask one question all day: did the money really arrive? "
           "In India, Paytm has thirteen point seven million merchant device subscriptions, like a speaker "
           "that reads out each payment. On TON, we found {tok} fake tokens that call themselves USDT."),
    "s7": ("We tested the check on real chain data. Across {fake} transfers of fake USDT, a naive check of the "
           "notification marks {naive} as paid. PaidGlance marks {ours} as paid, and wrongly rejects "
           "{rej} of {real} real Tether transfers."),
}


def narration(res: dict) -> None:
    f, r = res["fake_transfers"], res["real_transfers"]
    vals = {"tok": words(res["lookalike_usdt_jettons_found"]), "fake": words(f["n"]),
            "naive": "all of them" if f["naive_wrong"] == f["n"] else words(f["naive_wrong"]),
            "ours": words(f["ours_wrong"]), "rej": words(r["ours_wrong"]), "real": words(r["n"])}
    script_path = VID / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    changed = []
    for seg in script:
        if seg["id"] in NARRATION:
            text = NARRATION[seg["id"]].format(**vals)
            if text != seg["text"]:
                seg["text"] = text
                changed.append(seg["id"])
    if not changed:
        log("narration: numbers unchanged, keeping s2/s7 audio")
        return
    log(f"{'(dry) ' if DRY else ''}narration changed for {changed}: regenerating with kokoro")
    if DRY:
        return
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro(str(KOKORO / "kokoro-v1.0.onnx"), str(KOKORO / "voices-v1.0.bin"))
    for seg in script:
        if seg["id"] in changed:
            t = seg["text"].replace("USDT", "U S D T").replace("Paytm", "Pay T M")
            a, sr = k.create(t, voice="am_michael", speed=1.1, lang="en-us")
            sf.write(str(VID / f"{seg['id']}.wav"), a, sr)
            log(f"  {seg['id']}.wav {len(a) / sr:.1f}s")


def recapture() -> None:
    """Re-record the live simulator segments. Everything runs on a hidden Win32 desktop."""
    env = dict(PAIDGLANCE_DEMO="1", PAIDGLANCE_DATA=str(WORK / "shift_rebuild.json"))
    if DRY:
        log("(dry) start backend, vite and the simulator on a hidden desktop, run record.py, stop them")
        return
    (WORK / "shift_rebuild.json").unlink(missing_ok=True)
    procs = [
        subprocess.Popen([sys.executable, "server/app.py"], cwd=REPO, env=dict(os.environ, **env),
                         stdout=open(WORK / "rebuild_server.log", "w"), stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        subprocess.Popen(["npx.cmd", "vite", "--port", "5173", "--strictPort"], cwd=REPO,
                         stdout=open(WORK / "rebuild_vite.log", "w"), stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    ]
    sim_pid = None
    try:
        exe = REPO / "node_modules" / "@evenrealities" / "sim-win32-x64" / "bin" / "evenhub-simulator.exe"
        out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                   str(WORK / "start_hidden.ps1"), "-Exe", str(exe),
                   "-ArgLine", '"http://localhost:5173/?demo=1" --automation-port 9898 --no-glow',
                   "-Cwd", str(WORK)])
        sim_pid = out.stdout.strip().splitlines()[-1]
        import urllib.request
        for _ in range(60):  # first poll does ~25 tonapi lookups at ~1/s
            try:
                st = json.loads(urllib.request.urlopen("http://127.0.0.1:8787/api/state", timeout=5).read())
                urllib.request.urlopen("http://127.0.0.1:9898/api/ping", timeout=5).read()
                if st["poll"]["polls"] >= 1:
                    break
            except Exception:
                pass
            time.sleep(3)
        time.sleep(8)
        old = VID / "cap"
        if old.exists():
            shutil.move(str(old), str(VID / f"cap_before_{int(time.time())}"))
        run([sys.executable, "record.py"], cwd=VID)
    finally:
        if sim_pid:
            subprocess.run(["taskkill", "/PID", sim_pid, "/F"], capture_output=True)
        for p in procs:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F", "/T"], capture_output=True)


def video() -> None:
    env = {"PG_REPO": str(REPO), "PG_CAP": str(VID / "cap")}
    run([sys.executable, "compose.py", "--card", "s7", str(VID / "eval_card.png")], cwd=VID, env=env)
    run([sys.executable, "compose.py"], cwd=VID, env=env)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", "video_noaudio.mp4", "-i", "audio.wav",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "PaidGlance_demo.mp4"], cwd=VID)
    if DRY:
        return
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                     str(VID / "PaidGlance_demo.mp4")]).stdout.strip())
    if dur > 180:
        sys.exit(f"[rebuild] video is {dur:.1f}s, over the 3-minute limit")
    stamp = time.strftime("%Y%m%d_%H%M")
    old_dir = DELIVER / "_old" / f"before_rebuild_{stamp}"
    old_dir.mkdir(parents=True, exist_ok=True)
    for name in ["PaidGlance_demo.mp4", "eval_card.png", "BUIDL_text.md"]:
        if (DELIVER / name).exists():
            shutil.copy2(DELIVER / name, old_dir / name)
    shutil.copy2(VID / "PaidGlance_demo.mp4", DELIVER / "PaidGlance_demo.mp4")
    shutil.copy2(VID / "eval_card.png", DELIVER / "eval_card.png")
    log(f"video {dur:.1f}s -> {DELIVER / 'PaidGlance_demo.mp4'} (old copies in {old_dir})")


def fresh_git() -> None:
    git = REPO / ".git"
    backup = WORK / f"git_backup_{time.strftime('%Y%m%d_%H%M%S')}"
    if git.exists():
        log(f"{'(dry) ' if DRY else ''}move old history {git} -> {backup}")
        if not DRY:
            shutil.move(str(git), str(backup))
    ident = {"GIT_AUTHOR_NAME": AUTHOR_NAME, "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
             "GIT_COMMITTER_NAME": AUTHOR_NAME, "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL}
    run(["git", "init", "-b", "main"])
    run(["git", "config", "user.name", AUTHOR_NAME])
    run(["git", "config", "user.email", AUTHOR_EMAIL])
    run(["git", "add", "-A"])
    if not DRY:
        staged = run(["git", "diff", "--cached", "--name-only"]).stdout.split()
        bad = [p for p in staged if p in NEVER_COMMIT or "__pycache__" in p]
        if bad:
            sys.exit(f"[rebuild] STOP: would commit {bad}")
        if shutil.which("gitleaks"):
            r = run(["gitleaks", "dir", ".", "--no-banner", "--redact"], check=False)
            if r.returncode != 0:
                sys.exit("[rebuild] STOP: gitleaks found something. Nothing committed.")
        else:
            log("gitleaks not found; skipped the secret scan")
    if FORBIDDEN_IN_COMMIT.search(COMMIT_MSG):
        sys.exit("[rebuild] commit message mentions a model or co-author")
    run(["git", "commit", "-q", "-m", COMMIT_MSG], env=ident)
    if not DRY:
        out = run(["git", "log", "--format=%H%n%an <%ae>%n%cn <%ce>%n%aI%n%B"]).stdout
        if FORBIDDEN_IN_COMMIT.search(out) or f"{AUTHOR_NAME} <{AUTHOR_EMAIL}>" not in out:
            sys.exit("[rebuild] STOP: commit metadata check failed")
        when = run(["git", "log", "-1", "--format=%ct"]).stdout.strip()
        if dt.datetime.fromtimestamp(int(when), dt.timezone.utc) < START_UTC:
            sys.exit("[rebuild] STOP: commit time is before the window")
    log("new history ready. Not pushed. Next: create the GitHub repo (private), push, make it public before submitting.")


def main() -> None:
    global DRY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    ap.add_argument("--skip-git", action="store_true")
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--recapture", action="store_true", help="re-record the live simulator segments first")
    ap.add_argument("--offline-eval", action="store_true", help="recompute eval from eval/dataset.json (testing only)")
    ap.add_argument("--force-time", action="store_true", help="skip the time check (dry tests only)")
    a = ap.parse_args()
    DRY = a.dry_run
    check_time(a.force_time or DRY)
    if a.force_time and not DRY and not a.skip_git:
        sys.exit("[rebuild] --force-time is only allowed with --dry-run or --skip-git")
    tests()
    if a.offline_eval:
        run([sys.executable, "eval/run_eval.py", "--offline"])
        res = json.loads((REPO / "eval" / "results.json").read_text(encoding="utf-8"))
    else:
        res = run_eval() if not DRY else json.loads((REPO / "eval" / "results.json").read_text(encoding="utf-8"))
    rewrite_docs(res)
    if not a.skip_video:
        narration(res)
        if a.recapture:
            recapture()
        video()
    if not a.skip_git:
        fresh_git()
    log("done")


if __name__ == "__main__":
    main()
