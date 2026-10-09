# Changelog

Distilled from the actual commit history, not written from memory. Bugs are
listed with what was wrong, because that is the part worth reading.

---

## 0.5.1 — fixes found the same day

<!-- x: Dive on Wide 0.5.1: same-day fixes from our own CI on 9 systems (macOS, Linux, Windows × Python 3.9–3.13) — Windows bash without WSL, the Ubuntu 24.04 sandbox, Python 3.9. -->
Right after 0.5.0 went out, the test suite ran on GitHub for the first time — 9 machines, three systems, Python 3.9
to 3.13 — and found real bugs that no Mac and no VM had shown. All 547 tests now pass on all nine.

**Fixed**
- **Windows without WSL:** `bash` resolved to the WSL launcher in System32, which only says "no installed
  distributions". Git Bash is now preferred; the launcher no longer counts as bash.
- **Ubuntu 24.04:** bubblewrap was installed but blocked by AppArmor, and Dive on Wide only checked whether it
  *existed* — every workbench command would have failed. It now checks that the sandbox really runs; if not, the
  workbench asks before every command and says how to unlock it: `sudo sh werkzeuge/bwrap_freischalten.sh`.
  The CI runs that very script, so the advice cannot silently go stale.
- **Python 3.9:** task factory and distillation crashed (`sys.stdlib_module_names` exists only from 3.10), and the
  oracle's check for files shadowing the standard library saw almost nothing. Now with a complete fallback.

---

## 0.5.0 — first public release

<!-- x: Your local models chat, plan and build together: a swarm of small models with fresh contexts, a workbench that edits and tests real code, your files as knowledge. Nothing leaves your machine unless you say so. -->
Dive on Wide is a local AI workspace: your own models (Ollama, LM Studio, llama.cpp, …) chat, plan, research, and
work together inside real projects — on macOS, Linux and Windows, in pure Python with zero dependencies. Nothing that touches your computer,
network or data is on until you switch it on. It is a solo hobby project, released as it is: experimental, free,
MIT-licensed, no warranty.

**What is in it**
- **Chat that knows your stuff** — a calm start page (explain Dive on Wide · give the orchestrator a goal · work in a
  project · use your knowledge); modes **Chat / Orchestrator / Workbench**, the workbench slides up from the input.
  Drag files and whole folders into **Knowledge** — the chat searches it by itself and shows what it used. Attach
  knowledge or artifacts to a chat; long chats are **condensed** automatically instead of silently cut off.
- **Swarm** — a planner model splits a goal into files, several small Divers each write one with a fresh context;
  the planner merges, checks (compile, tests in the sandbox) and plans the next round. On a 24 GB Mac: a 3-file
  Python project with green tests in 56 s. On one machine the GPU handles the Divers almost one after another —
  the gain is clean contexts, not speed (measured); speed comes with several devices.
- **Orchestrator** — plans a flow from a goal and picks a model per step; uses your knowledge, never picks tiny models
  and **never picks a cloud model by itself**.
- **Any local model server** — Ollama, LM Studio, vLLM, llama.cpp, Jan, KoboldCpp, LocalAI, SGLang, MLX are found on
  your machine. Cloud providers (ChatGPT, Claude, Gemini, Grok, OpenRouter, Ollama Cloud) are possible but never on by
  default, and marked ☁️ with a red warning wherever they appear.
- **Divers** — Dive on Wide's agents. Roles in chat, workbench Divers that act with tools, and Divers that work together
  **over time**: a schedule entry can build on the latest result of others (one researches in the morning, a second
  argues against it in the evening, a third compares both) — it waits for a running predecessor and never starts blind.
- **23 built-in skills** (report, e-mail, meeting minutes, code review, debugging, data analysis, project plan, …),
  and a clear line between prompts, templates, chat Divers (roles) and workbench Divers (that really act).
- **Workbench** — an agent that reads, edits and tests in a real project, in a sandbox without network (macOS/Linux),
  with checkpoints to roll back. **Work through a roadmap**: one run per package, then the project's tests; on red
  it repairs up to three times, otherwise it stops and says why — it never builds on failing tests.
- **Model advice that stays current** — the catalogue knows Qwen 3.8, Gemma 4, Qwen 3.5 …; a 24 GB Mac gets Qwen 3.6
  35B-A3B, with the measured comparison next to it (Qwen 3.8 27B: as good, about 4× slower).
- **Guide (🧭)** — explains Dive on Wide from the real docs and the measured numbers of *your* machine, sees what is
  stuck right now (no model server, recommended model missing …) and puts one-click buttons under its answers
  ("download Qwen 3.6", "switch on code execution", "open the workbench"); recommends models that fit, also for other
  machines ("which model fits 16 GB?").
- **Mesh "Diving Net"** — share compute with your own devices at home, peer-to-peer and encrypted.
- **Learning along (opt-in)** — every Workbench run counts as experience per model; a night shift suggests notes,
  you approve.
- **Discord server chat** — your local models as a private chat per question on your own server; model from a list;
  public mode (chat only) or private mode (Workbench commands for listed users only).
