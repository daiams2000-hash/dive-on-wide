# Die Werkbank — Dive on Wide als lokales Claude Code

Die Werkbank ist ein Agent, der Aufgaben in echten Projekten selbstständig löst:
Er liest Code, sucht, plant, ändert gezielt, führt Tests aus und meldet sich erst,
wenn die Aufgabe geprüft ist. Er arbeitet mit lokalen Modellen, auf Wunsch aber
mit jedem OpenAI-kompatiblen Anbieter.

Diese Seite erklärt, wie man sie benutzt und was sie schützt.

---

## Loslegen

**In der Oberfläche:** *Werkbank* in der Seitenleiste. Auftrag beschreiben,
*▶ Starten*. Mehrere Aufträge laufen nebeneinander; jede Karte zeigt die
Schritte, wartende Freigaben, *± Änderungen* und *✕ Abbrechen*.

**Im Chat:** `/werkbank Behebe den Fehler in rechnen.py` — oder über
*Werkzeuge → Werkbank-Agent* einen Modus einstellen. Folgenachrichten im selben
Chat setzen den Lauf fort, wie in Claude Code.

**Im Terminal:**

```bash
ln -s ~/DowOS/app/dowos /usr/local/bin/dowos     # einmalig
cd mein-projekt
dowos werkbank "Die Suche ignoriert Umlaute — finde die Ursache und behebe sie"
```

`dowos` braucht keinen laufenden Server. Läufe aus dem Terminal erscheinen in
der Oberfläche.

| Befehl | Was |
|---|---|
| `dowos werkbank "…"` | Aufgabe im aktuellen Ordner lösen |
| `dowos werkbank --plan "…"` | erst planen, Plan freigeben, dann ändern |
| `cat fehler.log \| dowos werkbank "Finde die Ursache"` | Eingabe per Pipe anhängen |
| `dowos fortsetzen "Ja, Kommazahlen"` | letzten Lauf in diesem Ordner fortsetzen |
| `dowos review` · `dowos review --basis main` | Git-Änderungen nur lesend prüfen |
| `dowos checkpunkte` · `dowos zuruecksetzen <id>` | Stände ansehen, zurückgehen |
| `dowos werkbank "/review main"` · `dowos befehle` | eigenen Befehl einsetzen, Befehle auflisten |
| `dowos werkbank --profil reviewer "…"` · `dowos profile` | mit Agentenprofil arbeiten, Profile auflisten |
| `dowos verlauf [lauf]` | Schrittprotokoll ansehen |
| `dowos wiederholen [lauf]` | Lauf ohne Modell nachspielen: liefern die Werkzeuge noch dasselbe? |
| `dowos abzweigen <lauf> <schritt> "…" --zuruecksetzen` | bei Schritt N anders weitermachen |
| `--json` · `--stream-json` | maschinenlesbar für Skripte und CI |

Rückgabewerte: `0` fertig · `1` Fehler · `2` Schrittlimit · `3` Rückfrage an dich.

