# Introduction to Dive on Wide

The starter assistant reads this page when someone asks "What is Dive on Wide?" or "Explain Dive on Wide to me".
It describes every area of the interface. A test checks that no menu item is missing.

## What is Dive on Wide?

Dive on Wide is an AI workspace that runs **on your own computer**. Your models run locally (through Ollama, a GGUF
file or another local server such as LM Studio or vLLM), and your data stays with you. The name means: stay curious
and keep diving deeper into things.

It is more than a chat window. The models get things done:
- plan tasks across several models,
- write, run and check code,
- research the web,
- optionally operate the screen and share compute with your other devices.

Everything that touches your computer, your network or your data is **off by default**. Setup asks once what Dive on
Wide may do. There is no telemetry. Whatever does leave the machine (web search, cloud providers) is listed in the
outbound log.

## The areas

### Chat
Talk to any model. Pick the model and Diver at the top. `/` brings in prompts and knowledge, `@` starts skills, ＋
opens tools (web, orchestrator, deep research, coding Diver, browser). If something in your **knowledge** fits the
question, it is added automatically, and the answer shows what was used.

### Dashboard and inbox
The **dashboard** shows the real state of every part: connection to the models, what is on and what is off, recent
runs and artifacts. The **inbox** collects messages from finished background runs.

### Templates, Divers, prompts, skills
- **Prompts:** saved text, available in chat with `/`. Does nothing by itself.
- **Templates:** forms (e.g. blog optimiser). Fill in the fields, they become a prompt.
- **Chat Divers:** roles with their own system prompt and model, e.g. code expert. They decide who answers — they
  don't read files or run anything.
- **Skills:** multi-step flows that run in the background and deliver an artifact.
- **Workbench Divers** (explorer, reviewer, tester …) really act: they read, write, run commands and check with
  tests. They are listed on the Divers page below the roles.

### Documents & artifacts
Everything runs produce (reports, code, plans) is stored here as a file. View it, download it, or attach it to a chat
again.

### Knowledge
Your own knowledge base, fully offline. Drag files and whole folders in. The chat searches it by itself when a
question fits. The **Second Brain** folder can collect insights from your chats.

### Pipelines and orchestrator
**Pipelines** chain steps: Diver, web research, deep research, coding Diver, browser, image. Each step may use a
different model. The **orchestrator** builds such a flow by itself from a goal in your own words and picks the right
model for each step.

### Swarm
Several Divers build together: a planner splits your goal into files, small Divers each write one with their own fresh
context, the planner merges, checks with tests and sends another round if needed. On one machine this is not faster
than a single model — the gain is small, clean contexts; it gets faster only with several devices. In chat via ＋ →
Swarm.

### Deep research
A research Diver in rounds: plan sub-questions, search the web, summarise, carry the gaps into the next round. The
result is a report with sources.

### Workbench
The Diver for real projects, like Claude Code or Codex, but with your local model. It reads, searches, changes exactly
one spot, runs the tests and only reports "done" once checked. **Work through a roadmap:** a list of packages is done
one after the other, with the tests after each. The workbench is off by default. On macOS and Linux commands run in a
sandbox without network; on Windows it asks before every command. Also in the terminal: `dowos werkbank "…"`.

### Code sandbox
Workspaces with editor, file list and console where the coding Diver runs code.

### Rhythm
Dive on Wide works when nobody is watching: daily at a set time, at an interval, or once. An Diver, skill,
orchestrator or workbench task then runs by itself, e.g. as a morning briefing. An entry can **build on** the
latest result of other entries: one Diver researches in the morning, a second loads it in the evening and argues
against it, a third compares both.

### Network (Diving Net and open net)
Optionally share compute with your own devices. Peer-to-peer, encrypted, no central server, nothing on disk. The
**Diving Net** is your own network at home: devices find each other by themselves. The **open net** is the open network,
entered through an anchor exchanged in person.

### Messenger and forum
The **messenger** sends encrypted messages over the network, with no account and no phone number. The **forum** has
threads that expire by themselves, with a separate pseudonym per thread.

### Models
Which models are installed, which fit your machine and how well they measured here. One click loads a model. Set up
providers (Ollama, LM Studio, vLLM, llama.cpp, a GGUF file, cloud services if you want them) under Settings → Models &
providers.

### Training
Turn good work into your own model. Well-rated runs become a dataset, and a small student model learns from it
(LoRA). Optionally a stronger teacher model solves practice tasks that an independent checker verifies. A cloud
teacher sends those tasks off your machine, and the page says so clearly.

### Settings
Models & providers, code sandbox, outbound (what may leave), research, access for other devices, Discord, Telegram,
Slack, backup and language.

## Help

- **Guide (🧭 bottom right):** answers questions about Dive on Wide from the docs and works out which model fits your
  machine.
- **FAQ:** docs/FAQ.md.
- **Found a bug?** Please report it on GitHub (issues) or on Discord. Dive on Wide is a hobby project in an early
  version, and feedback helps most.

## First steps

1. Check under **Models** that a suitable model is there. If not, the page suggests one.
2. Ask something in **chat**, then let the **orchestrator** solve a goal via ＋.
3. Drag a few files into **knowledge** and ask about them in chat.
4. If you code: switch on the **workbench** and give it a small task in a project.
