"""HIVE engine — a free-model multi-agent orchestrator for Amir.
Stdlib only. Workers call the LiteLLM free-stack proxy (localhost:4000);
the queen brain calls the `claude` CLI (Opus/Fable-class) headless.
"""
import json, os, re, sys, time, shutil, subprocess, urllib.request, urllib.error
import concurrent.futures as cf
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def slug(s, n=44):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:n] or "run"


def _balance(text, oc, cc):
    i = text.find(oc)
    if i == -1:
        return None
    depth = 0; in_str = False; esc = False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == oc: depth += 1
        elif ch == cc:
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    return None


def extract_json(text):
    """Pull the first JSON value out of an LLM reply (handles ```json fences + trailing commas)."""
    if not text:
        return None
    cands = []
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence: cands.append(fence.group(1))
    cands.append(text)
    for c in cands:
        for oc, cc in (("{", "}"), ("[", "]")):
            raw = _balance(c, oc, cc)
            if not raw: continue
            for attempt in (raw, re.sub(r",(\s*[}\]])", r"\1", raw)):
                try:
                    return json.loads(attempt)
                except Exception:
                    pass
    return None


class LLMError(Exception):
    pass


def _http_post(url, payload, key, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _proxy_chat(model, messages, max_tokens, temperature):
    url = CFG["proxy_base"].rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    d = _http_post(url, payload, CFG["proxy_key"], CFG["http_timeout"])
    ch = (d or {}).get("choices")
    if not ch:
        raise LLMError(f"no choices from {model}: {str(d)[:160]}")
    msg = ch[0].get("message", {}) or {}
    content = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    if not content:
        raise LLMError(f"empty content from {model} ({d.get('model')})")
    return content, d.get("model", model)


def chat(chain, messages, max_tokens=1400, temperature=0.4, label=""):
    """chain: alias str or ordered list of aliases to escalate through. -> (text, answered_by)."""
    if isinstance(chain, str):
        chain = [chain]
    last = "no models tried"
    for model in chain:
        for attempt in range(CFG["http_retries"]):
            try:
                return _proxy_chat(model, messages, max_tokens, temperature)
            except Exception as e:
                last = f"{model}: {e}"
                time.sleep(min(1.5 * (attempt + 1), 6))
    raise LLMError(f"all models failed for {label or 'call'} -> {last}")


def _claude_path():
    return shutil.which("claude") or "/usr/local/bin/claude"


def brain_chat(system, user, max_tokens=4000):
    """Queen brain. Prefers the claude CLI; falls back to the strongest free alias."""
    if CFG.get("brain_backend") == "claude-cli":
        try:
            cmd = [_claude_path(), "-p", user, "--append-system-prompt", system]
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=CFG.get("brain_timeout", 240), stdin=subprocess.DEVNULL)
            out = (p.stdout or "").strip()
            if out:
                return out
            sys.stderr.write(f"[brain] claude empty: {(p.stderr or '')[:160]}\n")
        except Exception as e:
            sys.stderr.write(f"[brain] claude-cli failed ({e}); using free brain\n")
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    txt, _ = chat(CFG["brain_free_fallback"], msgs, max_tokens=max_tokens, temperature=0.3, label="brain-free")
    return txt


# ---------- Router: task type -> ordered model chain ----------
def route(task):
    tiers = CFG["tiers"]
    return tiers.get((task.get("type") or "analysis").lower(), tiers["analysis"])


# ---------- Reserved atoms (never auto-executed; queued for Amir) ----------
RESERVED_PATTERNS = [
    (r"\brm\s+-rf?\b", "irreversible-delete"),
    (r"\bsudo\b", "privileged"),
    (r"\bgit\s+push\b", "publish"),
    (r"\b(npm|pip|brew|gh)\s+(publish|release)\b", "publish"),
    (r"\bcurl\b[^\n]*-X\s*(POST|PUT|DELETE)", "outbound-write"),
    (r"\b(stripe|paypal|checkout|purchase|payment|wire|transfer)\b", "spend-money"),
    (r"\b(mailx|sendmail|--send|smtp)\b", "third-party-outreach"),
]


def reserved_hit(cmd):
    low = (cmd or "").lower()
    for pat, kind in RESERVED_PATTERNS:
        if re.search(pat, low):
            return kind
    return None


