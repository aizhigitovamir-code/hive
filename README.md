# HIVE 🐝 — one command, a swarm of free AI agents

A terminal orchestrator: a **queen brain** (Claude, max model) plans a goal into tasks, a **router**
assigns each task to the best **free** model, a **swarm of workers** executes them in parallel with real
file/shell tools, and an **adversarial verifier** red-teams every result and loops fixes — before a final
report lands in your lap. Built on your existing free stack (LiteLLM proxy + CCR + OpenCode + `claude`).

## Use it
```bash
hive "your goal in plain english"        # queen = claude, workers = free stack
hive --dry-run "your goal"               # plan only, spends nothing
hive --free-brain "your goal"            # brain is free too → £0 run
hive --workers 8 "your goal"             # more parallelism (default 5)
```
The launcher auto-starts the free-stack proxy if it's down. Each run writes to `~/hive/runs/<id>/`:
`plan.json`, `hive.log`, `task-*.md` (per-agent output + verify score), `workspace/` (real files the
agents created), `reserved_actions.jsonl`, and **`REPORT.md`** (the synthesized deliverable).

## How it works
1. **Plan** — the queen (`claude -p`, Opus/Fable-class) decomposes the goal into a JSON task DAG
   (type, mode, deps, acceptance test). Rich plans, understands what to parallelise.
2. **Route** — each task's `type` maps to an ordered chain of free aliases (see `models.md`).
3. **Swarm** — ready tasks (deps met) run in parallel. Two worker modes:
   - `llm` — pure thinking/writing/research, returns the deliverable.
   - `agent` — a tool-loop that actually `write_file` / `read_file` / `run_shell` in the run workspace.
4. **Verify** — 3 skeptical lenses per result (correctness · completeness · *10 ways this fails + fixes*).
   Majority-fail → the worker redoes it with the issue list, up to `max_fix_rounds`.
5. **Synthesize** — the queen merges everything into `REPORT.md`.

## Safety — reserved atoms (the only things it won't auto-do)
Spending money · messaging third parties · using credentials · irreversible deletes · publishing.
Workers **prepare these to the last step** and drop them in `reserved_actions.jsonl` as a single tap for
you — they are never executed automatically. Shell commands matching those patterns are auto-queued, not run.

## Tuning — `config.json`
`workers` (parallelism) · `verify_voters` · `max_fix_rounds` · `max_waves` · `max_agent_steps` ·
`tiers` (task→model chains) · `brain_backend` (`claude-cli` or `free`). No pip installs — stdlib only.

## Files
`hive` launcher · `hive.py` orchestrator · `hivelib.py` engine (llm/router/tools/brain/worker/verifier/store)
· `config.json` · `models.md` routing table · `runs/` history.
