# Security

Dive on Wide executes code and can operate your computer. This document states
plainly what it protects, what it does not, and where the boundaries are —
because a security document that only lists features is worse than none.

## Reporting a vulnerability

Open a **private** security advisory on the repository (Security → Report a
vulnerability). If that is unavailable, open a normal issue **without
technical detail** asking for a contact address.

Please do not post working exploits publicly before the fix ships. This is an
alpha project run by very few people; a fix takes days, not hours.

## Threat model in one paragraph

Dive on Wide is designed against a **network attacker** — someone on your LAN, on
the path, or running a hostile node in the mesh. It is **not** designed
against an attacker who already has code execution or physical access on your
machine. If someone can run their own programs as your user, they can read
Dive on Wide's memory, and no amount of encryption inside the process helps.

## What actually protects you

| Boundary | What it does | What it does **not** do |
|---|---|---|
| **Access keys** | Requests from other devices need a key from Settings → Access. Owner and guest roles differ; guests cannot change settings, manage keys or pull a backup. | Nothing on localhost by default. Turn on "require a key on this machine too" if others use your account. |
| **Origin check** | Cross-origin requests from foreign websites are rejected. | Does not protect against a malicious extension in your own browser. |
| **URL scheme allowlist** | Web fetches are http/https only. `file://`, `data:` and friends are refused, so the research feature cannot read local files. | — |
| **SSRF guard** | Access to localhost, private ranges and link-local addresses (including cloud metadata endpoints) is refused, including after redirects. Override with `ALLOW_LOCAL_FETCH=1`. | The override is a real hole. Only set it if you understand it. |
| **Request limits** | Bodies capped at 32 MB, filenames reduced to a harmless core, concurrent model runs capped by `MAX_PARALLEL_RUNS`. | Not a defence against a determined DoS on your LAN. |

## The code sandbox is not a sandbox

The coding agent runs generated code **on your machine, as your user**. It is
**off by default** — the first-run setup asks before it is ever enabled, and
leaving it off is one click. Once on, it is confined to a workspace folder and
a time limit. That is a *working boundary*
so an agent does not wander your disk — it is **not a security boundary**.
Generated code can read your files and reach the network.

- Review generated code before running it.
- For real isolation, install Docker and select "Docker container" in the
  settings. It then runs without network and with memory and CPU limits.
- If you do not need it: turn it off in the settings.

## The workbench agent: a real boundary, with a known gap

The workbench agent (`werkbank.py`, `/werkbank`) works inside a project folder
the way Claude Code does: it reads, searches, edits and runs commands. It is
**owner only** and needs code execution to be switched on.

What the operating system enforces, not the prompt:

| Rights level | File tools | Commands |
|---|---|---|
| `lesen` (read only) | read and search; editing is refused | in the sandbox, **no network, no writes** except a private temp folder |
| `projekt` (default) | read, edit, write — only inside the project | in the sandbox, **no network, writes only inside the project** |
| `voll` (full) | only inside the project | **no sandbox**, network, writes anywhere |

- The sandbox is `sandbox-exec` on macOS and `bwrap` (bubblewrap) on Linux.
  Tests assert that writing next to the project and opening a network
  connection fail. **Where no sandbox exists (Windows, Linux without
  bubblewrap), every command must be approved individually**, whatever the
  approval setting says — and the dialog states that the command runs unconfined.
- File tools resolve symlinks and refuse anything outside the project, and
  never write into `.git`.
- Commands get a minimal environment. The server's own variables (which can
  hold API keys) are not passed on.
- Approvals use the same gate as computer use: a timeout counts as refusal.

**Rules and hooks.** Global rules (Werkbank → Regeln & Hooks) can forbid actions
(`lesen(.env)` also hides the file from `suchen`) and skip approval for exact
commands; an allow rule never matches a command chain (`;`, `&&`, `|`, `$(`).
A project may ship `.dowos/einstellungen.json` — it only takes effect after you
trust that folder, and again after every change to the file, because a cloned
repository could otherwise approve commands or smuggle in hooks. Forbidding
`lesen(.env)` is a guard for the agent's own tools, not a wall: a command can
still `cat` the file unless you forbid that too.

**Messenger bots** (Telegram, Discord) are described below; workbench tasks from them
run with rights level `projekt` and ask before every command.

**MCP servers are outside the sandbox.** A server configured under MCP is a program
Dive on Wide starts with your rights. The workbench asks before every call unless you
marked that server as trusted; read-only runs cannot call MCP tools at all. Values
in `env` (often tokens) are stored in `storage/mcp.json` with mode 600 and are
never returned by the API.

