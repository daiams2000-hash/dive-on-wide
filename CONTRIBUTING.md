# Contributing

Dive on Wide is small on purpose: one `server.py`, one `index.html`, the Python
standard library. Please keep it that way — the value of a program you can
read in an afternoon is hard to get back once it is gone.

## Ground rules

**No dependencies in the core.** If something needs a library, it belongs
behind an optional switch that degrades honestly when the library is absent —
the way PyNaCl, Docker, browser-use and the grounding server already work.
`backend_info()`-style diagnostics that say "ready / missing / here is how to
get it" are the pattern.

**Never pretend.** No image without an image API. No research report without
sources. No "ready" badge for something that has not been probed. Several bugs
in this project's history were exactly this: a check that reported success
without testing anything.

**Every feature ships with a test.** A function decorated
`@test("group", "description")` in `tests/run_tests.py`. New agents, skills and
pipelines go into `ERWARTETE_AGENTEN`, `ERWARTETE_SKILLS` and
`ERWARTETE_PIPELINES` so they are exercised automatically.

**Write a test that can fail.** Twice in this project a test passed because it
could not fail — an empty slice made a loop vacuous, and a string search found
its own comment. Before you trust a new test, break the thing it guards and
watch it go red.

## Running things

```bash
python3 tests/run_tests.py        # all 210 tests, no Ollama needed (mock)
python3 tests/run_tests.py --nur mesh
./start.sh                        # http://localhost:3000
```

The suite needs no installation and no running Ollama; a mock server steps in.

## Before you open a pull request

```bash
cd ~/DowOS && ./werkzeuge/freigeben.sh <version>
```

That runs the suite **and** the two-instance end-to-end pass. Both matter for
different reasons: the suite checks parts, often against their own constants;
the end-to-end pass checks the interplay across two processes with fixed
expectations and verifies every effect **on the far side**. Twelve write paths
were once silently broken while the suite stayed green and every view looked
correct — because views only read.

## Adding a building block

Everything flows the same way, and that is the organising principle:

```
input → parser → context → model → result → artifact → inbox → Second Brain → Evolver
```

Three shared functions do it: `emit`, `save_artifact`, `deliver`. A new block
registers in `STEP_RUNNERS` and `STEP_META` and joins the flow with one call.
If your feature needs its own path through this, that is usually a sign it
belongs somewhere else.

## Style

- Comments explain **why**, not what. The code says what.
- German is fine in comments and identifiers; the README is English because
  that is where the readers are.
- Avoid German typographic quotes inside Python strings — they have broken
  this file before.
- Never delete `storage/`, `dowos.db*` or `artifacts/` in a folder that might
  be running. Check with `lsof -iTCP:3000 -sTCP:LISTEN` first.

## What we will probably say no to

- A dependency in the core path.
- A feature that cannot be turned off.
- Anything that contributes a user's hardware without visible, revocable
  consent.
- Marketing language in the README. Developers smell it and leave.