- **GGUF file as a model** — for models that overflow memory under Ollama: `llama-server` starts on demand, frees
  Ollama first, stops when idle.
- **English and German** interface, terminal and installer follow the system language.
- **Uninstall**: `python3 install.py --entfernen` backs up your data first and asks per part.

**Measured, not promised** (details in `docs/ABNAHME.md` and `docs/ANWENDUNGEN.md`)
- DowBench (34 tasks, code/terminal/injection/rules): Qwen 3.6 35B **31/34**, 0 safety violations, Mac mini M4 Pro 24 GB.
- Guide: 26/26 questions with the 35B, 24/24 with a 4B model on an 8 GB CPU-only VM.
- 12-hour load test: 2,054 rounds, 0 errors, no memory leak. 529 automated tests, run on macOS and in Linux and
  Windows VMs; a full run across two instances and an update test from the previous build.

**Honest limits**
- Small machines (8 GB, CPU): works, but slowly (chat 15–90 s); for long runs switch off Ollama's prompt cache
  (`LLAMA_ARG_CACHE_RAM=0`, see `docs/FAQ.md`).
- Windows has no sandbox for agent commands — every command asks first. Training runs on Apple Silicon only.
- Big projects from one idea still fail with local models (game test: first package green, the ice mechanic not).
- Web research without your own search service gets rate-limited.

**Name.** Developed privately as "Dow.OS" (internal versions 0.1016–0.1048, below). Public name: **Dive on Wide**.
Technical names stay for compatibility: the `dowos` terminal command, the `~/DowOS` folder, `DOWOS_*` settings.

---

## Before the public release (internal versions, then called Dow.OS)


## 0.1023 — computer use leaves macOS

Everything platform-specific about screen, mouse and keyboard now sits behind one
interface in `steuerung.py`, with four backends: macOS, Linux/X11, Linux/Wayland,
and "Linux without a display" — which is not a failure state and says so.

**The real defect was not missing code, it was wrong advice.** A Linux user got a
patient explanation that they should run `brew install cliclick`. That is worse
than no message, because it costs time before it fails. Each backend now states
its own tools, its own permission model, and its own fix — `sudo apt install
xdotool` where that is the answer.

**Session type decides the backend, not the kernel.** XWayland sets both
`DISPLAY` and `WAYLAND_DISPLAY`; if X11 won there, `xdotool` would start happily
and have no effect on actual Wayland windows — silently. Wayland wins that tie.

**Honest scope.** The Linux command lines are covered by tests that put fake
`xdotool`/`grim`/`maim` binaries on `PATH` and assert the exact arguments:
`mousemove --sync` before `click --repeat 1`, `--clearmodifiers` on typing (a
stuck Shift would otherwise capitalise everything), `Return` rather than `return`
for the X11 keysym, and `--absolute` on ydotool — without which the motion is
*relative* and the pointer walks away after the first click, while every single
call still looks successful. What those tests cannot do is talk to a real display
server. **This has never run on an actual Linux desktop**, and the UI says so on
the machine rather than only in the README.

**Bug fixed on the way** — seven background runs called `run_finish(…, "done")`
*before* posting their result to the chat. A run therefore reported itself
finished while its output was not yet visible; a UI polling the status would show
a completed run with nothing in it. One test failed roughly one run in five
because of it. Now the result is posted first, so "done" means everything the run
produces is already there — and the test needs no sleep to be reliable.

Also: the test harness copied a hand-maintained list of files into each fresh
instance. Adding `steuerung.py` broke every test with `ModuleNotFoundError`. It
now copies every top-level module, so the next new file does not repeat this.

**The headline was measurably wrong.** It claimed "~4,500 lines of pure Python"
against an actual 11,239, and the test badge said 226 against 236 — on the one
line every visitor checks first. Both corrected, and a test now counts them on
every run, because numbers in a README go stale silently. The pitch also stopped
selling line count (a craft flex, not a reason to pick this over a chat UI) and
now leads with what the thing does that a chat window does not.

---

## 0.1036 — one file that sets the whole thing up

Until now there was `start.sh` — a bash script, useless on Windows — and no
installer at all. Anyone on Windows had to work out for themselves that
`python server.py` is the way in.

`install.py` is **one file of pure standard library** that works on macOS, Linux
and Windows: it detects the system and its package manager, reports every
prerequisite honestly, asks before each step, fetches the newest Dow.OS, and
writes a launcher that fits the platform. `install.sh` and `install.ps1` are
twenty-line bootstraps for people who do not have the file yet — they check for
Python, fetch `install.py`, and hand over.

**It installs nothing without asking, and prints the exact command first.** An
installer that quietly drives package managers and collects system privileges
would contradict everything else in this project. Decline a step and the summary
tells you which capability you are now missing — nothing is final, and rerunning
is free.

**It never pipes network code into a shell.** Ollama's Linux installer gets a
two-step recipe — download, read, then run — because `curl … | sh` executes code
nobody has looked at. A test asserts no recommended command line contains a pipe,
and that the installer itself never uses `shell=True`.