**The gap: reading is not confined.** A sandboxed command can still *read*
files your user can read, for example `~/.ssh`. It cannot send them anywhere
by itself — but its output goes to the model. With a local model that stays on
your machine; **with a cloud provider, whatever a command prints reaches that
provider.** Use a local model for projects near secrets, or set approval to
"before every command".

**Checkpoints** (`checkpunkte.py`) record the project before every run and after
every step that changed files. A reset first saves the current state. Files
excluded by `.gitignore`/`.dowosignore`, `.git`, symlinks and files over 10 MB
are neither saved nor touched — so a reset can never roll back a live database,
but changes to those files cannot be undone through Dive on Wide either. Projects with
more than 20,000 files get no checkpoints; the agent then refuses to run unless
the project is a Git repository.

Every run is stored locally under `storage/werkbank/` (task, steps, command
output) so it can later become training data. It is never sent anywhere.

## Computer use is genuinely dangerous

`/steuern` moves your real mouse and types on your real keyboard. Protections,
all of which can be weakened by you:

- **Off by default.** Enabling it is a deliberate act.
- **Confirmation gate.** Every modifying action pauses and asks. Can be turned
  off; we recommend against it. **Measured, not assumed:** in a test with real
  Dive on Wide prompts, a Gemma-4 base model and an uncensored variant both typed
  passwords and card numbers when the user's request contained them — despite
  the planner rule below. The gate is the protection that actually holds.
- **Kill switch** between steps and while waiting. A timeout counts as refusal.
- **Owner only.** Guests can neither start it nor approve an action.
- **No secrets.** The planner is instructed never to type passwords or card
  numbers, and permitted keys are limited to an allowlist. This is a *prompt*
  instruction — treat it as a seatbelt, not a wall.

### Give the agent its own screen

Every protection above is a handbrake around a problem better not created: the
agent moving *your* pointer on *your* machine. `schirm/bauen.sh` builds a
container with a headless X server, and Dive on Wide uses it automatically whenever it
is running. The container has **no network**, **no bind mount** (it sees nothing
of your filesystem), a read-only image, `no-new-privileges`, and memory and CPU
limits. What goes wrong in there goes wrong in something disposable.

**A container is not a hypervisor.** Escaping one puts the escapee on your host.
That is rarer than a misclick — which is the threat this actually addresses —
but it is not impossible, and a container is a weaker boundary than a full VM.
We say so rather than overselling it.

The diagnosis panel states which of the two is in effect, above everything else
it shows. If it says the agent clicks on your real screen, it does.

Without that container, do not run computer use on a machine where a wrong click
would be expensive.

## The mesh

- **Content encryption** is XChaCha20-Poly1305 with X25519 key agreement and
  Ed25519 signatures, verified against the RFC test vectors (see the `mesh`
  test group).
- **Forward secrecy through a double ratchet.** Every message gets its own
  key, and the key chain moves on with each reply that carries a fresh X25519
  key. A captured message key opens exactly that message; a captured chain
  state stops working as soon as the other side answers, so a conversation
  heals itself. Because the transport is UDP, skipped message keys are kept
  (bounded, or it would be an attack surface) so that loss and reordering do
  not break the chain permanently.
- **Pure-Python crypto is not constant-time.** With PyNaCl installed the
  library does the work; without it, the fallback is sound against a network
  attacker and unsound against one measuring on the same machine.
  `backend_info()` always says which tier is running.
- **Metadata.** Rendezvous points go out as blinded marks, so observers cannot
  see which two devices belong together. IP addresses are still visible to
  anyone on the path; onion hops help only as far as the network is large.
- **"Deleted" is a request, not a guarantee.** A node that wants to keep a
  forum post keeps it. What the architecture guarantees is the TTL.

## UDP amplification, and what bounds it

The mesh answers questions over UDP, and UDP does not verify the sender. A
Kademlia answer is 539 bytes against a 57-byte question — forge a source address
and a node becomes a 9.5× amplifier aimed at someone else. Every participating
node behaves correctly; that is what makes this class of bug invisible from the
inside.

Bounded by two limits: 20 answers per source address per 10 seconds, and 300 in
total over the same window — the second is what stops an attacker from simply
rotating forged addresses. A node's contribution to such an attack is capped at
roughly 16 kB/s no matter how much is thrown at it. Throttled senders receive
nothing at all, since an error reply would itself be a packet delivered to the
victim.

This bounds the damage; it does not eliminate the property. Any UDP service that
answers unauthenticated queries has it.

## Distributed inference opens an unauthenticated port

