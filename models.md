# HIVE model routing (verified 2026-07-16)

**Brain (queen):** `claude` CLI — Opus 4.8 (~88.6% SWE-Verified) / Fable 5 if your plan exposes it
(95% SWE-Verified, restored 1 Jul 2026). Best planners available; use `--free-brain` to swap in a
free reasoner and spend zero claude quota.

**Workers:** the LiteLLM free-stack aliases at `localhost:4000` (20 deployments, auto-rotate on 429).
Top free coders right now sit ~80% SWE-Verified: DeepSeek-V4 (80.6), MiniMax-M3 (80.5), Kimi-K2.6 (80.2),
GLM-5.2. morphllm's 2026 finding: *the scaffold around the model drives more variance than the model* —
which is why HIVE routes by task, verifies adversarially, and loops fixes.

| task type | alias chain | backed by |
|-----------|-------------|-----------|
| code / reason | free-coder → free-general → free-fast | DeepSeek-R1/V4, GLM-5, Qwen3-Coder, GPT-OSS-120B, Cerebras GLM-4.7, Zen big-pickle |
| write / research | free-general → free-fast → free-coder | Gemini 2.5-flash (1500/day), SambaNova Llama-3.3-70B, Groq GPT-OSS-120B, Qwen3-Next-80B |
| analysis | free-general → free-coder → free-fast | as above |
| fast (glue/classify) | free-fast → free-general → free-coder | Groq GPT-OSS-20B, Gemini flash-lite, Zen deepseek-v4-flash |
| bigctx (200K+ repo) | free-bigctx → free-coder → free-general | Z.ai GLM-4.7-Flash (203K), Qwen3-Coder (262K) |

Every worker call escalates down its chain on error/empty; the proxy independently rotates providers
inside each alias. Slugs drift monthly — re-verify against each provider dashboard if a model 404s.
Edit chains in `config.json` (`tiers`), add providers in `~/…/OPERATOR - AMIR/litellm-config.yaml`.
