# Architecture

Dive on Wide is deliberately small: one `server.py`, one `index.html`, the Python
standard library. This document explains the four ideas that hold it together,
so a change lands in the right place instead of beside it.

```
Browser (frontend/index.html — vanilla-JS SPA, no build step)
   │  REST + NDJSON streaming
   ▼
server.py (stdlib HTTP server, port 3000)
   ├── SQLite   → storage/dowos.db      chats, agents, prompts, skills, settings
   ├── Files    → storage/artifacts/    generated artifacts
   ├── Router   → LLM providers         Ollama native · OpenAI-compatible · mesh
   └── mesh/    → the decentralised substrate (optional, off by default)
```

## 1. The Flow

Every module behaves the same way. This is the organising principle, and most
questions about "where does this go" answer themselves once it is understood.

```
input → parser (@skill · /prompt · 🌐 web) → context (agent · knowledge · Second Brain)
      → model → result → artifact → inbox → Second Brain → Evolver
```

Chat, skill, pipeline, deep research, browser task, coding agent, computer use
— all of them end the same way. Three shared functions implement it:

- `emit(title, body)` — a notice in the inbox
- `save_artifact(...)` — the result becomes a file under `storage/artifacts/`
- `deliver(...)` — both together, the usual case

A new building block registers in `STEP_RUNNERS` (what it does) and
`STEP_META` (how it appears in the builder) and joins the flow with one call.
If a feature needs its own path through this, it usually belongs elsewhere.

## 2. The provider router

Models are referenced as `providerid@@modelname`. A plain name routes through
the default provider, which is what keeps old configurations working.

```python
parse_model_ref("ollama@@qwen3:8b")  → (provider dict, "qwen3:8b")
parse_model_ref("qwen3:8b")          → (default provider, "qwen3:8b")
```

Three provider types, one interface (`llm_chat_once`, `llm_stream`):

| Type | Covers |
|---|---|
| `ollama` | Ollama's native API |
| `openai` | everything OpenAI-compatible — vLLM, LM Studio, llama.cpp, mlx_vlm, OpenAI, OpenRouter, Groq |
| `mesh` | models running on **other devices**, routed through task distribution |

`llm_stream` normalises Ollama's NDJSON and OpenAI's SSE into one stream of
text chunks, so the browser protocol never changes. The mesh type yields its
result in one piece rather than faking a token stream that does not exist.

**Deliberate:** an unknown provider prefix raises instead of falling back to
the default. Silently sending `mesh@@…` to Ollama produced "Connection
refused", a message nobody could trace back to the network being down.

## 3. The web bridge is two steps, not one

Searching and reading are separate problems and have separate backends.

```
query → distillation → search backend → relevance filter → fetch backend → grounding
```

- **Distillation** turns a natural-language question into several search
  variants (proper nouns first, typo correction included) and takes the
  best-scoring one, not the first that returns anything.
- **Relevance filter** scores hits against the *original topic*, not against
  the distilled variant — a drifting variant used to bring its own yardstick.
- **Grounding** forces the model to use only what was retrieved and to name it.
  With no hits it says so instead of inventing.
- Sources are appended **deterministically in code**, not typed by the model,
  and travel through pipeline steps in `ctx["quellen"]`. Models used to mangle
  URLs or invent replacements.

Every backend degrades to the keyless default if it fails, so research never
disappears entirely.

## 4. The mesh

Optional, off by default, and Dive on Wide is fully functional without it. Layers
bottom to top, each usable alone:

| Layer | Module | What it guarantees |
|---|---|---|
| 0 | `crypto` | Ed25519, X25519, XChaCha20-Poly1305 — checked against RFC vectors |
| 0 | `fluechtig` | secrets in wipeable buffers; identities per topic with **independent** randomness |
| 1 | `arbeit` | proof-of-work bound to the data, cheap enough for a phone |
| 1 | `inhalt` | address = hash of content, so no node can serve something else under it |
| 2 | `ressourcen` | never degrade the owner's device; instant handback; kill switch |
| 3 | `anker` | a pairwise secret → a rendezvous address that changes daily |
| 4 | `transport` | one interface, two substrates: real UDP, and an in-process net for tests |
| 4 | `knoten` | ties it together; Diving Net (Klause) and open net (Weite) never mix |
| 5 | `forum`, `zwiebel`, `ticket`, `qr` | threads with a TTL, onion hops, invitations |

Two design decisions worth knowing:

**Identities get independent randomness, not a derived master seed.** A master
seed would make every pseudonym linkable the moment it leaked, and would let
the operator prove two pseudonyms belong together. The price is that
identities cannot be recovered — which is the point.

**The in-process network is not a testing convenience.** A distributed system
that cannot be replayed deterministically never gets finished. It supports
configurable packet loss, so "works only without loss" fails here rather than
in the field.

## Testing

`tests/run_tests.py` is a harness of its own — no installation, no running
Ollama (a mock steps in). A test is a function decorated
`@test("group", "description")`.

Two levels, for different reasons:

- **The suite** checks parts. It often checks them against their own constants,
  which means changing a constant can leave it green.
- **`werkzeuge/volldurchlauf.py`** runs two real instances and verifies every
  effect **on the far side**. Twelve write paths were once silently broken
  while the suite stayed green, because views only read.

Both must pass before a release replaces the previous one.

## Where state lives

| What | Where | Survives restart |
|---|---|---|
| Chats, agents, skills, settings, API keys | `storage/dowos.db` | yes |
| Artifacts, workspaces | `storage/` | yes |
| Mesh identities, contacts, messages, forum | RAM only | **no, by design** |
| Theme choice | browser localStorage | yes |

The mesh keeps nothing on disk. That is the promise, and the service worker
honours it too: it caches the shell and never anything under `/api/`.