Splitting a model across devices uses llama.cpp's RPC backend, and **that
interface has no authentication of any kind**. Anyone who can reach the compute
node's port can use it, occupy its memory, and crash it. This is llama.cpp's
own design and documented as such upstream; Dive on Wide cannot fix it from the
outside.

What Dive on Wide does about it:

- The offer is **off by default**, and switching it on starts exactly one
  process on one port.
- The node binds to **the single address the mesh announces**, not `0.0.0.0` —
  the smallest surface that still lets other devices reach it.
- Leaving the mesh stops the node.

What that still means: **inside your own network the port is open.** In a
Klause among people you know, that is a reasonable trade. On a café or campus
network it is not — do not switch the compute offer on there.

## A deployed student model is a local, unauthenticated server

*Training → Deploy as model* starts `mlx_lm server` for a finished adapter.
That server has no authentication either. Dive on Wide binds it to **127.0.0.1
only**, starts it only on an explicit click, and lists it as a provider only
while the process lives. Any program running under your user account can still
talk to it while it runs — end it when you are done.

When a teacher or test model comes from a cloud provider, its API key is handed
to the task factory and the benchmark **through the child's environment**, never
on the command line (visible in `ps`) or in the stored run record.

## Agent profiles and external agents

**Profiles** (`.dowos/agenten/`, `.claude/agents/`, Dive on Wide, built-in) restrict
tools and add instructions. Their rights level and approval setting act as a
ceiling: the stricter of run and profile wins, so a profile shipped by a cloned
repository can only narrow what the agent may do — though its instructions are
text the model reads, like `DOWOS.md`.

**External agents** (Claude Code, Codex) are **off by default**
(`WERKBANK_EXTERN`). When enabled, every single call needs approval — regardless
of the approval setting — and unattended runs never call them. They run
**outside the Dive on Wide sandbox** with their own permission systems (plan /
read-only mode for read-only runs, accept-edits / workspace-write otherwise;
never their bypass modes), inherit your user's environment and credentials, and
send the task and project content to Anthropic or OpenAI.

**Step logs** (`storage/werkbank/<run>.schritte.jsonl`) contain everything the
model saw, including command output. They stay local like the run records.

## Webhooks

`/api/hooks/<id>` is the one endpoint reachable without an access key. It does
nothing unless the body carries a valid HMAC-SHA256 signature made with that
webhook's secret (constant-time comparison; an unknown id answers exactly like a
bad signature). The secret is shown once and never returned by the API. A
delivery id starts at most one run. Runs are unattended (anything needing
approval is refused), default to read-only and can never get full access.

The task text comes from outside and may contain instructions that are not
yours — a classic prompt-injection path. Give write rights only to sources you
trust, and remember that Dive on Wide must be reachable from the sender.

## Distillation with teacher models

Teacher calls send tasks, code and solutions to the teacher's provider. API keys
reach the distillation process only through its environment and are never
written to the job folder. Before the first job with an external provider the
owner has to confirm that provider's terms of use; many providers forbid using
outputs to build competing models.

Code written by a teacher is untrusted: it runs only in the sandbox. The
**oracle** (`orakel.py`) evaluates a fresh copy with tests from the task store,
isolated Python and the project path behind the standard library, and reports
test tampering and import hijacking. Its limit: tested code and tests share one
process, so code written specifically to subvert the test runner could forge a
result. The prüfstand now uses the same oracle — previously hidden tests ran
outside the sandbox in the agent's working folder.

The budget is enforced before each call (worst case reserved), so a job cannot
overspend unless a provider bills differently from the token counts it reports.

## Messenger bots (Telegram, Discord, Slack)

All of them are **off by default** and use your own bot token. Only chats paired with a
one-time code shown in the Dive on Wide UI (10 minutes, 5 attempts) are served;
approval buttons only work for runs that chat started. The Discord and Slack bots answer
**direct messages only** — in a server channel every member could use a paired
channel and press its buttons. Everything, including workbench results, passes
through the messenger's servers.

## Keys and secrets

- API keys live in the settings table of `storage/dowos.db`. They are stored
  masked in the UI and never sent to the client.
- `storage/` is git-ignored, and so is `.env`. The release script refuses to
  build if either would end up in a package.
- Mesh identities and contact anchors exist **only in RAM**. `mlock(2)` is
  attempted so they are not swapped to disk. Python cannot guarantee erasure:
  immutable `bytes` copies, garbage collection, hibernation and crash dumps
  are outside our reach.

## Network exposure

The server binds `0.0.0.0` so you can reach it from a tablet on the same
Wi-Fi. That means **anyone on your network can reach the port**. Access keys
are what stands between them and your data. On an untrusted network (hotel,
conference, café), do not run Dive on Wide, or bind it to localhost only.