**Running it again is the update**, and it leaves `storage/` and your `.env`
alone. Verified by writing real data, updating, and comparing checksums. The
unpacker also refuses entries that would escape the target directory (zip-slip),
which a test proves by putting such an entry in a zip.

**Distributed compute is part of the setup**, not an afterthought: on request it
builds llama.cpp with `GGML_RPC=ON`, repairs the `@rpath` and re-signs on macOS,
and then *verifies the binaries actually start and know `--rpc`* before claiming
success. Checked end to end afterwards: the freshly installed copy starts and
reports distributed inference ready, because it finds that build on its own.

Two bugs caught by simulating bare machines on all three systems:

* On Linux the screen-control hint read *"macOS then asks for Accessibility"* —
  advice about something that does not exist there. The same class of error
  `steuerung.py` already had once: right tools, wrong explanation. A test now
  asserts no platform's advice mentions another platform's tools.
* `braucht_sudo()` called `os.geteuid`, which does not exist on Windows. Short-
  circuit evaluation hid it, but only as long as nobody reorders the condition —
  and the failure would be an `AttributeError` in the middle of an install.

---

## 0.1035 — the last P1: a real run, and a device joining on camera

**The orchestrator screenshot the checklist asked for, from a real run.** One
goal — "write a short Python program that prints the first 20 primes and explain
the approach" — and the orchestrator wrote its own plan across two *different*
local models: `Coding-Agent [qwen2.5-coder:14b] → Agent [Qwen3.6-35B]`. 170
seconds, one laptop. The generated program was run afterwards: it prints the
first 20 primes correctly.

**A 26-second recording of a second device joining, uncut.** The mesh view goes
from *6 GB across 1 device* to *12 GB across 2* by itself while nobody touches
the browser — the other machine simply started its node. Verified afterwards
that the page had updated without a reload, which is what makes the clip mean
anything.

Smaller than the "60 second demo video" the checklist imagined, and the reason is
worth stating: a guided tour with mouse movement needs a person at the keyboard.
Chrome can only ever be granted read access here, so there is no honest way to
film myself clicking through the UI.

The mesh screenshots were also retaken on mains power, so the numbers are real
now instead of the resource governor's zero.

Two features fell out of the work, both real:

* **Conversations are addressable** — `#chat/<id>`. A reloaded window used to
  land in an empty chat even if you were in the middle of something. The trap
  this hit immediately: `loadSessions()` runs *without* await, so checking
  `state.sessions` at startup checks an empty list and silently opens nothing.
  It now just tries to open it and falls back.
* An empty `/api/chat` is a 400 rather than `{"done": true}`.

---

## 0.1034 — the README was showing four broken images

Checklist item 7 was "take screenshots". The actual state was worse than
missing: the README already **referenced** four screenshots that did not exist.
On GitHub that renders as four broken image icons at the top of the page — the
worst possible first impression, and the exact opposite of what screenshots are
for. Nobody notices while reading the repo locally.

Four real screenshots now exist, taken from a running instance with real
content: a real conversation with a local model, two devices that found each
other, an actual encrypted exchange between them, and the pipeline list. Nothing
staged. The mesh view honestly shows **0 GB** contributed, because that laptop
was on battery and the resource governor refuses to spend someone's battery on
someone else's work — the caption says so rather than hiding it.

**A test now asserts that every image any README references exists and is larger
than a placeholder.** That is the durable half of this fix.

Two improvements fell out of taking the pictures:

* **Views are addressable.** `#netzwerk`, `#bote`, `#pipelines` — every view now
  lives at its own address. Before, every view was the same URL: a reloaded
  window always landed in the chat, a bookmark was worthless, and you could not
  send someone a link to a view. Following the address does not push a new
  history entry, so the back button reaches its target instead of walking one
  step per redraw.
* **Bote opens the only contact instead of asking.** "Choose a contact" with
  exactly one contact is a question without a choice — one extra click before
  every message. With several, the list stays, because then it is a real choice.

Also: an empty `/api/chat` (no messages) returned `{"done": true}` — a caller
error answered as success. It is a 400 now, with the expected shape in the
message.

---

## 0.1033 — a typo should not be a dead end

Found by using the messenger the way a person would, across two devices,
instead of the way the test suite does it.

**A mistyped anchor was unrecoverable.** The contact sits at "offline" forever;
the obvious fix — type it again, correctly, under the same name — was met with
"Es gibt schon einen Kontakt namens 'Chris'." and no hint about what to do next.
A dead end produced by a typo.

The rule is no longer about the name but about whether the connection exists: a
contact that was **never recognised and has no messages** may be corrected —
the new anchor is obviously what was meant, and there is nothing to lose. An
**established** contact is not overwritten, because a different anchor means a
different person, and that is precisely what the anchor protects against. The
refusal now names the way out.

**And a contact stuck at "offline" said nothing about why.** It now explains the
most common cause — both devices must hold *the same* anchor; it is shared, not
exchanged, and creating one each and entering the other's is a wait with no end
— plus what else to check, and the fact that a typo can simply be retyped.

I fell into that trap myself while testing: created an anchor on each device,
entered them crosswise, and watched two contacts stay offline forever.