**Im Editor** (Zed, JetBrains, Neovim mit CodeCompanion … — alles, was das
[Agent Client Protocol](https://agentclientprotocol.com) spricht, wie bei Claude
Code und Codex). In Zed unter `settings.json`:

```json
"agent_servers": {
  "Dive on Wide": {"command": "/Users/ich/DowOS/app/dowos", "args": ["acp"]}
}
```

Der Editor zeigt jeden Schritt, fragt Freigaben selbst ab und bietet die Modi
*Im Projekt schreiben*, *Erst planen*, *Nur lesen* und *Voller Zugriff*. Im
Editor geöffnete Dateien, die du mitschickst, hängen am Auftrag. Folgenachrichten
setzen das Gespräch fort. MCP-Server, die der Editor mitgibt, stehen dem Agenten zur Verfügung
(jeder Aufruf wird gefragt), angehängte Bilder kommen beim Modell an. `dowos acp --freigabe befehle` fragt vor jedem Befehl,
`--modell` wählt ein anderes Modell.

---

## Modell wählen

*Training → Rollen* legt fest, welches Modell was tut. **Werkbank-Agent** ist das
Standardmodell für Aufträge; im Dialog oder mit `--modell` lässt es sich je
Auftrag ändern. Gemessen auf dem Prüfstand (20 Mini-Projekte, versteckte Tests):
Qwen3.6-35B-A3B löst 16–17 von 20.

### Lehrer und Schüler

Unter *Training* wird aus einem starken Modell ein kleines, eigenes:

| Rolle | Aufgabe | Wählbar |
|---|---|---|
| **Lehrer** | entwirft Übungsaufgaben (Aufgabenfabrik) und löst sie | jedes Modell von Ollama oder einem OpenAI-kompatiblen Anbieter |
| **Schüler · Grundmodell** | wird mit den Lösungen des Lehrers trainiert (LoRA) | ein MLX-Modellordner |
| **Schüler · in Dive on Wide** | wird auf dem Prüfstand gemessen und benutzt | jedes Modell, auch ein bereitgestellter Schüler |

**Frühstopp:** Wird der Val-Loss mehrere Prüfungen hintereinander nicht mehr
besser, endet das Training von selbst, und der beste Zwischenstand wird der
Adapter (der letzte bleibt als `adapters_ende.safetensors`). Die Ansicht zeigt,
wie viele Epochen die eingestellten Schritte ergeben — mehr als etwa eine lernt
bei wenigen Beispielen vor allem auswendig.

Jede Rolle lässt sich jederzeit neu besetzen. Ein fertiges Training wird mit
*▶ Als Modell bereitstellen* zu einem Modell wie jedes andere: Dive on Wide startet
das Grundmodell mit dem Adapter auf `127.0.0.1`, und es steht als
„Schüler: …“ in jeder Modellauswahl, bis du es beendest. Während ein Schüler
bereitsteht, startet kein Training — beide brauchen den GPU-Speicher.

*⤓ In Ollama übernehmen* macht daraus ein eigenständiges Ollama-Modell — auch
für andere Geräte im Diving Net: Adapter einbacken, nach GGUF umwandeln
(llama.cpp), quantisieren (Q8_0, Q4_K_M oder F16), `ollama create`.
Zwischendateien werden danach entfernt; es bleibt nur die Kopie in Ollama.

Ist der Lehrer ein Cloud-Anbieter, gehen Aufgaben und Code dorthin. Manche
Anbieter verbieten, mit ihren Antworten Modelle zu trainieren. Der API-Schlüssel
wird an Fabrik und Prüfstand nur über die Umgebung weitergegeben, nie über die
Befehlszeile oder das Lauf-Protokoll.

---

## Rechte und Freigaben

Zwei getrennte Fragen, wie bei Codex:

| Rechte | Dateien | Befehle |
|---|---|---|
| **nur lesen** | lesen und suchen | in der Sandbox, ohne Netz, ohne Schreiben |
| **im Projekt schreiben** (Standard) | nur im Projektordner | in der Sandbox, ohne Netz, Schreiben nur im Projekt |
| **voller Zugriff** | nur im Projektordner | ohne Sandbox, mit Netz |

| Freigabe | Wann gefragt wird |
|---|---|
| **nicht nachfragen** | nie — die Sandbox ist die Grenze |
| **vor jedem Befehl** | vor jedem `ausfuehren` |
| **vor jeder Änderung und jedem Befehl** | auch vor `schreiben`/`ersetzen` |

Die Sandbox ist `sandbox-exec` (macOS) oder `bwrap` (Linux, Paket *bubblewrap*).
**Gibt es keine, wird jeder Befehl einzeln gefragt** — egal, was eingestellt ist.

**Erst planen:** Der Agent darf zunächst nur lesen und legt mit `plan` einen Plan
vor. Erst deine Freigabe gibt ihm die eingestellten Rechte.

---

## Verlauf, Abzweigen, Aufnehmen

Jeder Lauf schreibt, während er arbeitet, ein **Schrittprotokoll** — alles, was
das Modell sah und antwortete, unverdichtet, mit Modellzeit und Kontextgröße je
Schritt (wie das Session-Log von DeepSeek Harness). *🔍 Verlauf* zeigt es.

- **Abzweigen:** Bei jedem Schritt *↳ Ab hier abzweigen* — der Agent übernimmt
  das Gespräch bis dahin und macht mit deiner neuen Anweisung weiter, auf Wunsch
  mit dem Projekt auf dem Stand dieses Schritts. Der ursprüngliche Lauf bleibt.
- **Aufnehmen:** Ein abgebrochener oder abgestürzter Lauf steht unter
  *Unterbrochen* und lässt sich dort fortsetzen, wo er stand.
- **Wiederholen** (`dowos wiederholen`): spielt die aufgezeichneten Antworten
  ohne Modell auf einer Kopie des Startstands nach und meldet jedes Werkzeug, das
  heute etwas anderes liefert — ein Regressionstest für die Werkbank selbst.

---

## Profile und externe Agenten

*🧩 Profile & externe Agenten* — ein Profil schneidet den Agenten auf eine Art
Aufgabe zu: Werkzeuge, Anweisungen, Modell, höchste Rechte. Format wie die
Subagenten von Claude Code; `.claude/agents/` wird mitgelesen, Werkzeugnamen wie
`Read, Grep, Bash` werden übersetzt. Eingebaut: **erkunder**, **reviewer**,
**tester**, **minimal** (vier Werkzeuge — für kleine Modelle).

- Wählbar beim Start (*Profil*), mit `--profil` und im Editor als Modus.
- Der Agent delegiert selbst an Profile: `delegieren {"profil": "tester", …}`.
- **Ein Profil gibt nie mehr Rechte** — es gilt die strengere Einstellung von
  Auftrag und Profil. Ein Profil aus einem fremden Repository schränkt nur ein.

**Externe Agenten:** Ist Claude Code oder Codex installiert und hier
eingeschaltet, kann der Agent Teilaufgaben an sie geben
(`extern {"agent": "claude", …}`). Sie laufen außerhalb der Dive-on-Wide-Sandbox mit
ihren eigenen Schranken (bei „nur lesen“ Plan- bzw. Read-only-Modus) und senden
Projektinhalte an Anthropic bzw. OpenAI. Standardmäßig aus, jeder Aufruf wird
einzeln gefragt, ohne Aufsicht nie.

---

## Sofortige Prüfung nach Änderungen

Nach jedem `ersetzen` oder `schreiben` an einer Python- oder JSON-Datei meldet die
Werkbank einen Syntaxfehler sofort mit Zeile — wie die Diagnosen, die Claude Code
aus der IDE bekommt, nur ohne Sprachserver. Der Code wird dafür übersetzt, nicht
ausgeführt. Eigene Prüfungen (Linter, Formatierer) gehören in den Hook `nach_aenderung`.

---

## Rückgängig

Vor jedem Lauf und nach jedem Schritt, der etwas verändert hat — auch durch
Befehle —, hält Dive on Wide den Stand fest. *± Änderungen* zeigt den Diff seit dem
Start, *↶ Checkpunkte* setzt zurück. Das Zurücksetzen sichert vorher selbst, ist
also umkehrbar. Was `.gitignore` ausschließt (etwa eine Datenbank), wird nie
angefasst. In Git-Projekten übernimmt *Commit* genau die Dateien des Laufs.

---

## Regeln und Hooks

*Werkbank → 🔐 Regeln & Hooks* — wie `permissions` und `hooks` in Claude Code:

```json
{
  "erlauben":  ["ausfuehren(python -m unittest*)", "ausfuehren(pytest*)"],
  "verbieten": ["lesen(.env)", "lesen(*.pem)", "ausfuehren(rm -rf*)", "ausfuehren(git push*)"],
  "hooks": {
    "nach_aenderung": [{"befehl": "ruff format {pfad}", "dateien": "*.py"}],
    "vor_fertig":     [{"befehl": "python -m unittest discover -s tests -t ."}]
  }
}
```

- **verbieten** gewinnt immer und prüft jeden Teil einer Befehlskette.
- **erlauben** spart die Rückfrage — aber nie für Ketten mit `;`, `&&`, `|`, `$(`.
- **nach_aenderung** läuft nach jeder Änderung; Probleme gehen an den Agenten.
- **vor_fertig** muss bestehen, bevor „fertig“ gilt.

Ein Projekt kann eigene Regeln in `.dowos/einstellungen.json` mitbringen. **Sie
gelten erst, wenn du dem Projekt vertraust** — beim ersten Lauf wird gefragt und
nach jeder Änderung der Datei erneut. Ein fremdes Repository kann so keine
Befehle freigeben oder Hooks einschleusen.

---

## Anweisungen, Skills, Gedächtnis

- **`DOWOS.md`** (oder `AGENTS.md`) im Projekt: Regeln für dieses Projekt, bei
  jedem Lauf im Prompt.
- **Skills** im offenen `SKILL.md`-Format — dieselben wie bei Claude Code, Codex
  und Hermes. Projekt: `.dowos/skills/`, `.agents/skills/`, `.claude/skills/`;
  global: *📚 Skills*. Der Agent sieht Name und Beschreibung und lädt einen Skill
  erst, wenn er passt. Aus einem gelungenen Lauf entwirft *✨ Skill* einen neuen.
- **Eigene Befehle** (*📜 Befehle*): wiederkehrende Aufträge als `/name` — dasselbe
  Format wie die Slash-Commands von Claude Code und die Prompts von Codex.
  `$ARGUMENTS` steht für alles hinter dem Befehl, `$1`…`$9` für einzelne Wörter.
  Projekt: `.dowos/befehle/`, `.claude/commands/`, `.agents/commands/`; dazu
  deine aus `~/.claude/commands/` und `~/.codex/prompts/`. Ein Befehl ist nur
  Text: `allowed-tools` gibt keine Rechte, `!befehl` wird nicht ausgeführt.
  Eingebaut sind `/init` (legt eine DOWOS.md für das Projekt an) und
  `/review [branch]` (prüft Änderungen auf echte Fehler); eigene gleichen Namens gehen vor.
- **Gedächtnis:** Mit `merken` notiert sich der Agent dauerhafte Fakten („Tests
  laufen mit make test“), mit `erinnern` durchsucht er frühere Läufe. Beides
  liegt in Dive on Wide, nicht im Projekt; ansehen und bearbeiten unter *🧠 Gedächtnis*.

---

## Mehr Werkzeuge

- **Unteragenten** (`delegieren`): eine Teilaufgabe mit frischem Kontext, zurück
  kommt nur das Ergebnis. Nie mehr Rechte als der Hauptagent. Mit
  `{"auftraege": [...]}` bis zu vier auf einmal — **gleichzeitig**, wenn alle nur
  lesen (etwa mehrere Stellen im Code untersuchen), sonst nacheinander, damit sich
  Änderungen nicht in die Quere kommen. Freigaben werden trotzdem einzeln gefragt.
- **Websuche** (`websuche`, `webseite`): je Auftrag einschalten — Anfragen
  verlassen den Rechner.
- **Bilder**: Screenshot oder Entwurf in die Werkbank einfügen; nur für Modelle
  mit Bildverständnis.
- **MCP**: *🔌 MCP-Server* — lokale Programme (`command`) und entfernte Server
  (`"type": "http"`) im Format von Claude. Jeder Aufruf wird gefragt, außer der
  Server ist als vertraut markiert.

---

## Ohne dich

- **Rhythmus:** *Rhythmus → Werkbank-Agent in einem Projekt* startet Aufträge
  zeitgesteuert. Ohne Aufsicht gilt „im Projekt schreiben“ in der Sandbox; was
  eine Freigabe bräuchte, wird abgelehnt.
  Das Ergebnis kann zusätzlich per Telegram, Discord oder Slack kommen (*Ergebnis zusätzlich zustellen*).
- **Telegram:** *Einstellungen → Telegram* mit einem eigenen Bot. Nachrichten
  beantwortet das Standardmodell, `/werkbank Auftrag` startet einen Lauf,
  Freigaben kommen als Knöpfe. Standardmäßig aus; Chats werden per Einmal-Code
  gekoppelt; alles läuft über die Server von Telegram.
- **Webhooks** (*🪝 Webhooks*): Aufträge von außen — GitHub/Gitea (etwa bei neuen
  Issues), CI, eigene Skripte. Jeder Webhook hat ein eigenes Geheimnis, ohne gültige
  HMAC-SHA256-Signatur passiert nichts (`X-Dowos-Signature` oder GitHubs
  `X-Hub-Signature-256`), dieselbe Zustellung startet nur einmal. Aus dem JSON-Rumpf
  und einer Vorlage (`Behebe: {issue.title}`) entsteht der Auftrag. Unbeaufsichtigt,
  Standard „nur lesen“, voller Zugriff nicht möglich — der Text kommt von außen.
- **Slack:** dasselbe über eine eigene Slack-App im Socket Mode (*Einstellungen → Slack*,
  Bot- und App-Schlüssel). Dive on Wide baut die Verbindung selbst auf — keine öffentliche
  Adresse nötig. Nur Direktnachrichten an die App.
- **Discord:** dasselbe über einen eigenen Discord-Bot (*Einstellungen → Discord*),
  nur in Direktnachrichten — in Server-Kanälen könnte sonst jedes Mitglied mitlesen
  und Knöpfe drücken.

---

## Was die Werkbank (noch) nicht kann

- Schreibende Unteragenten laufen nacheinander — nur lesende arbeiten gleichzeitig.
- Kein „Code-Modus“ wie bei DeepSeek Harness (das Modell schreibt ein Programm, das Werkzeuge
  aufruft) — kleine lokale Modelle kommen mit einzelnen Schritten besser zurecht.
- Keine eigene IDE-Erweiterung (Editoren binden die Werkbank über ACP an), keine Cloud-Sitzungen.
- Das Lesen ist in der Sandbox nicht begrenzt: Ein Befehl kann Dateien lesen,
  die dein Benutzer lesen darf. Mit einem Cloud-Anbieter gelangt deren Ausgabe
  dorthin. Für Projekte nahe an Geheimnissen: lokales Modell oder „vor jedem Befehl“.

Mehr zur Sicherheit: [SECURITY.md](../SECURITY.md).


## Roadmap abarbeiten — nie weiter, solange Tests rot sind

Werkbank → **„📋 Roadmap abarbeiten“**: eine nummerierte Liste von Paketen einfügen (Details eingerückt darunter,
optional „Fertig, wenn: …“). Dive on Wide startet je Paket einen Werkbank-Lauf und danach die Tests des Projekts — den Befehl
aus `Tests: …` in der `DOWOS.md`, sonst `python -m unittest discover -s tests`. Sind sie rot, repariert die Werkbank mit
der Testausgabe (Vorgabe: höchstens dreimal). Bleiben sie rot, **hält die Roadmap an** und nennt die letzte Fehlerzeile —
die nächsten Pakete bauen darauf auf und würden die Fehler nur stapeln. „Fortsetzen“ prüft zuerst die Tests, damit du
zwischendurch selbst reparieren kannst. Ohne Sandbox (Windows) fragt sie einmal, bevor sie die vom Modell geschriebenen
Tests ausführt.

Gemessen mit Qwen 3.6 35B: drei Pakete (Temperatur-Modul, Kelvin, Kommandozeile) grün ohne Reparatur in 2 Minuten.