class Tools:
    def __init__(self, store):
        self.store = store
        self.ws = store.workspace

    def _safe(self, path):
        p = (self.ws / path).resolve()
        if p != self.ws and self.ws not in p.parents:
            return None
        return p

    def write_file(self, path, content):
        p = self._safe(path or "out.txt")
        if p is None:
            return "ERROR: path escapes workspace"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content))
        return f"wrote {path} ({len(str(content))} bytes)"

    def read_file(self, path):
        p = self._safe(path or "")
        if p is None or not p.exists():
            return f"ERROR: {path} not found"
        return p.read_text()[:8000]

    def run_shell(self, cmd):
        kind = reserved_hit(cmd)
        if kind:
            self.store.reserve(kind, cmd)
            return f"RESERVED ({kind}): queued for Amir's one tap, NOT executed."
        try:
            p = subprocess.run(cmd, shell=True, cwd=str(self.ws),
                               capture_output=True, text=True, timeout=90)
            return ((p.stdout or "") + (p.stderr or ""))[:4000] or "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out (90s)"
        except Exception as e:
            return f"ERROR: {e}"

    def reserve(self, detail):
        self.store.reserve("manual", detail or "unspecified")
        return "RESERVED: queued for Amir."


class Store:
    def __init__(self, goal, input_dir=None):
        self.id = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(goal)}"
        self.dir = HERE / "runs" / self.id
        self.workspace = self.dir / "workspace"
        (self.workspace / "output").mkdir(parents=True, exist_ok=True)
        self.goal = goal
        (self.dir / "goal.txt").write_text(goal)
        self.reserved_path = self.dir / "reserved_actions.jsonl"
        self.log_path = self.dir / "hive.log"
        self.manifest = self._ingest(Path(input_dir).expanduser()) if input_dir else []

    def _ingest(self, src):
        dst = self.workspace / "input"
        skip = {".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mov", ".pdf", ".zip", ".ico"}
        man = []
        for p in sorted(src.rglob("*")):
            if p.is_dir() or p.suffix.lower() in skip or p.stat().st_size > 1_000_000:
                continue
            rel = p.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                out.write_bytes(p.read_bytes())
                man.append((str(rel), p.stat().st_size))
            except Exception:
                pass
        return man

    def log(self, msg):
        line = f"[{now()}] {msg}"
        with open(self.log_path, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def reserve(self, kind, detail):
        with open(self.reserved_path, "a") as f:
            f.write(json.dumps({"time": now(), "kind": kind, "detail": detail}) + "\n")

    def artifact(self, name, content):
        p = self.dir / name
        p.write_text(content)
        return p

    def reserved_actions(self):
        if not self.reserved_path.exists():
            return []
        return [json.loads(l) for l in self.reserved_path.read_text().splitlines() if l.strip()]


# ---------- QUEEN BRAIN: decompose goal -> task DAG ----------
PLAN_SYS = """You are the QUEEN BRAIN of HIVE, an autonomous multi-agent system operated by Amir.
Decompose the GOAL into a concrete plan of tasks that cheaper worker agents run in parallel.
Output ONLY a JSON object, no prose outside it.
- 3 to 8 tasks. Each independently executable by one worker.
- Fields per task: id (short slug), title,
  type = EXACTLY ONE OF research|code|write|analysis|fast|reason|bigctx (never "agent"),
  mode = "llm" (thinking/writing/research) OR "agent" (needs to create files or run shell),
  deps (list of task ids that must finish first), acceptance (one sentence success test).
- Maximise parallelism; add deps only when a task truly needs another's output.
- Anything that spends money, messages third parties, uses credentials, or is an irreversible delete:
  set mode "agent" and tell the worker to PREPARE it fully and call the reserve tool (never execute).
Shape: {"summary":"one line","tasks":[{...}]}"""


def plan(goal, context=""):
    user = f"GOAL:\n{goal}\n\n{context}\nProduce the JSON plan now."
    for nudge in ("", "\nReturn VALID JSON only, starting with '{'."):
        txt = brain_chat(PLAN_SYS, user + nudge, max_tokens=3000)
        data = extract_json(txt)
        if isinstance(data, dict) and isinstance(data.get("tasks"), list) and data["tasks"]:
            break
    if not (isinstance(data, dict) and data.get("tasks")):
        raise LLMError("brain did not return a valid plan")
    for i, t in enumerate(data["tasks"]):
        t.setdefault("id", f"t{i+1}")
        t.setdefault("type", "analysis")
        t.setdefault("mode", "llm")
        t.setdefault("deps", [])
        t.setdefault("acceptance", "Output is correct and complete.")
        t["type"] = str(t["type"]).lower()
    data.setdefault("summary", goal[:120])
    return data


# ---------- WORKERS ----------
WORKER_LLM_SYS = """You are a HIVE worker. Deliver the actual finished content for the task (not a plan to do it).
Be specific, concrete and immediately usable. If a fact is missing, state the assumption and continue."""

AGENT_SYS = """You are a HIVE agent worker with tools. Accomplish the task by emitting ONE action as JSON per turn:
{"tool":"write_file","path":"rel/path","content":"..."}
{"tool":"read_file","path":"rel/path"}
{"tool":"run_shell","cmd":"..."}   (runs in your workspace; spend/outreach/destructive commands are auto-queued to Amir, not run)
{"tool":"reserve","detail":"what needs Amir's tap"}
{"final":"summary of what you produced"}
If input/ files exist, READ the relevant ones first; WRITE every deliverable under output/ (e.g. output/wf9-v2/...).
Emit ONLY the JSON for the next action. After each you receive an OBSERVATION. Keep steps minimal; create real files."""


def worker_llm(task, context):
    chain = route(task)
    user = f"TASK: {task['title']}\nACCEPTANCE: {task['acceptance']}\n"
    if context:
        user += f"\nCONTEXT FROM EARLIER TASKS:\n{context}\n"
    user += "\nProduce the deliverable now."
    return chat(chain, [{"role": "system", "content": WORKER_LLM_SYS},
                        {"role": "user", "content": user}],
                max_tokens=2200, temperature=0.5, label=task["id"])


def worker_agent(task, context, tools, max_steps):
    chain = route(task)
    start = (f"TASK: {task['title']}\nACCEPTANCE: {task['acceptance']}\n"
             f"Files you write land in the run workspace.\n"
             + (f"CONTEXT:\n{context}\n" if context else "")
             + "Begin. Emit your first action as JSON.")
    msgs = [{"role": "system", "content": AGENT_SYS}, {"role": "user", "content": start}]
    last_by = "none"
    for step in range(max_steps):
        txt, by = chat(chain, msgs, max_tokens=1600, temperature=0.3, label=f"{task['id']}#{step}")
        last_by = by
        action = extract_json(txt)
        if not isinstance(action, dict):
            msgs += [{"role": "assistant", "content": txt[:500]},
                     {"role": "user", "content": "Emit a single valid JSON action."}]
            continue
        if "final" in action:
            return action.get("final", "done"), last_by
        tool = action.get("tool")
        if tool == "write_file":
            obs = tools.write_file(action.get("path", "out.txt"), action.get("content", ""))
        elif tool == "read_file":
            obs = tools.read_file(action.get("path", ""))
        elif tool == "run_shell":
            obs = tools.run_shell(action.get("cmd", ""))
        elif tool == "reserve":
            obs = tools.reserve(action.get("detail", ""))
        else:
            obs = "ERROR: unknown tool"
        msgs += [{"role": "assistant", "content": json.dumps(action)[:800]},
                 {"role": "user", "content": f"OBSERVATION: {obs[:1500]}\nNext action as JSON."}]
    return "(agent hit step limit)", last_by


def execute(task, context, store, tools):
    try:
        if task.get("mode") == "agent":
            out, by = worker_agent(task, context, tools, CFG["max_agent_steps"])
        else:
            out, by = worker_llm(task, context)
    except Exception as e:
        out, by = f"ERROR: worker failed: {e}", "none"
    return {"id": task["id"], "title": task["title"], "output": out, "by": by}


# ---------- ADVERSARIAL VERIFIER ----------
VERIFY_LENSES = [
    ("correctness", "Is the deliverable factually and technically CORRECT? Find any error."),
    ("completeness", "Does it FULLY satisfy the acceptance criterion? What is missing?"),
    ("failure-modes", "List up to 10 concrete ways this could be WRONG, break, or fail in practice, each with a one-line fix."),
]
VERIFY_SYS = ('You are a ruthless HIVE verifier. Be skeptical and specific. Respond ONLY as JSON: '
              '{"pass": true|false, "issues": ["..."], "fixes": ["..."]}.')


def _verify_one(task, output, lens_q):
    user = (f"TASK: {task['title']}\nACCEPTANCE: {task['acceptance']}\nLENS: {lens_q}\n\n"
            f"DELIVERABLE:\n{output[:6000]}\n\n"
            "Judge via this lens. If it broadly meets acceptance mark pass=true, but still list any issues/fixes.")
    try:
        txt, _ = chat(["free-general", "free-coder", "free-fast"],
                      [{"role": "system", "content": VERIFY_SYS}, {"role": "user", "content": user}],
                      max_tokens=900, temperature=0.2, label="verify")
        d = extract_json(txt)
    except Exception:
        d = None
    if not isinstance(d, dict):
        d = {}
    return {"pass": bool(d.get("pass", True)), "issues": d.get("issues", []) or [], "fixes": d.get("fixes", []) or []}


def verify(task, output):
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda l: _verify_one(task, output, l[1]), VERIFY_LENSES))
    passes = sum(1 for r in res if r["pass"])
    issues, fixes = [], []
    for r in res:
        if not r["pass"]:
            issues += r["issues"]; fixes += r["fixes"]
    return {"ok": passes >= 2, "passes": passes, "issues": issues[:10], "fixes": fixes[:10]}