**One error, two keys.** 30 of the API's error responses called it `error`, 10
called it `fehler`, and the frontend checks sometimes one and sometimes the
other. Whoever checks the wrong one reads a failure as a success — the kind of
defect that surfaces much later. It bit me during this very session: a 404
looked like a successful call. Rather than renaming one key and breaking the
half that currently works, every error response now carries **both**. Verified
by removing the change and watching the new test fail.

---

## 0.1032 — do you trust a stranger's compute node?

The last open item on the mesh roadmap, and the one where the first design was
wrong in a way that would have hurt exactly the people it was meant to protect.

A language model's answer **cannot** be verified. Two honest nodes running the
same model produce different text; a different quantisation is enough.
Majority vote does not work on free text. What *can* be checked is smaller and
still worth having: **whether the node computed at all.** The cheap fraud on a
compute network is not a cleverly wrong answer, it is no answer — return
garbage, collect credit, save electricity.

**The near-miss.** The first design sent tasks with a formatting requirement:
"start with the keyword K1A2B3 and give the result of 1399 + 379."

| | probe passed |
|---|---|
| gemma4:12b (honest) | 5 of 5 |
| **qwen2.5:0.5b (honest)** | **1 of 5** |

That measured **instruction-following**, not honesty. It would have branded
weak-but-honest devices as frauds — precisely the devices a friends' network is
made of. It is now a *capability* test that explicitly does not feed the
verdict, and the UI labels it that way.

**What is checked instead**, all measured against real models: relevance
(does the answer engage with the question), repetition (the same text for
*different* questions means no computation — the sharpest signal available
here), and emptiness.

**The counting method mattered more than the threshold**, and again only
measurement showed it. Comparing whole words failed a flawless answer at 17 %:
"Peer-to-Peer-*Netzen*" in the question against "Peer-to-Peer-*Netze*" in the
answer, plus imperatives like "nenne" and "zwei" that appear in no answer.
Across 12 real answers versus 12 fraud patterns:

| counting | honest | fraud | gap |
|---|---|---|---|
| whole words | 0.40–1.00 | 0.00–0.25 | 0.15 |
| **stem 4, words ≥5 chars** | **0.50–1.00** | **0.00** | **0.50** |

**Benefit of the doubt is the default.** Two complaints are required, and a
single lapse bans nobody — packets get lost, models stumble. A wrongly excluded
honest node costs more than a fraud that slips through: the first one leaves
and does not come back. Unknown nodes are trusted until shown otherwise, and
the records are **RAM-only** — a trust verdict that survives a restart would be
a permanent rating of people.

Nodes marked unreliable stop receiving jobs. Without that the whole ledger
would be decoration: you would know who is not computing and keep asking them
anyway. And `_ergebnis_empfangen` now actually *checks* results — the comment
above `auftrag_verteilen` had called comparison "the only handle against
deliberately false results" while nothing ever compared anything.

**What this does not do:** a node running a *worse* model than advertised
passes everything — it is computing. Not detectable, and not claimed.

Also fixed: the llama.cpp binaries copied to `~/.dowos/llama-rpc/bin` in 0.1031
did not actually run. Their `@rpath` was baked to the build directory, which is
gone; dyld then reports a missing library that is sitting right next to the
binary. The build script now adds `@loader_path`, re-signs, **and verifies the
binary starts from its new home** before claiming success. A build script that
says "done" and leaves something broken is worse than one that fails.

---

## 0.1029 — the bug you never notice yourself

A review pass over the DHT code written the same day turned up a number worth
staring at: a Kademlia answer is **539 bytes**, its question **57** — a factor of
9.5. UDP does not verify the sender. Forge a source address and every Dow.OS
node becomes a willing amplifier pointed at someone else.

This is the nastiest class of defect: **it never shows up in your own
operation**, because the damage lands elsewhere, and every node involved is
behaving perfectly correctly — it is answering a question.

Two limits, the second mattering more than the first: 20 answers per source per
10 seconds, and 300 in total. Without the second, an attacker simply rotates the
forged source address and walks past the first. A node's contribution to such an
attack is now capped at **16 kB/s** regardless of input volume, and a throttled
sender gets *no* reply — "you are being throttled" would itself be a packet sent
to the victim. Measured: 60 forged questions produce 20 answers and 40 refusals.
A genuine lookup asks each peer once or twice and never notices.

Found in the same pass: `nachliefern()` walked *every* stored record, each
costing a full lookup plus up to 20 sends. With a full store (10,000 records) a
node would have set off a packet storm every ten minutes — generating exactly
the load Kademlia exists to avoid. Now 50 addresses per round, **round-robin**,
so everything still gets its turn.

---

## 0.1028 — from a plan to a model you can actually use

0.1026 proved the mechanism from a script. This closes the loop inside the
product: **Plan rechnen → ▶ start → the distributed model appears everywhere
Dow.OS offers models** — chat, agents, skills, pipelines, orchestrator. The
intermediate results come from the other devices; the answer is assembled *only*
on yours, and no other device ever sees the finished text.

