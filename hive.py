#!/usr/bin/env python3
"""HIVE — one command, a swarm of free AI agents.
Usage:
  hive "your goal"                 full run (queen brain = claude, workers = free stack)
  hive --dry-run "your goal"       plan only, no workers spent
  hive --free-brain "your goal"    use a free model as the brain too (zero claude quota)
  hive --workers 8 "your goal"     set parallel worker count
"""
import sys, json, argparse
import concurrent.futures as cf
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hivelib as H


def run_task(task, done_ctx, store, tools):
    ctx = "\n\n".join(f"[{k}] {v['output'][:1200]}"
                      for k, v in done_ctx.items() if k in task.get("deps", []))
    store.log(f"▶ {task['id']} ({task['type']}/{task['mode']}): {task['title']}")
    res = H.execute(task, ctx, store, tools)
    v = H.verify(task, res["output"])
    rounds = 0
    while not v["ok"] and rounds < H.CFG["max_fix_rounds"]:
        rounds += 1
        store.log(f"  ↻ {task['id']} fix round {rounds}: {len(v['issues'])} issues")
        fix_ctx = ctx + "\n\nFIX THESE ISSUES:\n- " + "\n- ".join(v["issues"])
        if v["fixes"]:
            fix_ctx += "\nAPPLY FIXES:\n- " + "\n- ".join(v["fixes"])
        res = H.execute(task, fix_ctx, store, tools)
        v = H.verify(task, res["output"])
    res["verify"] = v
    store.log(f"  {'✓' if v['ok'] else '✗'} {task['id']} by {res['by']} (verify {v['passes']}/3)")
    store.artifact(f"task-{task['id']}.md",
                   f"# {task['title']}\n\nby: {res['by']} | verify: {v['passes']}/3\n\n{res['output']}")
    return res


def dispatch_wave(tasks, done_ctx, store, workers):
    tools = H.Tools(store)
    results = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_task, t, done_ctx, store, tools): t for t in tasks}
        for fut in cf.as_completed(futs):
            t = futs[fut]
            try:
                results[t["id"]] = fut.result()
            except Exception as e:
                store.log(f"task {t['id']} crashed: {e}")
                results[t["id"]] = {"id": t["id"], "title": t["title"],
                                    "output": f"ERROR: {e}", "by": "none", "verify": {"ok": False, "passes": 0}}
    return results


def synthesize(goal, done, store):
    joined = "\n\n".join(
        f"## {r['title']} (by {r['by']}, verify {r.get('verify', {}).get('passes', '?')}/3)\n{r['output']}"
        for r in done.values())
    sys_p = ("You are the HIVE queen brain. Synthesize the workers' outputs into ONE clean, concrete final "
             "deliverable for Amir. Usable, not a summary of process. Flag any RESERVED actions awaiting his tap.")
    user = f"GOAL:\n{goal}\n\nWORKER OUTPUTS:\n{joined[:12000]}\n\nWrite the final deliverable."
    try:
        return H.brain_chat(sys_p, user, max_tokens=4000)
    except Exception as e:
        return f"(synthesis failed: {e})\n\n{joined}"


def print_summary(store, done):
    ok = sum(1 for r in done.values() if r.get("verify", {}).get("ok"))
    print("\n" + "=" * 60)
    print(f"HIVE run: {store.id}")
    print(f"Folder:   {store.dir}")
    print(f"Tasks:    {ok}/{len(done)} verified" if done else "Tasks:    (plan only)")
    reserved = store.reserved_actions()
    if reserved:
        print(f"\n⚠ {len(reserved)} RESERVED action(s) awaiting your tap:")
        for r in reserved:
            print(f"  - [{r['kind']}] {r['detail'][:100]}")
    if done:
        print(f"\nReport:   {store.dir}/REPORT.md")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser(prog="hive")
    ap.add_argument("goal", nargs="+")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--free-brain", action="store_true")
    ap.add_argument("--input", default=None, help="dir of existing files to improve (copied in, originals untouched)")
    ap.add_argument("--out", default=None, help="dir to copy the run's output/ into when done")
    args = ap.parse_args()
    goal = " ".join(args.goal)
    if args.free_brain:
        H.CFG["brain_backend"] = "free"
    workers = args.workers or H.CFG["workers"]

    store = H.Store(goal, input_dir=args.input)
    store.log(f"HIVE run {store.id}")
    store.log(f"GOAL: {goal}")
    ctx = ""
    if store.manifest:
        listing = "\n".join(f"  input/{r} ({b}b)" for r, b in store.manifest[:80])
        ctx = ("INPUT FILES available to agents under input/ (agent mode reads with read_file):\n" + listing +
               "\nPlan agent tasks that READ the relevant input file(s) and WRITE improved versions under output/.\n")
        store.log(f"Ingested {len(store.manifest)} input files")
    store.log("Queen brain planning…")
    plan = H.plan(goal, ctx)
    tasks = {t["id"]: t for t in plan["tasks"]}
    store.artifact("plan.json", json.dumps(plan, indent=2))
    store.log(f"Plan: {plan.get('summary', '')}")
    for t in plan["tasks"]:
        store.log(f"  • {t['id']} [{t['type']}/{t['mode']}] {t['title']} (deps {t.get('deps', [])})")
    if args.dry_run:
        store.log("dry-run: plan only.")
        print_summary(store, {})
        return

    done = {}
    waves = 0
    while len(done) < len(tasks) and waves < H.CFG["max_waves"]:
        waves += 1
        ready = [t for tid, t in tasks.items()
                 if tid not in done and all(d in done for d in t.get("deps", []))]
        if not ready:
            store.log("No ready tasks (dependency deadlock) — running the rest ungated.")
            ready = [t for tid, t in tasks.items() if tid not in done]
        store.log(f"── Wave {waves}: {len(ready)} task(s) across {workers} workers ──")
        done.update(dispatch_wave(ready, done, store, workers))

    store.log("Queen brain synthesizing final report…")
    store.artifact("REPORT.md", synthesize(goal, done, store))
    if args.out:
        import shutil as _sh
        dest = Path(args.out).expanduser(); dest.mkdir(parents=True, exist_ok=True)
        for p in (store.workspace / "output").rglob("*"):
            if p.is_file():
                t = dest / p.relative_to(store.workspace / "output")
                t.parent.mkdir(parents=True, exist_ok=True); _sh.copy2(p, t)
        store.log(f"Copied output → {dest}")
    print_summary(store, done)


if __name__ == "__main__":
    main()
