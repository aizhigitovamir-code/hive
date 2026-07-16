# Example run (real, unedited)

Goal given to HIVE:

> Create a Python script hello.py that prints today's date, run it, and confirm the output is correct

What happened: the queen brain planned it into three tasks, the swarm executed them on free models, the verifier forced a fix round on the code before accepting it, and the queen synthesised the final report.

- **`plan.json`** — the task DAG the queen produced (`create_script` → `run_script` → `verify_output`, with types and an acceptance test per task).
- **`REPORT.md`** — the final synthesised deliverable.

Reproduce it:

```bash
hive "Create a Python script hello.py that prints today's date, run it, and confirm the output is correct"
```

Everything lands in `runs/<id>/` — the plan, each agent's output with its verify score, a `workspace/` of the real files created, and this report.
