# AGENTS.md — read this first if you are an AI agent

You are an external agent (Claude Code, Codex, Cursor, another harness) and you
are about to drive **Dive on Wide** or work on its code. This file tells you who does
what, how to work cheaply, and what to ask the user once.

## Who does what

| Role | Who | Does |
|---|---|---|
| **Owner** | the person at this machine | decides; sets rights, approvals and what may leave the machine — only in the Dive on Wide UI |
| **Planner / orchestrator** | **you** | splits the goal into small jobs, writes precise instructions, reviews results, puts the pieces together |
| **Worker** | a local model in Dive on Wide (profile `arbeiter`) | reads, edits, runs tests inside the project |
| **Reviewer** (optional) | a second local model (profile `pruefer`) | checks the worker's result read-only before it comes back to you |

The point of this split: **your tokens go into planning and reviewing, local
compute goes into reading, trying and testing.** A small local model costs
nothing per step; you cost money and budget per token.

## Work token-efficiently

1. **Don't explore first.** Instead of reading the project yourself, send a job:
   "Summarise how X works, name the files" — and read the short answer.
2. **One job, one outcome, one check.** Every job ends with "done when …"
   (a test passes, a file exists, a number matches).
3. **Poll rarely.** Check a running job every 30–60 s at most; never stream logs.
4. **Read the short result.** With `rueckmeldung: knapp` you get the outcome,
   changed files and — if a reviewer is set — its findings. Open files only when
   something is flagged.
5. **Two strikes, then you.** If the local worker fails the same job twice,
   do it yourself or split it smaller. Don't loop.
6. **Specify interfaces yourself.** For anything with several parts, write names,
   return values and conventions into the job (or into the project's `DOWOS.md`).
   Measured: local models otherwise invent two different interfaces for code and
   tests (`docs/ANWENDUNGEN.md`, case 10).

## First contact: ask the user once

```
GET  /api/extern/fragebogen      → questions + three compute profiles for THIS machine
POST /api/extern/profil          → store the answers
```

Ask the user the questions from the response (in their language) and store the
answers. The profiles are computed from the real hardware and the installed
models, for example:

- **sparsam** — one small model (e.g. a 4B) works, the same model reviews;
  little memory, fast, for clearly bounded jobs
- **ausgewogen** — the largest model that fits comfortably works, a small one reviews
- **stark** — the best model measured on this machine works (may fit only tightly)

With a Dive on Wide mesh, more machines mean larger or more models; ask the user how
much of it Dive on Wide may use.

**What you cannot set:** rights (`lesen`/`projekt`/`voll`), approvals, whether
code may run, and what leaves the machine (`AUSGANG_STUFE`). Those belong to the
owner and are only changed in the Dive on Wide UI. If a job is refused, the error says
why — tell the user where to change it; don't try to work around it.

## Doing the work

```
POST /api/extern/auftrag   {"aufgabe": "…", "workspace": "name"}   → {"id": …}
GET  /api/extern/auftrag/<id>                                        → result (+ "pruefung")
GET  /api/extern/info                                                → state, allowed paths
```

Authenticate with a **harness key** (Settings → Access → key for an external
agent); it only reaches `/api/extern/*`. On the same machine you can also use the
terminal: `dowos werkbank --json "…"` (exit codes: 0 done, 2 step limit,
3 question, 4 stuck, 5 model not answering), or `dowos acp` for editors.

Good job text, measured on local models:

> In `rechnung.py`, VAT is computed before the discount; 3 × 19,99 € with 10 %
> should give 10,25 € VAT. Fix it and add a test for exactly this case.
> Done when `python -m unittest discover -s tests` is green.

## Working on Dive on Wide itself (this repository)

- Pure Python standard library, no dependencies in the core. One vanilla-JS file.
- Every change needs a test that can fail; run `python3 tests/run_tests.py`
  (about 5 minutes) before you commit. Keep the README test badge in sync.
- Nothing that touches the machine, the network or the user's data is on by
  default.
- **No output of a frontier model goes into training data** for Dive on Wide models;
  task texts written by one are measurement material only.
- Measured results beat opinions: `docs/ABNAHME.md`, `docs/ANWENDUNGEN.md`,
  `docs/DOWBENCH.md`, `docs/SICHERHEIT.md`.