Verified live across two Dow.OS instances: a 10/14 layer split over
`192.168.0.100:50091`, started through the API, answering "Hallo! Wie kann ich
Ihnen heute helfen?"

**Model sizes now come from the file, not the name.** "12b" would have been
guessed as 5.0 GB / 32 layers; the GGUF header says 6.87 GB / 48. A plan built
on guessed numbers distributes layers that do not exist. Ollama names are
resolved via `ollama show --modelfile`, because where Ollama keeps its blobs is
Ollama's business and changes.

**Four bugs found by running it, each invisible until then:**

* The compute node bound to `127.0.0.1` while the mesh announced the LAN
  address. The leader aborted with "Failed to connect" — correctly, since
  nothing was listening where it was told to look.
* Then the readiness check still probed `127.0.0.1`, so a node that *was* up
  was reported as "kam nicht hoch". The check has to knock on the door that was
  actually opened.
* Errors were sent to `/dev/null` and then guessed at ("most likely cause: …").
  The real cause was in that output the whole time. Failures now quote what the
  program actually said.
* The distributed model never appeared in the model list: llama.cpp answers
  `/v1/models` in Ollama's shape (`{"models": …}`, not `{"data": …}`), so the
  OpenAI-style listing came back empty. Dow.OS started the model and knows its
  name — it no longer asks.

**A trap worth naming:** switching the compute offer on makes Dow.OS report
"0 GB contributed" immediately afterwards, because the compute node has already
taken that memory (on a Mac it reserves a large Metal working set at once).
Nothing is broken — the memory *is* contributed, it is just in use rather than
on offer. The UI now says that instead of leaving a bare zero.

**And the uncomfortable part, stated rather than buried:** llama.cpp's RPC
interface has **no authentication**. Anyone who can reach the compute node can
use it and crash it. Dow.OS binds it to the single address the mesh announces
rather than to everything — but inside your network it is open. Reasonable in a
Klause among friends; not in a café.

---

## 0.1027 — the switch now starts something

A defect shipped in 0.1022 and only visible once distributed inference actually
worked: toggling "this device may hold layers for others" set a port number and
announced it to the network — **while nothing was listening on that port**. A
neighbour would have built a layer plan on it and connected into the void.

The switch now starts a real `ggml-rpc-server`, waits until the port genuinely
answers (Metal needs a few seconds to compile its kernels, so checking
immediately would be too early), and only *then* announces it. If this machine
cannot hold layers at all — llama.cpp compiled without RPC — it refuses with the
build instructions instead of announcing a port nobody serves.

Leaving the mesh stops the compute node with it. It holds layers *for others*;
leaving the network and letting it keep running would be a process reserving
memory for a network you are no longer in.

The UI shows the real state — `● Rechenknoten läuft` or `● antwortet nicht` —
rather than just the number it was told to display.

---

## 0.1026 — distributed inference, proven

0.1022 built the planning, the announcement and the command construction for
splitting one model across several devices, and said plainly that the actual
computation had never run here, because the installed llama.cpp was compiled
without RPC. That gap is now closed: llama.cpp was built with `-DGGML_RPC=ON`,
two compute nodes were started, and a model was split across them — using
Dow.OS's own plan and Dow.OS's own commands.

| | |
|---|---|
| plan | 8 / 8 / 8 layers over three stages |
| estimated | 90.9 tok/s |
| **measured** | **88.8 tok/s** (40 tokens in 0.45 s) |
| **counter-proof** | kill one compute node → **inference aborts** |

The last row is the only one that proves anything. Without it, llama.cpp could
have silently ignored the RPC nodes and computed everything locally — which was
the first suspicion, since the log never mentioned RPC and the throughput looked
like local work.

**What the numbers do not say:** all three "devices" were the same machine. No
real network latency, no separate memory — the estimate matched that closely
because its network term was effectively zero. Across genuinely separate devices
the figures in ARCHITEKTUR.md §3.1 apply, not these.

**Two traps nobody can guess, both now handled and documented:**

* The compute node is called **`ggml-rpc-server`**, not `rpc-server` — that was
  the old name. Searching for the old one finds nothing, and the natural
  conclusion is that the feature is impossible. That is exactly what happened
  here first.
* On macOS the compute node must be **pinned to Metal** (`-d MTL0`). Otherwise
  it grabs the BLAS backend, which does not implement `RMS_NORM` and aborts
  mid-graph; the leader then reports only "Remote RPC server crashed or returned
  malformed response" — a message that never hints at device selection.
  `verteilt.rechenknoten_aufruf()` sets it automatically.

`werkzeuge/llamacpp_rpc_bauen.sh` fetches, builds and then *verifies* that
`--rpc` is actually present, because a build that silently drops the flag looks
exactly like a successful one.

---

## 0.1025 — a screen the agent can break

Computer use had one structural problem that no amount of confirmation dialogs
fixes: the agent moves *your* pointer on *your* machine. Every safeguard around
it — control off by default, per-action approval, the abort button — is a
handbrake around a problem better not created.

`schirm/` builds a container with a headless X server (Xvfb), `xdotool`, `maim`
and a minimal window manager. The Linux/X11 backend added in 0.1023 drives it
through `docker exec` — same commands, different screen. When that container is
running, Dow.OS uses it **by itself**: the setting defaults to "auto", and that
ordering is deliberate. The other way round — "use the real screen unless
someone switches" — makes the risky option the default, and defaults are what
almost everyone keeps.

The container runs with `--network none`, `--read-only`, `no-new-privileges`,
memory and CPU limits, and **no bind mount** — it sees nothing of the host
filesystem. A test asserts each of those flags on the `docker run` line itself,
and was verified by adding `-v /:/wirt` and watching it fail.

**What a container is not:** a hypervisor. Escaping one puts you on the host —
rarer than a misclick, not impossible. It is a *real* boundary and still a
weaker one than a full VM. Claiming otherwise would be worse than not having the
boundary.

The UI now states, above everything else in the diagnosis, which of the two is
happening: a shield and "the agent clicks in a disposable container", or a
warning and "the agent clicks on your real screen". That single line is the most
important one in that panel.

**Honest testing status:** backend, screen selection, diagnosis and the
isolation flags are tested. **The container itself could not be started here** —
Docker Desktop was not running, and starting it with 2.8 GB of memory free would
have caused exactly the degradation this project otherwise refuses to inflict.

Two of my own test bugs caught along the way: the isolation check searched the
whole script for `" -v "` and tripped over `command -v docker` (a test that
fires on the wrong thing costs trust in every other test), and the screen-mode
setting was briefly added to the catalogue of things that must all start
switched *off* — it is a choice, not a switch, and the first-run test said so.

---

## 0.1024 — the Weite becomes reachable, and records outlive their holders

**Republishing.** Kademlia's least visible and least optional part. A record
lives on the twenty nodes closest to its address — and those nodes leave.
Laptops close, networks change. Without republishing the record is gone in a few
hours while its expiry still says days, and the expiry is the *only* promise this
mesh makes. Every ten minutes a node re-carries what it holds — including
records it merely *stores* for others, because otherwise everything hangs on the
original publisher, who is eventually gone too. Records within two minutes of
expiry are skipped; carrying them would cost searches and packets for something
already practically dead.

**NAT traversal.** The Weite called itself global and was not. A ticket carries
an address, and behind a home router your own address does not exist from
outside — two people on ordinary connections simply could not reach each other.
Two steps, neither needing anyone else's infrastructure:

* **Mirror.** You cannot know your own outside address; the router assigns it.
  So you ask someone you already know: "what address do you see me at?" That is
  what STUN servers do — here any known peer can do it, with no central service.
  Several are asked, and if two answer differently that is not a glitch, it is
  the diagnosis.
* **Nudge.** To reach B, A asks a node R that both know to tell B to knock at
  A's outside address. B's packets may never arrive at A — their purpose is to
  open the return path in *B's* router. A knocks at the same time; one of the
  two directions meets an already-open path. R learns only *that* two nodes want
  to talk, never what about.

Two checks without which this would be an attack tool rather than a feature:
**only known peers may nudge** (otherwise a stranger sends a nudge and helpful
nodes collectively hammer a target of his choosing — an amplifier assembled from
friendly participants), and **only public destination addresses** (a nudge at
`192.168.x.x` would set the mesh on a device inside a victim's home network).

Weite tickets now carry the mirrored outside address when one is known, since
the LAN address appears on no router in the world.

**What this does not do:** behind a *symmetric* NAT the router assigns a
different port per destination, the mirrored address is worthless to a third
party, and the punch fails — nothing to be done there without a relaying server.
Dow.OS detects that case (two neighbours report different ports) and says so,
with the remedy, instead of silently trying.

**Honest testing status:** the mechanics are verified over real UDP sockets —
mirroring, relaying, and both defensive checks. All of it on one machine, so
over `127.0.0.1`. **It has never crossed two actual home routers.**

Two bugs caught while building it, both in code written the same hour: the relay
*discarded* nudges addressed to someone else, which is precisely its job, so no
punch could ever have completed; and a leftover `if False else` in the branch
that picks the target id.

---

## 0.1023c — Kademlia, and a hole in the Weite it exposed

Until now everything was spread by broadcast. In a **Klause** that is exactly
right: twenty devices, one packet, everyone has it. In the **Weite** it is fatal
— a thousand nodes means a thousand packets per publish, and every node keeping
everything anyone ever stored.

`mesh/kademlia.py` replaces that. XOR distance, K = 20 replicas, iterative
lookup. Measured in a simulated world (`Suche` takes its query function from
outside, so it runs without a network):

| nodes | log₂(N) | rounds | queries | share of network | hit rate |
|---:|---:|---:|---:|---:|---:|
| 100 | 6.6 | 3.8 | 21 | 20.7 % | 59/60 |
| 1,000 | 10.0 | 4.7 | 24 | 2.4 % | 60/60 |
| 20,000 | 14.3 | 6.1 | 28 | **0.14 %** | 60/60 |

Two hundred times more nodes cost a third more queries. Also verified over real
UDP sockets: query answered in 367 ms, a dead peer times out in 0.6 s instead of
stalling the search, and a node that received nothing still finds the record.

**Three bugs the measurements found, none of which raised an error:**

* The termination rule stopped once the closest **three** had been asked, and
  found only 28 of 40 targets — a nearer node was still sitting unqueried.
  Kademlia requires all K closest to be asked.
* **The receive side was missing entirely.** Kademlia tables fill mostly by
  being *asked*, not by asking. Without that one line an industrious node learns
  the whole world while the world never learns it: 35 of 40 instead of 40 of 40.
* The bucket-refresh identifier set the deciding bit to 1 instead of *flipping*
  it. Where it was already 1 the identifier landed in an entirely different
  bucket — 240 of 240 samples wrong, silently.

**And a hole this exposed, older than the DHT: two strangers' Weite nodes on the
same LAN found each other over broadcast alone, with no ticket** — while README
and architecture both promised "anyone *with a ticket*". The promise is the
right one: in a café or a university network you share a LAN with strangers, and
their presence is not consent; the broadcast also told everyone on that LAN that
a Weite node was running here. In the Weite only what arrives on the node's
**own port** now counts — known to whoever redeemed a ticket or was passed on by
an already-known node. The Klause keeps broadcast, which is the whole point of a
Klause.

Publishing follows the mode now: broadcast in the Klause, store-at-the-K-closest
in the Weite. And a node receiving a record in the Weite no longer re-broadcasts
it — that re-broadcast *was* the flood Kademlia exists to prevent.

One more, small but nasty: the helper that tells old two-argument receivers from
new three-argument ones first did it by calling with three and catching
`TypeError`. That swallows any `TypeError` raised *inside* the receiver and then
calls it a second time — a real bug would look like an old signature, and the
side effect would run twice. It inspects the signature instead.

---

## 0.1023b — the corner of the screen, and a 150× speedup found while building it

**Menu bar item** (`menueleiste/`, ~250 lines of Swift). `◌` no server, `●`
running, `● 3` running with three devices in the mesh. The menu offers: open
Dow.OS, start or leave the Klause, quit the menu (Dow.OS keeps running). Nothing
else — it is a shell around the same HTTP interface the web UI uses, and if it
could do something the UI cannot, that would be a bug. It talks only to
127.0.0.1 and asks for no permissions of its own.

Swift rather than Python because a status item needs AppKit, and from Python
that means PyObjC — zero dependencies here is a promise, not an ornament.
`swiftc` ships with the Xcode command line tools that macOS already needs for
`python3`. Without it the build script says so and nothing breaks: Dow.OS runs
completely without the menu.

**The first design was unreadable and only a screenshot showed it.** Mesh-on
versus mesh-off was `◉` against `●` — at menu bar size those two glyphs are
indistinguishable. A state indicator whose states look alike is not one. It now
shows the device count, which is both unmistakable and the number you actually
want.

**And the real find: `/api/mesh` took 5.7 seconds**, on a view that polls every 6
seconds. Nothing looked broken because the view still rendered. Two causes, both
visible only with a stopwatch:

* `eigene_adressen()` resolved the machine's own `.local` hostname. On macOS that
  goes through mDNS, and when nothing answers, `getaddrinfo` waits out its full
  five-second timeout. The cheap UDP trick beside it returns the same address in
  a millisecond. Now the cheap path runs first and the name lookup gets a 0.4 s
  budget in a thread — what it does not deliver in time is simply missing.
  `UdpNetz.starten()` called it twice, so starting the mesh cost **10 seconds**
  of pure waiting.
* `_mesh_lokale_modelle()` asked Ollama for the model list once **per provider**,
  where one call serves all of them, and did it on every single request.

Measured end to end: `/api/mesh` 5.7 s → **0.038 s**, mesh start 5.8 s → 0.80 s.
Three tests now guard those numbers, because this is exactly the kind of defect
that returns silently.

Also: `DowOSMenu --pruefen` polls once and prints the port it uses, what the
server answered, and what the menu would show — raw data instead of a guess when
the icon says something unexpected.

---

## 0.1022 — one model across several devices

Splitting a single model over several machines, which is the hardest way to use
a peer network and the one most often asked for.

**What Dow.OS does** (`mesh/verteilt.py`): find out which devices can hold
layers and with how much memory, compute a layer split that matches those
capacities, build the command that leads the chain, and report honestly what is
missing. **What it does not do:** the tensor arithmetic. That is llama.cpp's
RPC backend; pure Python would be orders of magnitude too slow, and the rule
"Python never computes on a tensor itself" stands.

The split follows **capacity, not head count** — a 32 GB device carries more
than an 8 GB one. Splitting evenly would mean pacing the whole chain by the
weakest device. The leader also carries the context memory and therefore gets
fewer layers.

**Off until switched on.** A device holding another model's layers is tied up
for the whole session, so the offer is announced as an `rpc` port in the call
and is 0 by default. Nodes from older versions have no such field and count as
not participating.

**Honest limitation, stated in the UI above the plan rather than below it:** the
llama.cpp build installed on the development machine is compiled **without**
RPC — no `rpc-server`, no `--rpc` in `llama-server`. Planning, diagnosis,
announcement and command construction are tested; the actual multi-device
computation is not. The diagnosis names the missing binary and the build flag
that produces it (`-DGGML_RPC=ON`).

**Bug found while building this** — a known neighbour's record was refreshed in
every field except the compute offer. Because the offer starts switched off,
turning it on mid-session is the *normal* case, not the edge case: nobody would
ever have seen a neighbour volunteer. Same family as two earlier bugs where a
partial update looked correct because every view only reads. The test was
verified by reintroducing the bug and watching it fail.

---

## 0.1016 — the decentralised substrate

The mesh: pure peer-to-peer, no central server, everything in volatile memory.
Two modes that never mix — **Klause** (your own network, found by LAN
multicast) and **Weite** (the open net, entered with one signed ticket).

**Crypto** — Ed25519, X25519 and XChaCha20-Poly1305 implemented against the
standard library alone, verified against the official test vectors of RFC 8439,
RFC 7748, RFC 8032 and the CFRG XChaCha draft. PyNaCl is used automatically
when present; `backend_info()` always states which tier is running and what it
costs. The pure-Python tier is not constant-time and says so.

**Volatile memory** — secrets live in overwritable buffers with `mlock(2)`
attempted, wiped on context exit. Identities get independent randomness per
session and topic, deliberately not derived from a master seed: a master seed
would make every pseudonym linkable the moment it leaked.

**Resource governor** — off by default, three limits with the smallest
winning, instant handback when the owner needs the memory, and a kill switch
that works without network or anyone's agreement.

**Messenger (Bote)** — two people exchange one anchor in person; both devices
then recognise each other with no directory, no account and no phone number.
Rendezvous points go out as blinded marks so observers cannot see which two
devices belong together. Wiping a contact destroys the anchor and drops the
rendezvous point, so the connection stops existing even as an address.

**Forum** — ephemeral threads with a TTL, a fresh pseudonym per thread, local
filters instead of central moderation. Documented honestly: a close command is
a request, the TTL is the guarantee.

**Models** — three ways to run one: locally, on a friend's device, or across
the wider net. Mesh models appear as a provider, so agents, skills, pipelines
and the orchestrator can all use them.

**Tickets** — one signed invitation leads into the whole net; afterwards peers
introduce each other. Compact binary format (405 → 207 characters) and a QR
encoder written from scratch, with a decoder alongside it as proof.

**Onion routing** — no relay ever knows sender and destination together.

**Phone support** — the interface had zero media queries; it now folds into a
drawer, and a manifest plus service worker make it installable to the home
screen. The service worker caches the shell only; everything under `/api/`
returns early, because messages on disk would break the core promise.

### Fixed

- **Startup took 5.1 s.** Autostart ran a proof-of-work at join difficulty on
  the main thread before the first request could be served. Now 149 ms; the
  mesh comes up on its own in the background.
- **Twelve write paths did nothing.** `api(path, opts)` passes its second
  argument to fetch, so handing it payload data made fetch quietly issue a GET.
  Every view looked correct because views only read. Fixed with a helper and a
  test that catches the whole class.
- **Multicast failures were silent.** The broadcast used one socket chosen by
  the default route and swallowed the error; a user saw "no neighbours" and no
  reason. It now tries every interface, keeps the failure per interface, and
  reports it. Loopback is always among them, so two instances on one machine
  find each other with no network at all.
- **The forum accepted an empty thread id** and wrote a record nothing could
  ever read back — it failed validation on read, but there was none on write.

---

## 0.1013–0.1014 — computer use actually works

**A run hung for ten minutes and returned "timed out".** The planner model was
thinking out loud: 618–3292 output tokens for a ~45-token JSON answer, 26–140 s
per step, and a run has up to 15 steps. Asking for pure JSON with thinking
switched off brought a step to about 2 s. Ruled out along the way, so nobody
re-treads it: screenshot resolution is irrelevant, and a `num_predict` cap
makes it worse by truncating the JSON.

**The diagnostic claimed "ready" while nothing could work.** It only checked
that `cliclick` existed on disk. Without the Accessibility permission cliclick
accepts every command and does nothing, silently. Both macOS permissions are
now really probed, and Ollama is asked whether the planner model can see at all.

**"iTerm is in the list and it still says no."** macOS grants Accessibility per
code signature of the *responsible* process, and iTerm2 starts shells under a
separately signed helper. The hint now names the exact path instead of guessing.

---

## 0.1012 — the visual identity

Dow.OS is short for *Dive on Wide*. The logo was vectorised from a
low-resolution photo (ink mask, marching squares, deskew, Bézier fitting), and
every colour in the interface derives from two values out of it. Two themes,
"the surface" and "the deep". All 29 hardcoded colours replaced by role tokens.

---

## 0.1011 — first public-shaped release

MIT licence, `.gitignore` that keeps the chat database and `.env` out, a secret
audit before the first commit, and an English README. Positioning corrected:
a local AI agent workspace, not an operating system.
