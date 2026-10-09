# Dive on Wide — lokaler KI-Arbeitsplatz

> **Experimentelle Software (Alpha). Nutzung auf eigene Gefahr** — ohne jede Gewährleistung,
> siehe [LICENSE](LICENSE). Dive on Wide kann Code ausführen, Maus und Tastatur steuern und ins Netz
> gehen, sobald du das einschaltest; lies vorher, was jeder Schalter tut.

**Dive on Wide** heißt: neugierig bleiben und immer tiefer in eine Sache hineintauchen. Das ist der Name, das Logo und das Designsystem.

**Kein weiteres Chatfenster.** Ein Arbeitsplatz, auf dem deine lokalen Modelle
wirklich etwas tun: eine Aufgabe über mehrere Modelle hinweg planen, Code
schreiben und ausführen, im Netz recherchieren, deinen echten Bildschirm
bedienen — und auf Wunsch Arbeitsspeicher mit deinen eigenen Geräten bündeln,
über ein verschlüsseltes Peer-to-Peer-Netz, das nichts auf die Platte schreibt.

**23.982 Zeilen reine Python-Standardbibliothek und eine einzige JS-Datei.**
Kein pip, kein npm, kein Docker, kein API-Schlüssel, null Abhängigkeiten —
`python3 server.py`, und es läuft. Alles, was deinen Rechner, dein Netz oder
deine Daten anfasst, ist **ausgeschaltet ausgeliefert**, und jede Diagnose sagt,
was wirklich fehlt, statt lautlos zu scheitern.

<p align="center">
  <img src="docs/bilder/orchestrator.png" alt="Orchestrator-Modus: ein Ziel, ein Plan über zwei lokale Modelle, lauffähiger Code mit Erklärung" width="80%">
</p>

<sub><b>Ein Ziel, zwei Modelle, lauffähiger Code.</b> Geplant von Qwen 3.6 35B-A3B, geschrieben und ausgeführt von qwen2.5-coder 14B, erklärt wieder vom großen Modell — 70 Sekunden auf einem MacBook mit 24 GB. Der Schwarm unten brauchte 36 Sekunden für drei Dateien und vier bestandene Tests.</sub>

<p align="center">
  <img src="docs/bilder/start.png" alt="Startseite: Dive on Wide erklären lassen, dem Orchestrator ein Ziel geben, im Projekt arbeiten, mit Wissen" width="49%">
  <img src="docs/bilder/wissen.png" alt="Der Chat findet abgelegte Dateien selbst und nennt sie unter der Antwort" width="49%">
  <br>
  <img src="docs/bilder/schwarm.png" alt="Schwarm: ein Planer und drei kleine Diver bauen ein Python-Projekt in Runden, bis die Tests grün sind" width="49%">
  <img src="docs/bilder/lotse.png" alt="Der Lotse antwortet aus der Doku und den gemessenen Zahlen dieses Rechners, mit Knöpfen, die es erledigen" width="49%">
</p>

<sub>Echte Aufnahmen einer laufenden Instanz — nichts nachgestellt. Oben links die Startseite. Oben rechts: drei Dateien
ins Wissen gezogen, der Chat findet sie selbst und sagt, welche er genutzt hat. Unten links: ein Schwarm aus drei kleinen
Modellen baut ein Python-Projekt mit Tests. Unten rechts: der Lotse (🧭).</sub>

## Schnellstart

Ein Paket unter **Releases** laden (oder den Code als ZIP), entpacken, dann:

- **macOS:** Doppelklick auf `Dive on Wide starten.command` (beim ersten Mal blockt macOS Dateien aus dem Internet: Rechtsklick → Öffnen → Öffnen)
- **Windows:** Doppelklick auf `Dive on Wide starten.cmd`
- **Linux:** `./start.sh` (oder `python3 server.py`)

Die App öffnet sich im Browser unter `http://localhost:3000`. Nötig sind Python 3.9+ und für lokale Modelle
[Ollama](https://ollama.com). Die Datei `dowos` ist das Terminal-Werkzeug für Skripte und Agenten, nicht der Starter.

## Einrichten — eine Datei, drei Systeme

```bash
python3 install.py            # einrichten oder aktualisieren
```

Auf **macOS und Linux** geht auch `sh install.sh`, auf **Windows**
`powershell -ExecutionPolicy Bypass -File install.ps1`. Beide tun fast nichts:
Sie prüfen Python, holen `install.py` und übergeben. Der Installer selbst ist
**eine Datei aus reiner Standardbibliothek** — man kann sie lesen, bevor man
sie ausführt.

| | |
|---|---|
| `python3 install.py` | einrichten, oder eine vorhandene Installation aktualisieren |
| `--pruefen` | nur nachsehen, was da ist und was fehlt — nichts anfassen |
| `--alles` | auch das Optionale anbieten (verteiltes Rechnen, Bildschirmsteuerung) |
| `--von paket.zip` | aus einem lokalen Paket statt aus dem Netz |
| `--ziel ORDNER` | wohin (Vorgabe: `~/DowOS`) |

**Er installiert nichts ungefragt.** Vor jedem Schritt steht, welcher Befehl
gleich ausgeführt wird — im Klartext, zum Mitlesen. Wer nein sagt, bekommt am
Ende gesagt, welche Fähigkeit ohne diesen Schritt fehlt. Ein Installer, der im
Hintergrund Paketmanager anwirft und Systemrechte einsammelt, wäre das Gegenteil
dessen, was Dive on Wide verspricht.

**Er lädt nirgends Code blind aus dem Netz in eine Shell.** Für Ollama unter
Linux gibt es deshalb einen Hinweis in zwei Schritten — herunterladen, lesen,
dann ausführen. `curl … | sh` führt Code aus, den niemand gesehen hat.

**Ein zweiter Lauf ist das Update.** Er holt die neueste Fassung und lässt
`storage/` und deine `.env` unangetastet — ein Update, das die eigenen Chats
wegräumt, wäre kein Update.

Was er einrichtet: Python 3 (prüft nur — ein laufendes Python kann sich nicht
selbst ersetzen), **Ollama** für die Modelle, **git** und **cmake**, auf Wunsch
**llama.cpp mit RPC** für verteiltes Rechnen (wird gebaut, gut zehn Minuten) und
die **Bildschirmsteuerung** (`cliclick` bzw. `xdotool`+`maim`).

### Danach

```bash
~/DowOS/dowos                       # macOS und Linux
```
Auf Windows: **„Dive on Wide starten.cmd"** doppelklicken. Dann
**http://localhost:3000**.

### Entfernen

```bash
python3 install.py --entfernen          # Windows: py install.py --entfernen
```

Vorher Dive on Wide beenden. Der Installer sichert zuerst deine Daten (`storage/`, `.env`) als Zip neben die
Installation und fragt dann jedes Teil einzeln ab: den Installationsordner, `~/.dowos` (selbst gebautes
llama.cpp), `~/.dowos-browserprofil`. Was keine Dive-on-Wide-Installation ist (dein Home-Ordner, eine
Git-Arbeitskopie), fasst er nicht an. Bewusst bleiben: Programme, die mit deiner Zustimmung installiert
wurden (Ollama, git, …), und deine Ollama-Modelle. Autostart-Einträge legt Dive on Wide keine an.

### Ehrlich zum Erprobungsstand

Entwickelt und gemessen auf **macOS** (Apple Silicon). Seitdem:

- **Linux** — erster echter Lauf in einer Ubuntu-26.04-VM (27.09.2026):
  Installer, Einrichtung, bubblewrap-Sandbox, ein Verbraucherprojekt. Einzelheiten
  in [`docs/ABNAHME.md`](docs/ABNAHME.md).
- **Windows** — erster echter Lauf in einer Windows-11-Pro-VM (25H2, ARM64,
  deutsch, 29.09.2026): Die ganze Testsuite läuft (492 Tests, 7 mit Grund
  übersprungen), Einrichtung über die echte Oberfläche, Chat, Code-Sandbox und
  der Werkbank-Agent an einem Projekt in einem Ordner mit Umlaut. Der Lauf fand
  eine Reihe reiner Windows-Fehler und behob sie — darunter einen, bei dem unter
  Windows **keine Pfadregel griff** ([`docs/SICHERHEIT.md`](docs/SICHERHEIT.md),
  Befund 13). Nicht geprüft: ein Rechner mit Nvidia/CUDA, x64-Hardware (die VM
  ist ARM64).

**Windows, ehrlich:** Für die Befehle des Agenten gibt es dort keine eingebaute
Sandbox — die Werkbank lässt deshalb **jeden** Befehl einzeln freigeben. Prüfstand,
DowBench und Destillation (sie führen fremden Code nur in einer Sandbox aus) und
das Training (nur Apple Silicon) gibt es dort nicht. Mehr Schutz: Dive on Wide in WSL2
betreiben ([`docs/VM_BETRIEB.md`](docs/VM_BETRIEB.md)) — geschrieben, **noch nicht erprobt**: WSL2 braucht
verschachtelte Virtualisierung, die die Test-VM auf dem Mac nicht bietet; dafür braucht es einen echten Windows-PC.


## Weitergabe an andere ("Easy to Share")

Einfach den kompletten `Dive on Wide`-Ordner kopieren/zippen und weitergeben. Der Empfänger muss nur:

1. Ollama installieren und ein Modell laden (`ollama pull llama3`)
2. Falls nötig in der `.env` die Adresse anpassen: `OLLAMA_BASE_URL=http://localhost:11434`
3. `./start.sh` ausführen

Alle Einstellungen lassen sich alternativ direkt in der UI unter **Einstellungen** ändern (inkl. Verbindungstest).

## Funktionen

> **Diver:** So heißen die Agenten in Dive on Wide — die Rollen im Chat ebenso wie die Werkbank-Diver, die mit Werkzeugen in echten Projekten arbeiten. Das eigene Netz heißt **Diving Net**.

| Bereich | Beschreibung |
|---|---|
| **🔗 Multi-Modell-Workflows** | **Jeder Baustein kann sein eigenes Modell bei seinem eigenen Provider nutzen** — manuell im Drag-&-Drop-Builder wählbar, oder der **Orchestrator entscheidet selbst**, welches Modell welchen Schritt macht (z. B. Recherche mit einem schnellen Modell → Code mit einem Coder-Modell → Synthese mit dem stärksten). Rangfolge: Baustein-Modell → Agenten-Modell → Pipeline-Override → Standard. Im Protokoll steht bei jedem Schritt, **welches Modell** ihn ausgeführt hat und **warum** es gewählt wurde. |
| **🧩 Modelle & Provider (Multi-Provider)** | Verbinde beliebig viele LLM-Quellen gleichzeitig: **Ollama** (nativ) und alles **OpenAI-kompatible** — mlx_vlm, vLLM, LM Studio, llama.cpp-Server, OpenAI, OpenRouter … Ein einziger zusätzlicher Adapter deckt sie alle ab. Alle Modelle stehen überall zur Wahl (Chat, Agenten, Skills, Pipelines, Orchestrator), im Dropdown nach Provider gruppiert. Modelle werden intern als `providerid@@modell` referenziert; reine Namen laufen rückwärtskompatibel über den Standard-Provider. API-Keys maskiert gespeichert. Verwaltung + Verbindungstest unter Einstellungen → Modelle & Provider. |
| **Chat** | Streaming-Chat mit Modellauswahl (providerübergreifend), Agenten-Auswahl, Markdown- und Code-Rendering. Inline-Trigger: `@` für Skills, `/` für Prompts & Wissen. |
| **Inbox** | Benachrichtigungen über abgeschlossene Hintergrund-Skills und Systemhinweise. |
| **Templates** | Kachel-Bibliothek mit Eingabemasken (Ad-Copy-Optimierer, Blog-Optimierer, ICP-Builder u. v. m.). Variablen werden in den Prompt injiziert und direkt ausgeführt. |
| **Agenten** | KI-Personas mit eigenem System-Prompt und wählbarem Modell. Keine Paywall — alles frei nutzbar. |
| **Dokumente & Artefakte** | Generierte Dateien werden physisch unter `storage/artifacts/` gespeichert (Ansehen, Download, Löschen). |
| **Prompts** | Wiederverwendbare Prompt-Bibliothek, im Chat per `/` abrufbar. |
| **Skills** | Mehrstufige Pipelines (verkettete System-Prompts), laufen im Hintergrund → Ergebnis als Artefakt + Inbox-Meldung. Per `@trigger` im Chat startbar. |
| **Wissen** | Lokale Wissensbasis, komplett offline. Der Chat sucht vor jeder Antwort selbst darin und nennt unter der Antwort, was er genutzt hat; per `/` lässt sich ein Eintrag auch gezielt anhängen. |
| **Second Brain** | Ordner in der Wissensbasis, der automatisch alle Chatverläufe als destilliertes Wissen eingliedert (nach jeder Antwort; abschaltbar). Der **Evolver** verdichtet alle Einträge zu einem Wissenskern, der mit jeder Ausführung klüger wird. |
| **Pipelines** | Mehrere Agenten zu einem Workflow verketten: Jeder Agent bearbeitet das Ergebnis des vorherigen (z. B. Konzept entwerfen → prüfen → finalisieren). Ergebnis mit allen Zwischenschritten als Artefakt. |
| **Deep Research** | Recherche-Agent mit Schleifen: Teilfragen planen → pro Runde Web-Recherche (WebBridge) + Freier KI-Modus (modell-internes Wissen) → Synthese → Lücken in die nächste Runde. Finaler Bericht als Artefakt, Fortschritt in der Inbox. |
| **WebBridge** | **Pluggbare Web-Recherche nach dem Zwei-Schritt-Prinzip** (Suche ⇢ Auslesen). Der Standard läuft schlüssellos und sofort (**Mojeek → DuckDuckGo → Wikipedia**), für stabile aktuelle Treffer schaltest du unter Einstellungen ein professionelles Backend frei: **Suche** über Tavily · Serper · Brave (API-Key) oder eine eigene **SearXNG**-Instanz (JSON), **Auslesen** über **Firecrawl** oder **Jina** (sauberes Markdown statt HTML-Wüste). Fällt ein Backend aus, greift automatisch der keyless-Standard. **Anfrage-Destillation:** Aus einer natürlichsprachigen Frage werden mehrere Suchvarianten gebildet (Eigennamen wie „Kimi K3" zuerst) und die erste mit echten Treffern genommen. **Erdung gegen Halluzination:** Das Modell wird gezwungen, **ausschließlich** die gefundenen Quellen zu nutzen und sie zu nennen; findet die Suche nichts, sagt es das ehrlich, statt zu erfinden. Gilt für Chat, jeden Agenten, den Web-Baustein in Pipelines und den Orchestrator. API-Keys werden maskiert gespeichert und nie an den Client ausgeliefert. |
| **Browser-Agent** | Echte Browser-Automation über browser-use: Der Agent sucht, öffnet Seiten, liest sie und wertet aus — gesteuert von deinem lokalen Modell. Nutzbar im Chat per `/browser`, als Werkzeug (＋ Menü) und als Pipeline-Baustein 🖥. Kann **dein laufendes Chrome übernehmen** (mit Anmeldungen und Erweiterungen) oder einen eigenen Browser starten. Die Diagnose unter Deep Research zeigt genau, was noch fehlt. |
| **⚗️ KI-Ersteller** | Skills und Agenten aus einer natürlichsprachigen Beschreibung generieren lassen (Skill-Fabrik-Prinzip) — das lokale Modell entwirft Name, Trigger, System-Prompts und Pipeline-Schritte automatisch. |
| **Backup** | Kompletter Speicher (Datenbank + Artefakte + Workspaces) als ZIP-Download in den Einstellungen. Wiederherstellen: ZIP nach `storage/` entpacken. |
| **Automation im Chat** | Alles direkt aus der Eingabezeile: Werkzeugmenü (＋), Slash-Befehle `/orchestrator`, `/research`, `/pipeline`, `/code`, `/agent`, `/browser`, `/zeig`, `/steuern`, `/web`, sowie `@skill`. Der aktive Modus wird über der Eingabe als Chip angezeigt, der Fortschritt läuft als Live-Karte im Verlauf mit. |
| **Pipeline-Baukasten** | **Drag-&-Drop-Builder**: Bausteine aus der Palette in den Ablauf ziehen, per Griff umsortieren, live den Fluss sehen. Neun Typen: 🤖 Agent, ⚡ Skill, 💬 Prompt, 🌐 Web-Recherche, 🔭 Deep Research, 🧰 Coding-Agent, 🎨 Bild, 🖥 Browser, 🤔 Denkschritt. Jeder Schritt erhält das Ergebnis des vorherigen. Ergebnis landet im Chat und als Artefakt. |
| **🐝 Schwarm** | Ein Planer-Modell zerlegt ein Ziel in Dateien, mehrere kleine Diver (z. B. Qwen 3.5 4B) schreiben je eine Datei mit **eigenem, frischem Kontext** — nur ihre Aufgabe und die Dateien, die sie brauchen; der Planer führt zusammen, prüft (Kompilieren, Tests in der Sandbox) und plant die nächste Runde. Gemessen auf einem Mac mit 24 GB: kleines Python-Projekt (3 Dateien, Tests grün) in 56 s. **Ehrlich:** Auf einem einzigen Rechner arbeitet die Grafikkarte die Diver praktisch nacheinander ab (gemessen: drei verschiedene Modelle gleichzeitig nur 15 % schneller, dasselbe Modell gar nicht) — der Gewinn dort sind kleine, saubere Kontexte, nicht Tempo. Schneller wird es erst verteilt auf mehrere Geräte. |
| **🎼 Orchestrator** | Beschreib ein Ziel in eigenen Worten (`/orchestrator …` oder ＋-Menü) — der Orchestrator plant selbst einen Ablauf aus den Bausteinen, besetzt die Rollen spontan, führt alles über die Pipeline-Maschinerie aus und gibt das Ergebnis als Antwort im Chat zurück. Der Dirigent über allen Werkzeugen. |
| **Dashboard** | Startseite mit dem Zustand aller Systeme: Ollama-Verbindung, Fähigkeiten, Zählerstände, letzte Ereignisse und Artefakte. |
| **Code-Sandbox** | Echte Arbeitsumgebung für den Coding-Agenten: Workspaces unter `storage/workspaces/`, Editor, Dateiliste, Konsole, venv mit Paketinstallation, Shell-Befehle. Ausführung mit Zeitlimit und Workspace-Bindung; optional komplett in Docker ohne Netzwerk. |
| **Qualitätssicherung** | Eigene Rollen für den zweiten Blick: **Test-Ingenieur** (Randfälle und Testcode), **Sicherheits-Prüfer** (Schwachstellen mit Angriffsweg und Gegenmaßnahme), **Kritiker** (unbelegte Annahmen), **Advocatus Diaboli** (härtet Entscheidungen durch Gegenargumente). |
| **Zugänge** | Auf dem eigenen Rechner läuft alles ohne Anmeldung. Andere Geräte im Netz brauchen einen Zugangsschlüssel — unter **Einstellungen → Zugänge** legst du pro Alpha-Tester einen eigenen an und widerrufst ihn nach dem Test. Der Besitzer-Schlüssel steht beim Serverstart in der Konsole. |
| **Lauf-Abbruch** | Jeder Hintergrundlauf (Skill, Pipeline, Deep Research, Browser, Coding-Agent) zeigt auf seiner Live-Karte ein **✕ Abbrechen** — der Lauf endet sauber an der nächsten Schrittgrenze und meldet sich als „abgebrochen“ statt als Fehler. |
| **🖱 Computer-Use (Alpha)** | Zwei lokale Modelle im Team: dein Ollama-Modell **plant**, ein Grounding-Modell (z. B. `nvidia/LocateAnything-3B`) **findet die Stelle** auf dem Bildschirm, und Dive on Wide **führt aus** (Klicken, Tippen, Tasten). `/zeig` markiert nur, `/steuern` erledigt eine Aufgabe Schritt für Schritt. Die Steuerung ist standardmäßig **aus**; jede Aktion braucht (per Voreinstellung) deine Freigabe im Chat, der ✕-Knopf bricht sofort ab. |
| **🚦 Nichts ist vorab an** | Beim ersten Start fragt eine Einrichtung einmal, was Dive on Wide darf. **Alles, was deinen Rechner, dein Netz oder deine Daten anfasst, ist aus**, bis du es willst — Code ausführen, Chats ins Wissen übernehmen, Maus und Tastatur, das Netzwerk. Die Einrichtung prüft nach, was auf diesem Rechner wirklich geht, statt Auswahlmöglichkeiten anzubieten, die später mit einer kryptischen Meldung scheitern. Es gibt einen ausdrücklichen Knopf „Alles aus lassen“, und der lässt auch alles aus. Jederzeit über die Einstellungen wieder erreichbar. |
| **🕰 Rhythmus** | Dive on Wide arbeitet auch, wenn niemand zusieht: morgens ein Briefing, stündlich eine Beobachtung, abends ein Nachfassen. Täglich zu einer Uhrzeit (mit Wochentagen), in einem Abstand oder einmalig — als Agent, Skill, Orchestrator oder als Briefing, das **ausschließlich zusammenfasst, was das System wirklich weiß**; der Prompt verbietet ausdrücklich, Termine zu erfinden. Ergebnisse landen wie alles andere als Artefakt in der Inbox. Der Zeitgeber steckt in Dive on Wide: es wird nichts in die Systemzeitsteuerung eingetragen, es läuft also nichts weiter, wenn du den Ordner löschst. |
| **🕸 Mesh (optional)** | Ein eigener dezentraler Unterbau: reines Peer-to-Peer, kein zentraler Server, alles im RAM. Zwei Betriebsarten, die sich nie mischen — **Diving Net** (dein Netz, per LAN-Rundruf gefunden) und **Weite** (das offene Netz, betreten mit einem signierten Ticket als QR-Code, danach geben Knoten sich weiter). Zeigt den gebündelten Speicher aller Geräte, die nutzbare Zahl bereits um die dreifache Absicherung bereinigt. Modelle anderer Geräte erscheinen als Anbieter — Agenten, Skills, Pipelines und der Orchestrator können sie alle nutzen. |
| **✉️ Bote (Messenger)** | Zwei Menschen tauschen persönlich einen Anker; beide Geräte leiten dieselbe, täglich wechselnde Treffpunkt-Adresse ab und finden sich ohne Verzeichnis, Konto oder Telefonnummer. Treffpunkte gehen blind raus, damit niemand sieht, welche zwei Geräte zusammengehören. Versiegelt mit XChaCha20-Poly1305, mit Sicherheitszahl zum persönlichen Abgleich. Auslöschen vernichtet auch den Anker — die Verbindung existiert danach nicht mehr als Adresse. |
| **🗣 Forum** | Fäden, die von selbst verfallen. Je Faden ein eigenes Pseudonym, damit zwei Beiträge nicht verknüpfbar sind. Nur der Eröffner kann schließen. Moderation sind lokale Filter statt Moderatoren. |
| **🧠 Geteilte Modelle** | Ein Modell lokal fahren, auf einem anderen Gerät im Netz oder in der Weite. Aufträge gehen versiegelt raus und werden nur auf dem eigenen Gerät zusammengesetzt. An mehrere fähige Knoten gleichzeitig, weil der Vergleich der Antworten die einzige Handhabe gegen absichtlich falsche Ergebnisse ist. Ein fremder Auftrag bekommt genau einen Modellaufruf. |
| **Werkbank-Agent** | Anleitung: [docs/WERKBANK.md](docs/WERKBANK.md). |
| **Werkbank-Agent (Kurzfassung)** | Löst Aufgaben in einem echten Projekt wie Claude Code: auflisten, lesen, suchen, genau eine Stelle ändern, Tests ausführen — und erst „fertig“ melden, wenn geprüft ist. Rechtestufen (nur lesen / im Projekt schreiben / voll) und getrennt davon die Freigabe; Befehle laufen in `sandbox-exec` (macOS) bzw. `bwrap` (Linux) ohne Netz. Befolgt eine `DOWOS.md` im Projekt, verdichtet den Kontext bei langen Aufgaben, repariert halb kaputtes JSON kleiner Modelle. **Checkpunkte** vor jedem Lauf und nach jedem Schritt, der die Platte verändert hat (auch durch Befehle) — Diff ansehen, zurücksetzen, das Zurücksetzen selbst zurücknehmen; was `.gitignore` ausschließt, bleibt unangetastet. Gemessen an 20 Mini-Projekten mit versteckten Tests: `python3 pruefstand/stufe2.py`. |
| **`dowos` im Terminal** | Der Werkbank-Agent in Terminal, Skripten und CI — wie `claude -p` oder `codex exec`: `dowos werkbank "Behebe den Fehler"`, Eingaben per Pipe, `--json`, Rückgabewerte (0 fertig · 2 Schrittlimit · 3 Rückfrage), `dowos fortsetzen`, `dowos review` (Git-Änderungen nur lesend prüfen), `dowos checkpunkte` / `zuruecksetzen`. Braucht keinen laufenden Server; Läufe erscheinen in der Oberfläche. |
| **Eigene Befehle & Schülermodelle** | `/name Argumente` setzt einen Markdown-Befehl aus `.dowos/befehle/`, `.claude/commands/` oder `~/.codex/prompts/` ein (`$ARGUMENTS`, `$1`…) — reiner Text, gibt keine Rechte, keine Shell. Lehrer- und Schülerrolle nehmen jedes Modell von Ollama oder einem OpenAI-kompatiblen Anbieter; ein fertiges LoRA-Training lässt sich als Modell auf 127.0.0.1 bereitstellen und überall auswählen. |
| **Editoren (ACP)** | `dowos acp` spricht das Agent Client Protocol: Zed, JetBrains und andere ACP-Editoren nutzen den Werkbank-Agenten wie Claude Code oder Codex — Schritte als Werkzeugaufrufe, Freigaben im Editor, Modi für Planen / nur lesen / Projekt / voller Zugriff. |
| **Trainingsrezepte** | Ein Dokument beschreibt einen ganzen Lauf (Basis, Daten, Verfahren, Prüfung). `auto`-Werte kommen aus deinen Daten und deinen **gemessenen** früheren Läufen — mit Begründung je Zahl. Die Daten werden vorher untersucht: Format (Alpaca, ShareGPT, ChatML, Frage/Antwort), Dubletten, leere Antworten, zu lange Beispiele, Überschneidung zwischen Training und Prüfteil — undichte Daten starten nicht. Anleitung: [docs/REZEPTE.md](docs/REZEPTE.md). |
| **Destillation mit Lehrermodellen** | Spitzenmodelle jedes eingerichteten Anbieters (OpenAI, Anthropic, Kimi, OpenRouter …) lösen Übungsaufgaben zu deinen Themen; ein unabhängiges Orakel in der Sandbox prüft jede Lösung (versteckte Tests, Manipulation, Störungen, Ablation), Tore G0–G7 berechnen das Label (GOLD/SILVER/BRONZE/REJECT). Harte Budgetgrenze, Kostenschätzung aus gemessenen Läufen, Datensätze je Thema mit Anteilsgrenzen je Lehrer. Anleitung: [docs/DESTILLATION.md](docs/DESTILLATION.md). |
| **Webhooks** | Werkbank-Aufträge aus GitHub/Gitea, CI oder Skripten: HMAC-signiert, ein Lauf je Zustellung, unbeaufsichtigt, Standard „nur lesen“, nie voller Zugriff. |
| **Slack** | Dasselbe über eine eigene Slack-App im Socket Mode — keine öffentliche Adresse nötig, nur Direktnachrichten, Freigaben als Knöpfe. Standardmäßig aus. |
| **Discord** | Dasselbe wie Telegram über einen eigenen Discord-Bot — nur Direktnachrichten, Kopplung per Einmal-Code, Freigaben als Knöpfe. Gateway-Client aus der Standardbibliothek. Standardmäßig aus. |
| **Verlauf, Abzweigen, Profile** | Jeder Werkbank-Lauf schreibt live ein Schrittprotokoll (was das Modell sah und antwortete, unverdichtet) — ansehen, bei jedem Schritt mit neuer Anweisung abzweigen (auf Wunsch mit dem Projekt auf diesem Stand), unterbrochene Läufe aufnehmen. Agentenprofile (Format der Claude-Code-Subagenten, `.claude/agents/` wird gelesen) beschränken Werkzeuge und senken Rechte nur; eingebaut erkunder/reviewer/tester/minimal. Claude Code und Codex als Unteragenten — standardmäßig aus, jeder Aufruf mit Freigabe. Ideen aus DeepSeek Harness. |
| **Telegram** | Dive on Wide vom Handy, wie das Gateway von Hermes: Nachrichten beantwortet das Standardmodell, `/werkbank Auftrag` startet den Werkbank-Agenten im gewählten Projekt, Freigaben kommen als Knöpfe, `/status`, `/abbrechen`. Standardmäßig aus, eigener Bot-Schlüssel, Chats per Einmal-Code gekoppelt. |
| **MCP** | Der Werkbank-Agent nutzt Werkzeuge von MCP-Servern (stdio) — dasselbe `mcpServers`-Format wie bei Claude, eine vorhandene Konfiguration lässt sich einfügen. Pro Lauf auswählbar; jeder Aufruf braucht eine Freigabe, außer der Server ist als vertraut markiert, und „nur lesen“ ruft sie nie. Schlüssel in `env` werden nie zurückgezeigt. |
| **Training** | Aus guter Arbeit ein eigenes Modell machen: mit 👍 bewertete Läufe (plus gelöste Prüfstand-Läufe, nur die freigegebene Hälfte) werden ein sauberer Datensatz; LoRA-Training mit mlx-lm läuft abgekoppelt, mit Fortschritt, Loss-Kurve, NaN-Warnung und Stopp. Lernraten-Plan in Optimizer-Schritten, Prompts maskiert. Bisher nur Apple Silicon — andere Plattformen erfahren das. |
| **Coding-Agent** | Selbstheilende Schleife: schreibt Code → führt ihn aus → liest die Fehlermeldung → korrigiert sich → wiederholt, bis es läuft. Jede Iteration meldet sich in der Inbox, das Protokoll landet als Artefakt. |

## Tests

Das System bringt einen eigenen Test-Harness mit — keine Installation, kein laufendes Ollama nötig (ein Mock-Server springt ein):

```bash
./test.sh                    # alle Tests
./test.sh --liste            # Testgruppen anzeigen
./test.sh --nur sandbox      # nur eine Gruppe
./test.sh --stress 50        # Nebenläufigkeit härter prüfen
./test.sh --stop             # beim ersten Fehler abbrechen
```

Aktuell **226 Tests** in zweiundzwanzig Gruppen: `start`, `seeds`, `crud`, `chat`, `laeufe`, `sandbox`, `wissen`, `fabrik`, `robust`, `last`, `inhalte`, `sicherheit`, `zugang`, `orchestrator`, `provider`, `webbridge`, `computer`, `stabil`, `upgrade`, `frontend`, `browser`, `mesh`.

Der Harness wächst mit dem System: Neue Agenten, Skills und Pipelines werden in `tests/run_tests.py` in die Listen `ERWARTETE_AGENTEN`, `ERWARTETE_SKILLS` und `ERWARTETE_PIPELINES` eingetragen und dadurch automatisch mitgeprüft — jeder Agent wird einzeln durch eine Testpipeline geschickt, jeder Skill komplett durchlaufen. Ein neuer Test ist eine Funktion mit `@test("gruppe", "beschreibung")`.

## Der Fluss

Alle Module verhalten sich gleich — das ist das Ordnungsprinzip des Systems:

```
Eingabe → Parser (@Skill · /Prompt · 🌐 Web) → Kontext (Agent · Wissen · Second Brain)
        → Ollama → Ergebnis → Artefakt → Inbox → Second Brain → Evolver
```

Egal ob Chat, Skill, Pipeline, Deep Research, Browser-Task oder Coding-Agent: Das Ergebnis wird zum Artefakt, das Artefakt meldet sich in der Inbox, der Verlauf fließt ins Second Brain, der Evolver verdichtet daraus Erkenntnisse. Im Code sorgen dafür drei gemeinsame Funktionen (`emit`, `save_artifact`, `deliver`) — jedes neue Modul, das du ergänzt, fügt sich mit einem Aufruf ein.

## Browser-Agent einrichten

Die eingebaute Web-Recherche (🌐) braucht nichts und funktioniert sofort. Der **Browser-Agent** (🖥) geht weiter: Er bedient einen echten Browser, kann also klicken, scrollen, Formulare ausfüllen und Seiten lesen, die eine reine Textabfrage nicht hergibt.

```bash
pip3 install browser-use ollama
python3 -m playwright install chromium
```

Danach zeigt **Deep Research → Browser-Agent** eine Diagnose: Welche Teile vorhanden sind, was fehlt, und mit welchem Befehl du es nachholst.

**Dein eigenes Chrome verwenden** (empfohlen — dann gelten deine Anmeldungen, Erweiterungen und Cookies):

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
```

Anschließend in den Einstellungen unter *Browser-Agent* die Adresse `http://localhost:9222` eintragen. Dive on Wide übernimmt dann das laufende Fenster, statt einen leeren Browser zu starten. Ohne Eintrag startet der Agent einen eigenen, sauberen Browser.

**Verwendung:**

- Im Chat: `/browser Finde die drei neuesten Ollama-Releases und fasse die Änderungen zusammen`
- Als Werkzeug: ＋ → 🖥 Browser-Agent — die nächste Nachricht wird zum Auftrag
- In Pipelines: Baustein 🖥 **Browser** an beliebiger Stelle einhängen

**Ohne Installation bricht nichts ab:** Ein Browser-Baustein in einer Pipeline weicht automatisch auf die eingebaute Web-Recherche aus und vermerkt das im Ergebnis.

## Computer-Use einrichten (Alpha)

Computer-Use braucht einen **Grounding-Server** — ein Vision-Modell hinter einer OpenAI-kompatiblen API, das auf einem Screenshot die Stelle findet, die der Planner meint. Wie Ollama ist er Maschinen-Infrastruktur und **nicht** Teil dieses Ordners:

```bash
# Apple Silicon (eigenes venv, Python 3.12; mlx-vlm serviert das Modell
# OpenAI-kompatibel — vllm-metal liegt bei, kann Vision-Modelle aber noch nicht):
uv venv --python 3.12 ~/dowos-grounding/venv
uv pip install --python ~/dowos-grounding/venv/bin/python vllm-metal mlx-vlm
~/dowos-grounding/venv/bin/python -m mlx_vlm server \
    --model nvidia/LocateAnything-3B --port 8600

# Linux/GPU-Rechner (echtes vLLM):
pip install vllm && vllm serve nvidia/LocateAnything-3B
```

Danach zeigt **Einstellungen → Computer-Use** eine Diagnose: was erreichbar ist, was fehlt und mit welchem Befehl du es nachholst. Ohne Server bricht nichts — die Funktion bleibt sichtbar deaktiviert.

**„Zeig mir wo" (Phase 2):** Im Chat `/zeig der rote Senden-Knopf` eingeben — Dive on Wide nimmt ein Bildschirmfoto auf, fragt den Grounder und liefert die Fundstelle als Pixel-Koordinaten plus einen **markierten Screenshot** als Artefakt. Es wird dabei **nichts geklickt oder getippt** — nur gezeigt. So prüfst du die komplette Strecke (Screenshot → Grounding → Koordinaten), bevor die eigentliche Steuerung dazukommt. Screenshots sind Besitzer-Sache: Gäste können `/zeig` nicht auslösen. Hinweis: Beim ersten Mal fragt macOS nach der Berechtigung „Bildschirmaufnahme" für das Terminal, in dem Dive on Wide läuft.

**Steuerung (Phase 3):** `/steuern öffne die Systemeinstellungen und aktiviere den Nachtmodus` — Dive on Wide arbeitet in einer Schleife: Bildschirm ansehen → **Planner** (Ollama) entscheidet die nächste Aktion → **Grounder** findet das Ziel → **Executor** (`cliclick`) klickt/tippt → nächster Screenshot zur Kontrolle, bis der Planner „fertig" meldet. Das ganze Protokoll (jeder Gedanke, jede Aktion) landet als Artefakt.

Sicherheit ist hier nicht optional, sondern eingebaut:

- **Standardmäßig aus.** Maus- und Tastatursteuerung schaltest du bewusst frei unter *Einstellungen → Computer-Use → „Steuerung erlauben"*. Ohne das läuft nur `/zeig`.
- **Bestätigungs-Schranke.** Vor jeder verändernden Aktion hält der Lauf an und zeigt sie im Chat mit **✓ Ausführen / ✓✓ Alles im Lauf / ✕ Ablehnen**. (Abschaltbar, aber empfohlen an.)
- **Not-Aus.** Der ✕-Knopf am Lauf bricht zwischen den Schritten und während des Wartens sofort ab. Eine Zeitüberschreitung beim Warten gilt als Ablehnung.
- **Nur der Besitzer.** Gäste (Alpha-Tester im LAN) können weder `/steuern` starten noch eine Aktion freigeben.
- **Keine Geheimnisse.** Der Planner ist angewiesen, niemals Passwörter oder Kreditkartennummern einzugeben; erlaubte Tasten sind auf eine Weißliste begrenzt.

### Was Computer-Use wirklich braucht

Die Diagnose unter **Einstellungen → Computer-Use** prüft jeden Punkt einzeln und benennt den fehlenden. Wenn ein Lauf nichts tut, ist das die erste Anlaufstelle — genau dafür ist sie da.

| Voraussetzung | Warum sie zählt | Wenn sie fehlt |
|---|---|---|
| **Bildfähiges Planner-Modell** | Der Planner bekommt einen Screenshot. Ein reines Textmodell (etwa ein Coder-Modell) lehnt das Bild schlicht ab. | Dive on Wide fragt Ollama nach den Fähigkeiten des Modells und startet gar nicht erst — mit Nennung des Modells. |
| **Recht „Bildschirmaufnahme"** | Ohne das Recht sagt macOS nicht Nein — `screencapture` liefert einfach ein leeres Bild. | Systemeinstellungen → Datenschutz & Sicherheit → Bildschirmaufnahme, dort das Programm eintragen, in dem Dive on Wide läuft (Terminal/iTerm), danach dieses Programm neu starten. |
| **Recht „Bedienungshilfen"** | Ohne das Recht nimmt `cliclick` jeden Befehl an und tut nichts. Klicks scheitern lautlos — genau das sieht aus wie „das Feature ist kaputt". | Gleicher Ort, Abschnitt Bedienungshilfen. **Freigeben muss man den wirklich verantwortlichen Prozess — das ist nicht immer die App, die man sieht** (siehe Hinweis unten). Danach neu starten. |
| **`brew install cliclick`** | Der Executor, der Maus und Tastatur bewegt. | Installieren; macOS fragt danach einmalig nach den Bedienungshilfen. |

Die Tabelle nennt die **macOS**-Wege, weil dort gemessen wurde. Die Diagnose in
Dive on Wide zeigt aber die Wege **deines** Systems — auf Linux steht dort
`sudo apt install xdotool`, nicht `brew`.

## Das Symbol in der Ecke (optional)

Dive on Wide läuft als Server im Hintergrund — man merkt ihm von außen nichts an.
Das Menüleisten-Symbol schließt die Lücke:

```bash
menueleiste/bauen.sh
open "menueleiste/Dive on Wide Menü.app"
```

| Symbol | Bedeutung |
|---|---|
| `◌` | Kein Dive on Wide auf diesem Port |
| `●` | Läuft, Netzwerk aus |
| `● 3` | Läuft, Netzwerk mit 3 Geräten |

Im Menü: Dive on Wide öffnen, Diving Net starten oder verlassen, Menü beenden (Dive on Wide
läuft dabei weiter). Mehr nicht — es ist eine Hülle um dieselbe Schnittstelle,
die auch die Weboberfläche benutzt, kein zweites Produkt. Könnte es etwas, was
die Oberfläche nicht kann, wäre das ein Fehler.

**Warum Swift und nicht Python.** Ein Menüleisten-Symbol braucht AppKit; aus
Python ginge das nur mit PyObjC, und null Abhängigkeiten ist hier keine Zierde,
sondern die Zusage. `swiftc` liegt den Xcode-Befehlszeilenwerkzeugen bei, die
auf dem Mac ohnehin schon für `python3` gebraucht werden. Fehlt es, sagt das
Bauskript das — und nichts geht kaputt: **Dive on Wide läuft ohne das Menü
vollständig.**

**Es bekommt keine eigenen Rechte.** Gesprochen wird ausschließlich mit
127.0.0.1. Kein Zugriff nach außen, keine Dateien, keine Bedienungshilfen. Das
Menü kann nichts, was ein Browser auf derselben Maschine nicht auch könnte.

**Selbstprüfung.** Wenn das Symbol etwas anderes zeigt als erwartet:

```bash
"menueleiste/Dive on Wide Menü.app/Contents/MacOS/DowOSMenu" --pruefen
```

Das fragt einmal ab und schreibt hin, welchen Port es benutzt, was der Server
geantwortet hat und was im Menü stünde — Rohdaten statt Vermutung.

---

### Wem im Netz kann man trauen?

Wenn Aufträge an fremde Geräte gehen, stellt sich die Frage, ob dort überhaupt
gerechnet wird. Unter **🕸 Netzwerk → Vertrauen** steht, was jeder Knoten
bisher geliefert hat.

**Was nicht geht:** eine Modellantwort nachprüfen. Zwei ehrliche Geräte
antworten verschieden — Mehrheitsentscheid ist bei freiem Text sinnlos.

**Was geht:** erkennen, ob jemand gar nicht rechnet. Immer dieselbe Floskel,
leere Antworten, Themaverfehlung, die Frage zurückgespiegelt — das sind die
billigen Betrugsmuster, und die werden gefangen.

**Im Zweifel für den Knoten.** Zwei Beanstandungen sind nötig; ein einzelner
Aussetzer sperrt niemanden. Pakete gehen verloren, Modelle verhaspeln sich —
und ein fälschlich ausgesperrter ehrlicher Knoten ist teurer als ein
durchgerutschter Betrüger, denn der Erste kommt nicht wieder.

**Kleine Modelle werden nicht bestraft.** Ein erster Entwurf prüfte, ob ein
Knoten eine Formatvorgabe einhält. Gemessen: ein 12B-Modell bestand fünf von
fünf, ein völlig ehrliches 0,5B-Modell **eine von fünf**. Diese Prüfung maß
Anweisungstreue, nicht Ehrlichkeit, und hätte genau die kleinen Geräte
ausgesperrt, die ein Freundesnetz ausmachen. Sie ist jetzt ein reiner
Können-Test und steht getrennt daneben — mit dem Zusatz „zählt nicht ins
Urteil".

**Die Akten sind flüchtig.** Ein Vertrauensurteil, das einen Neustart überlebt,
wäre eine dauerhafte Bewertung von Menschen. Wer sich neu verbindet, fängt neu
an.

---

### Ein eigener Bildschirm für den Agenten

Ohne ihn bewegt der Agent den echten Zeiger auf deinem echten Rechner. Jede
Fehlentscheidung trifft deine wirklichen Fenster, Dateien und Konten. Deshalb
ist die Steuerung standardmäßig aus, deshalb muss jede Aktion einzeln
freigegeben werden — lauter Notbremsen um ein Problem herum, das besser gar
nicht entsteht.

```bash
schirm/bauen.sh
```

Das baut einen Container mit einem X-Server ohne Grafikkarte (Xvfb), `xdotool`,
`maim` und einem winzigen Fenstermanager. Läuft er, benutzt Dive on Wide ihn **von
allein** — die Einstellung steht auf „Automatisch", und das ist Absicht: Die
sichere Wahl gehört in die Voreinstellung, denn Voreinstellungen sind das, was
fast alle behalten.

Der Container läuft:

| | |
|---|---|
| `--network none` | kein Netz — von dort ist nichts erreichbar |
| `--read-only` | das Abbild bleibt unverändert, nur `/tmp` ist beschreibbar |
| kein Bind-Mount | er sieht **nichts** von deinem Dateisystem |
| `no-new-privileges` | keine neuen Rechte, egal was drinnen passiert |
| `--memory 1g --cpus 1.5` | er kann den Rechner nicht lahmlegen |

Zusehen kannst du mit `SCHIRM_VNC=1 schirm/bauen.sh` — dann horcht ein
VNC-Server auf Port 5900, **nur über localhost**.

**Was ein Container nicht ist.** Er ist kein Hypervisor. Wer aus ihm ausbricht,
steht auf deinem Rechner — seltener als ein Fehlklick, aber nicht unmöglich. Er
ist eine *echte* Grenze und trotzdem eine schwächere als eine vollständige VM.
Das Gegenteil zu behaupten wäre schlimmer, als die Grenze gar nicht zu haben.

**Ehrlicher Erprobungsstand:** Rückseite, Bildschirmwahl, Diagnose und die
Abschottungs-Flaggen sind geprüft. Der Container selbst **konnte auf dem
Entwicklungsrechner nicht gestartet werden** — Docker Desktop lief dort nicht,
und ihn bei 2,8 GB freiem Speicher zu starten hätte genau die Beeinträchtigung
verursacht, die dieses Projekt sonst verweigert. Wer ihn zuerst baut, ist der
Erste.

---

### Computer-Use unter Linux: geschrieben, auf einem echten Desktop noch nie gelaufen

Alles Systemnahe steckt in [`steuerung.py`](steuerung.py) hinter einer
Schnittstelle. Drei Rückseiten:

| System | Bild | Maus und Tastatur | Stand |
|---|---|---|---|
| macOS | `screencapture` | `cliclick` | **gemessen** |
| Linux/X11 | `maim`, `scrot`, `import` oder `gnome-screenshot` | `xdotool` | geschrieben, Befehle getestet |
| Linux/Wayland | `grim`, `spectacle`, `gnome-screenshot` | `ydotool`, Tippen über `wtype` | geschrieben, Befehle getestet |

**„Befehle getestet" heißt genau das und nicht mehr.** Die Tests schieben
gefälschte `xdotool`/`grim`/`maim`-Programme auf den `PATH` und prüfen die
Argumente Zeichen für Zeichen — dass `xdotool` erst `mousemove --sync` und dann
`click --repeat 1` bekommt, dass `ydotool` **`--absolute`** benutzt (ohne das
Flag wäre die Bewegung relativ und der Zeiger liefe mit jedem Klick weiter),
dass die Rückgabetaste auf X11 `Return` heißt. Was diese Tests nicht können:
mit einem echten Anzeigeserver reden. **Auf einem echten Linux-Desktop ist das
noch nie gelaufen.** Dive on Wide sagt das auf dem Gerät selbst, nicht nur hier.

**Wayland ist ein Sonderfall, und zwar kein Fehler.** Unter X11 darf jedes
Programm den Schirm lesen und jedem Fenster Tasten schicken. Wayland hat genau
das abgeschafft — dafür gibt es Wayland. `grim` und `ydotool` können deshalb
trotz Installation abgewiesen werden, und `ydotool` braucht zusätzlich seinen
Dienst und Zugriff auf `/dev/uinput`. Der kürzeste Weg ist meist, bei der
Anmeldung eine **X11-Sitzung** zu wählen. Dive on Wide schreibt das hin, statt einen
raten zu lassen.

**Ohne Bildschirm ist nichts kaputt.** Auf einem Server ohne grafische Sitzung
sagt die Diagnose genau das — Computer-Use steuert einen Bildschirm, und wo
keiner ist, gibt es nichts zu steuern. Alles andere läuft dort normal weiter.
| **Der Steuerungs-Schalter** | Standardmäßig aus, mit Absicht. | Einstellungen → Computer-Use → „Steuerung erlauben". |

**Zur Geschwindigkeit des Planners.** Ein Lauf hat bis zu 15 Schritte und fragt den Planner pro Schritt einmal. Zwei Dinge halten das erträglich, beide automatisch: der Planner wird zu **reinem JSON** gezwungen, und **das laute Denken wird für diese Anfrage abgeschaltet**. Gemessen an `gemma4:12b` gegen einen 3024×1964-Bildschirm, je drei Läufe:

| | Sekunden je Schritt | Ausgabe-Token |
|---|---|---|
| ohne beides | 26–140 s | 618–3292 |
| nur JSON-Zwang | 39–81 s | 40–47 |
| nur Denken aus | 2–4 s | 40–59 |
| **beides — so macht es Dive on Wide** | **~2 s** | **42–46** |

„Thinking"-Modelle verbringen fast die ganze Zeit damit, vor einer winzigen Antwort laut nachzudenken; für einen Planner, der einen Klick entscheidet, ist das reine Kosten. Sprengt ein Planner trotzdem den Zeitrahmen je Schritt (**Einstellungen → Computer-Use**, Standard 180 s), endet der Lauf mit einer Meldung samt Modellnamen, statt still zu hängen — dann ein kleineres Vision-Modell wählen.

**Die Falle „ist doch eingetragen, geht trotzdem nicht".** macOS vergibt die Bedienungshilfen **pro Code-Signatur des verantwortlichen Prozesses** — und der ist nicht immer die App, die man sieht. iTerm2 startet Shells über einen eigenen Helfer:

```
iTerm.app                  Signatur com.googlecode.iterm2
└─ iTermServer-3.6.11      Signatur iTermServer   ← andere Identität
   └─ zsh
      └─ python3 server.py erbt von iTermServer, NICHT von iTerm.app
```

*iTerm.app* freizugeben bringt deshalb nichts: Der Eintrag steht in der Liste und wirkt trotzdem nicht. Die Diagnose erkennt das und nennt den genauen Pfad, statt „Terminal/iTerm" zu raten. Drei Auswege:

1. **Am einfachsten:** Dive on Wide aus **Terminal.app** starten und Terminal.app freigeben — kein Helfer dazwischen.
2. Die Helfer-Datei selbst in die Bedienungshilfen-Liste ziehen (bricht bei jedem Update, die Version steht im Dateinamen).
3. iTerms Sitzungsserver abschalten: `defaults write com.googlecode.iterm2 RunJobsInServers -bool false`, danach iTerm vollständig beenden und neu starten. Kostet die Sitzungswiederherstellung.

## Discord: deine lokalen Modelle auf deinem Server

Zwei Wege, je nach Zweck:

- **Fernbedienung (Direktnachricht, nur du):** Einstellungen → Discord. Gekoppelte Chats; `/werkbank`, `/status`,
  `/abbrechen`, Freigaben per Knopf.
- **Server-Chat (für alle Mitglieder):** Einstellungen → **Discord-Server-Chat**. Mitglieder schreiben einfach in den
  freigegebenen Kanal oder klicken **„Neuer Chat“** — jede Frage öffnet einen **privaten Chat**, den nur sie und der Bot
  sehen. Dort schreiben sie einfach weiter, wählen das Modell aus einer **Liste** (nur die Modelle, die du freigibst) und
  beenden den Chat mit einem Knopf. Unter jeder Antwort stehen Modell und „KI-Antwort, ungeprüft“.

**Einrichten in drei Schritten:** (1) discord.com/developers → New Application → Bot → Reset Token; unter Installation
„Install Link“ auf None, dann „Public Bot“ aus; **Message Content Intent** einschalten. (2) Token in Dive on Wide eintragen,
speichern. (3) Einladungslink aus Dive on Wide öffnen (nur die nötigen Rechte, kein Administrator), Kanäle ankreuzen, einschalten.

**Betriebsarten:**
- **Öffentlich — empfohlen für Server mit Fremden:** nur Chat. Keine Werkbank, kein Code, kein Dateizugriff. Grenze je
  Mitglied und Stunde, Warteschlange (der Rechner rechnet eine Antwort nach der anderen).
- **Privat — voller Zugriff:** zusätzlich `/werkbank`, `/status`, `/abbrechen` mit Freigabe per Knopf — **nur für die
  eingetragenen Discord-Nutzer-IDs**. Alle anderen im Kanal bekommen weiter nur den Chat. Nur auf einem Server, dem du vertraust.

Gerechnet wird auf deinem Rechner: Läuft Dive on Wide nicht, ist der Bot offline. Ohne Message Content Intent antwortet der Bot
nur auf @Erwähnung; er versucht es alle 10 Minuten erneut, sobald du den Schalter nachträglich setzt. Moderatoren mit
„Threads verwalten“ können private Threads ebenfalls öffnen.

---

## Das Mesh — dein eigenes Netz, ohne Server

Dive on Wide kann auf einem eigenen dezentralen Unterbau laufen: reines
Peer-to-Peer, kein zentraler Server, alles im flüchtigen Arbeitsspeicher. Das
ist vollständig optional — Dive on Wide funktioniert ohne, wenn du es nie startest —
und es ist aus, bis du eine Betriebsart wählst.

Öffne **🕸 Netzwerk** und wähle. **Ein Knoten läuft in genau einer Betriebsart,
und der Wechsel braucht einen Neustart** — so rutscht niemand versehentlich aus
einem privaten Kreis in ein offenes Netz.

| | **Diving Net** — dein eigenes Netz | **Weite** — das offene Netz |
|---|---|---|
| Wer beitritt | Geräte in deinem Netz oder mit Einladungsticket | **nur mit Ticket** — auch wer im selben LAN sitzt |
| Wie sie sich finden | LAN-Rundruf, ohne Konfiguration | ein Ticket, danach geben Knoten sich weiter |
| Wie Inhalte verbreitet werden | Rundruf — zwanzig Geräte, ein Paket | Kademlia: zu den 20 Knoten, die der Adresse am nächsten liegen |
| Wie gesucht wird | jeder hat ohnehin alles | ~28 Fragen, auch bei 20.000 Knoten |
| Gut für | Freunde, ein Lehrstuhl, ein Haushalt | viele Knoten, große Modelle |
| Der Preis | zu klein für sehr große Modelle | Fremde im Netz |

### Was du bekommst

**Einen Compute-Zähler.** Jeder Knoten ruft aus, wie viel Speicher er gerade
beisteuert — du siehst die Summe über alle Geräte. Die als *nutzbar* gezeigte
Zahl ist bewusst ein Drittel der Rohsumme: Ohne dreifache Absicherung steht
eine verteilte Pipeline praktisch dauerhaft. Eine Anzeige, die das verschweigt,
verspricht Modelle, die nie laufen.

**✉️ Bote — der Messenger.** Zwei Menschen tauschen persönlich einen **Anker**
aus, 32 Zeichen ohne 0/O/1/I, also zum Vorlesen gemacht. Beide Geräte leiten
daraus dieselbe Treffpunkt-Adresse ab, jeden Tag eine andere, und finden sich
ohne Verzeichnis, ohne Konto, ohne Telefonnummer. Treffpunkte gehen **blind**
raus — frischer Zufall bei jedem Ruf — damit ein Beobachter nicht sieht, welche
zwei Geräte zusammengehören. Nachrichten sind mit XChaCha20-Poly1305
versiegelt. Vergleicht die **Sicherheitszahl** einmal persönlich:
Verschlüsselung schützt die Leitung, sagt aber nichts darüber, wer am anderen
Ende sitzt. Nachrichten lassen sich zusätzlich **über Umwege** schicken — in
Schichten verpackt, sodass jeder Zwischenknoten nur den nächsten kennt, nie
Absender und Ziel zugleich. Dann sieht auch kein Nachbar mehr, **dass** ihr
miteinander sprecht.

**🗣 Forum — Fäden, die verfallen.** Kein Betreiber, keine Konten. Jeder Faden
gibt dir ein **eigenes Pseudonym**, zwei Beiträge von dir in zwei Fäden sind
nicht als derselbe Mensch erkennbar. Nur der Eröffner kann schließen.
Moderation sind lokale Filter statt Moderatoren — wer moderieren darf, kann
auch zensieren.

**🧠 Modelle — drei Wege.** Lokal auf deinem Ollama, auf einem anderen Gerät in
deinem Netz, oder in der Weite. Der Auftrag geht versiegelt hin, das Ergebnis
versiegelt zurück; **zusammengesetzt wird nur auf deinem Gerät**, ohne
Zwischenspeicher. Aufträge gehen an mehrere fähige Knoten gleichzeitig: Ein
Knoten kann mitten im Auftrag verschwinden, und bei Fremden ist der Vergleich
zweier Antworten die einzige Handhabe gegen absichtlich falsche Ergebnisse.

**Dein Beitrag, jederzeit widerrufbar.** Der Ressourcen-Statthalter lässt einen
Knoten nie das Gerät seines Besitzers beeinträchtigen: Er gibt Speicher sofort
zurück, wenn du ihn brauchst, und trägt nichts bei auf Akku, bei
Wärmedrosselung oder wenn Speicher nicht messbar ist. Der Halt wirkt ohne Netz
und ohne die Zustimmung Dritter. Ein fremder Auftrag bekommt genau einen
Modellaufruf — nichts gestartet, nichts ausgeführt, keine Werkzeuge.
Cloud-Modelle mit API-Key werden dem Netz nie angeboten.

### Ein Modell über mehrere Geräte

Ein großes Modell passt auf kein einzelnes Gerät. **Verteilt** hält jedes Gerät
ein paar Schichten, und jedes Token läuft einmal durch die ganze Kette.

Unter **🕸 Netzwerk → Ein Modell über mehrere Geräte**:

1. **„Dieses Gerät darf Schichten für andere halten"** anhaken — auf jedem
   Gerät, das mitmachen soll. **Standardmäßig aus.** Ein Gerät, das Schichten
   eines fremden Modells hält, ist für die ganze Sitzung gebunden.
2. Ein Modell eintragen (z. B. `llama3:70b`) und **Plan rechnen**.

Dive on Wide zeigt dann, welches Gerät wie viele Schichten trägt. **Die Aufteilung
richtet sich nach Kapazität, nicht nach Kopfzahl** — ein Gerät mit 32 GB trägt
mehr als eines mit 8 GB. Das Gerät, das die Kette anführt, bekommt zusätzlich
den Kontextspeicher aufgebürdet und deshalb weniger Schichten.

**Was Dive on Wide dabei tut und was nicht.** Gerechnet wird von **llama.cpp**, nie
von Python — die Tensor-Arbeit wäre in reinem Python um Größenordnungen zu
langsam. Der Mechanismus dafür existiert bereits: die **RPC-Rückseite** von
llama.cpp. Dive on Wide erfindet sie nicht neu, sondern findet die Geräte, plant die
Aufteilung, baut den Aufruf und zeigt das Ergebnis.

**Dafür braucht es llama.cpp MIT RPC.** Die Fassung aus Homebrew hat es nicht:
weder `ggml-rpc-server` noch `--rpc` in `llama-server`. Die **offiziellen Pakete**
unter [github.com/ggml-org/llama.cpp/releases](https://github.com/ggml-org/llama.cpp/releases)
haben es — für jedes System, bauen muss niemand:

| System | Paket |
|---|---|
| Windows | `llama-…-bin-win-cpu-x64.zip` · Nvidia: `…-win-cuda-12.4-x64.zip` + `cudart-…-win-cuda-12.4-x64.zip` · AMD/Intel: `…-win-vulkan-x64.zip` |
| Linux | `llama-…-bin-ubuntu-x64.tar.gz` · Nvidia: `…-ubuntu-cuda-12.8-x64.tar.gz` · andere Grafik: `…-ubuntu-vulkan-x64.tar.gz` |
| macOS | `llama-…-bin-macos-arm64.tar.gz` (Apple-Chip) · `…-macos-x64.tar.gz` (Intel), danach `xattr -dr com.apple.quarantine ~/.dowos/llama-rpc/bin` |

Die Programme nach `~/.dowos/llama-rpc/bin` entpacken (Windows:
`%USERPROFILE%\.dowos\llama-rpc\bin`) und Dive on Wide neu starten. **Am `PATH` ist
nichts zu ändern** — Dive on Wide sieht dort von allein nach, zeigt danach „bereit“
und nennt unter **Netzwerk** sonst genau die Anleitung für das eigene System.

Der eigene Ort wird dabei **vor** dem `PATH` durchsucht. Sonst gewänne der
Homebrew-Bau, der kein RPC kann und im `PATH` weiter vorn steht — und man
suchte lange nach dem Grund.

**Zwei Stolperstellen, die uns Zeit gekostet haben:**

* Der Rechenknoten heißt **`ggml-rpc-server`**, nicht `rpc-server` (so hieß er
  früher). Wer den alten Namen sucht, findet nichts und hält es für unmöglich.
* Auf dem Mac muss der Rechenknoten **auf Metal festgenagelt** werden
  (`-d MTL0`). Sonst greift er sich BLAS, das kennt `RMS_NORM` nicht und bricht
  mitten im ersten Durchlauf ab — der Hauptprozess meldet nur „Remote RPC
  server crashed or returned malformed response", was einem nicht verrät, dass
  man ein Gerät auswählen muss. Dive on Wide setzt es von allein.

### So benutzt man es

1. **Auf jedem Gerät, das mitrechnen soll:** 🕸 Netzwerk → „Dieses Gerät darf
   Schichten für andere halten" anhaken. Dive on Wide startet dann wirklich einen
   Rechenknoten — kein Häkchen ohne Prozess dahinter.
2. **Auf dem Gerät, an dem du sitzt:** Modell eintragen, **Plan rechnen**, dann
   **▶ Dieses Modell jetzt verteilt starten**.
3. Danach steht es **überall zur Wahl**, wo Dive on Wide Modelle anbietet — Chat,
   Agenten, Skills, Pipelines, Orchestrator.

Die Zwischenergebnisse kommen von den anderen Geräten. **Zusammengesetzt wird
die Antwort ausschließlich auf deinem Gerät** — kein anderes sieht je den
fertigen Text.

**Eine Stelle, die verwirrt, wenn man sie nicht kennt:** Schaltest du das
Rechenangebot ein, zeigt Dive on Wide danach oft „0 GB abgegeben". Das ist kein
Fehler — der Rechenknoten hat sich den Speicher bereits genommen. Dive on Wide
schreibt das inzwischen dazu, statt eine nackte Null stehen zu lassen.

**Die Modellmaße kommen aus der Datei, nicht aus dem Namen.** Aus „12b" hätte
Dive on Wide 5 GB und 32 Schichten geraten; die Datei sagt 6,87 GB und 48. Ein Plan
auf geratenen Zahlen verteilt Schichten, die es gar nicht gibt.

**Und der unbequeme Teil:** Die RPC-Schnittstelle von llama.cpp kennt **keine
Anmeldung**. Wer den Rechenknoten im Netz erreicht, kann ihn benutzen und zum
Absturz bringen. Dive on Wide bindet ihn deshalb auf genau die eine Adresse, unter
der das Mesh ihn kennt, und nicht auf alle — aber innerhalb deines Netzes ist
er offen. In einem Diving Net unter Freunden ist das vertretbar; in einem fremden
WLAN nicht.

### Es läuft — nachgemessen

Nicht behauptet, sondern durchgeführt: llama.cpp mit RPC gebaut, zwei
Rechenknoten gestartet, ein Modell darüber aufgeteilt.

| | |
|---|---|
| Plan von Dive on Wide | 8 / 8 / 8 Schichten auf drei Stufen |
| Geschätzt | 90,9 Token/s |
| **Gemessen** | **88,8 Token/s** (40 Token in 0,45 s) |
| Gegenbeweis | Ein Rechenknoten abgeschaltet → **die Inferenz bricht ab** |

Die letzte Zeile ist die wichtigste. Sie zeigt, dass das Modell wirklich an
beiden Knoten hing und nicht heimlich alles lokal lief.

**Was diese Zahlen nicht sagen.** Alle drei „Geräte" waren derselbe Rechner.
Es gab also keine echte Netzlaufzeit und keinen getrennten Speicher — die
Schätzung traf so genau, weil der Netzanteil praktisch null war. Über echte
Geräte hinweg wird es deutlich langsamer; die Zahlen dafür stehen in
[`mesh/ARCHITEKTUR.md`](mesh/ARCHITEKTUR.md) Abschnitt 7.

Und die unbequeme Zahl für den echten Fall: Über drei getrennte Geräte mit
einem großen Modell sind grob **1–2 Token/s** zu erwarten. Der Engpass ist die Speicherbandbreite, nicht die Rechenleistung —
jedes Gewicht muss je Token einmal gelesen werden. Für einen Chat ist das
nichts. Für einen Auftrag über Nacht sind acht Stunden rund 40.000 Token. Ob
sich das lohnt, entscheidet die Aufgabe; deshalb steht die Schätzung unter dem
Plan, bevor irgendetwas startet. Wofür sich das Netz **besser** eignet, steht in
[`mesh/ARCHITEKTUR.md`](mesh/ARCHITEKTUR.md) Abschnitt 7.2.

---

### Ehrliche Grenzen

- **Ein Handy kann kein Knoten sein.** Der reine Stdlib-Aufbau läuft nicht auf
  iOS oder Android. Ein Handy kann die Oberfläche eines Knotens bedienen, aber
  keinen Speicher beisteuern.
- **Verteilte Inferenz ist geplant, aber hier nie gelaufen.** Planung,
  Diagnose und Aufruf sind geprüft; das tatsächliche Rechnen über mehrere
  Geräte konnte auf dem Entwicklungsrechner **nicht** verifiziert werden, weil
  die dortige llama.cpp-Fassung ohne RPC übersetzt ist. Es als „funktioniert"
  zu verkaufen, wäre gelogen.
- **Ein Neustart löscht alles.** Kontakte, Chats und Fäden leben nur im RAM,
  der Anker muss neu eingegeben werden. Das ist die Zusage, keine fehlende
  Bequemlichkeit — wer das Gerät später in die Hand bekommt, findet nichts.
- **„Gelöscht" ist eine Bitte, keine Garantie.** Niemand kann fremden
  Arbeitsspeicher zwingen. Verlässlich ist nur die Verfallszeit.
- **Die Krypto ist standardmäßig reines Python und nicht konstantzeitig.**
  Solide gegen einen Netzwerk-Angreifer, unsolide gegen einen, der auf
  demselben Rechner misst. Für ein echtes Bedrohungsmodell PyNaCl installieren;
  der Knoten sagt immer, welche Stufe läuft.
- **Onion-Routing schützt vor den Zwischenknoten, nicht vor einem allwissenden
  Beobachter.** Wer alle Leitungen sieht, kann Pakete nach Zeit und Größe
  zuordnen. Dagegen hilft nur Verzögern und Vereinheitlichen — beides kostet
  Latenz und ist absichtlich nicht eingebaut. Und eine Kette ist nur so gut wie
  das Netz groß ist: Bei drei Geräten schützt sie wenig, weshalb die Oberfläche
  sagt, wie viele Umwege es wirklich gibt, statt eine Zahl zu versprechen.

Entscheidungen, die gemessenen Zahlen dahinter und die offenen Fragen stehen in
[`mesh/ARCHITEKTUR.md`](mesh/ARCHITEKTUR.md).

---

### Dive on Wide auf dem Handy

Der Kern von Dive on Wide ist reines Python — und Python läuft auf keinem iPhone
und keinem Android-Gerät. Daraus folgt **nicht**, dass ein Handy draußen
bleiben muss; es folgt nur, dass es drei verschiedene Wege gibt, die
verschieden weit tragen.

| Weg | Was damit geht | Was **nicht** geht | Zustand |
|---|---|---|---|
| **1. Browser / zum Startbildschirm hinzufügen** | Alles, was ein Mensch am Telefon tut: Chat, Bote, Forum, Aufträge stellen, Netzansicht, Modelle wählen | RAM beisteuern, im Hintergrund weiterlaufen, im LAN gefunden werden | **fertig** |
| 2. Native Hülle (Flutter / React Native + Rust-Kern) | zusätzlich: eigener Knoten, Hintergrundbetrieb, LAN-Rundruf | — | offen |
| 3. Rechenknoten in Rust/C++ | zusätzlich: echte Tensor-Arbeit statt nur Vermittlung | — | offen |

**Weg 1 ist eingebaut und braucht keinen App-Store.** Öffne auf dem Handy
`http://<IP-des-Rechners>:3000`, dann im Browsermenü *Zum Home-Bildschirm*.
Danach startet Dive on Wide als eigenständige App mit eigenem Symbol. Die
Oberfläche klappt die Seitenleiste zu einer Schublade zusammen; alles ist
mit dem Daumen erreichbar.

**Warum ein Browser-Tab keinen Arbeitsspeicher beisteuern kann** — das ist
keine fehlende Mühe, sondern eine Eigenschaft des Systems:

- Ein Tab bekommt einen harten Speicherdeckel (auf iOS besonders eng) und
  wird beim Überschreiten kommentarlos beendet.
- Wechselt man die App oder sperrt den Schirm, friert der Tab ein. Ein
  Knoten, der nur bei eingeschaltetem Display antwortet, ist für ein
  verteiltes Modell wertlos — die Pipeline steht bei jedem Ausfall.
- Ein Browser darf keinen UDP-Port öffnen und keinem Multicast beitreten.
  Die Nachbarschaftssuche im LAN ist ihm damit verschlossen.

**Wichtig, weil leicht zu verwechseln:** Wenn du Dive on Wide vom Handy aus
bedienst, läuft der Knoten weiterhin auf deinem **Rechner**. Das Handy ist
dann der Bildschirm, nicht der Knoten. Die Speicheranzeige und der Beitrag
gelten für die Maschine, auf der Dive on Wide gestartet wurde — nicht für das
Telefon in deiner Hand. Die Netzansicht sagt das auf schmalen Schirmen
ausdrücklich dazu.

Ein Handy wäre erst dann selbst ein Knoten, wenn Weg 2 gebaut ist.

**Der Dienstarbeiter hält nur die Hülle vor, niemals Inhalte.** Alles unter
`/api/` ist ausdrücklich ausgenommen und wird nie zwischengespeichert. Sonst
lägen Nachrichten auf der Platte — genau der Wortbruch, den das ganze System
vermeiden soll. Ein Test hält das fest.

## Web-Recherche stabil machen (optional)

Der Standard sucht schlüssellos (Mojeek/DuckDuckGo/Wikipedia) und funktioniert sofort. Wer verlässliche, aktuelle Treffer braucht, trägt unter **Einstellungen → 🌐 Web-Recherche** ein stärkeres Backend ein — nach dem Prinzip aus der Praxis: **strukturierte JSON-Suche statt HTML-Parsing**, und **sauberes Markdown statt roher HTML-Wüste**.

**Schritt 1 — Suche** (eines auswählen):

| Backend | Setup | Vorteil |
|---|---|---|
| **Tavily** | API-Key (großzügiges Gratis-Kontingent) | Für KI-Agenten optimiert, liefert schon gefilterte Textabschnitte |
| **Serper.dev** | API-Key | Echte Google-Ergebnisse als JSON, sehr stabil |
| **Brave Search** | API-Key | Unabhängiger Index, datenschutzfreundlich |
| **SearXNG** | eigene Docker-Instanz, URL eintragen | Komplett lokal, bündelt Google/Bing/DDG, kein Konto |

```bash
# SearXNG lokal (Beispiel):
docker run -d -p 8888:8080 -e SEARXNG_BASE_URL=http://localhost:8888/ searxng/searxng
# dann in Dive on Wide: Einstellungen → Web-Recherche → SearXNG-URL = http://localhost:8888
```

**Schritt 2 — Auslesen** (optional, für saubere Inhalte):

| Backend | Setup |
|---|---|
| **Firecrawl** | lokal via Docker (URL) oder API-Key — verwandelt jede JS-Seite in reines Markdown |
| **Jina Reader** | API-Key (`r.jina.ai`) |

Fällt ein konfiguriertes Backend aus, springt automatisch der schlüssellose Standard ein — die Recherche bricht nie ganz weg. Die **Diagnose** in derselben Karte zeigt live die aktive Quelle und ob eine Testsuche Treffer liefert.

## Die Pipeline „Web-Research Thinking"

Der mitgelieferte Vorzeige-Workflow zeigt, wie Recherche und Nachdenken zusammenspielen:

```
🤖 Recherche-Analyst   Thema in Teilfragen zerlegen, Wissenslücken benennen
🖥 Browser             die wichtigste offene Frage im Web nachschlagen
🤔 Denkschritt         ordnen: belegt / unbelegt / widersprüchlich / offen
🖥 Browser             gezielt die offene Frage klären, auch Gegenquellen suchen
🤖 Kritiker            trennen: was ist belegt, was hat das Modell ergänzt
🤖 Synthese-Agent      Bericht mit Antwort, Quellen und Verlässlichkeitsurteil
```

Der **Denkschritt** (🤔) ist dabei der Kern: Er bringt bewusst keine neuen Informationen ein, sondern sortiert das Vorhandene in vier Fächer — belegt, plausibel-aber-unbelegt, widersprüchlich, offen — und formuliert daraus die nächste Frage. Genau dieses Innehalten zwischen zwei Suchen unterscheidet Recherche vom Zusammentragen.

## Bildgenerierung

Der Baustein 🎨 **Bild** erzeugt aus dem bisherigen Ergebnis einen optimierten englischen Bildprompt. Ist in den Einstellungen eine **Bild-API URL** hinterlegt (z. B. eine lokale AUTOMATIC1111-Instanz unter `http://localhost:7860/sdapi/v1/txt2img`), erzeugt der Schritt echte Bilder und legt sie als Artefakt ab. Ohne Eintrag liefert er ehrlich nur den Prompt — es wird nichts vorgetäuscht.

## Netzwerk- und Datensicherheit

**Keine Telemetrie.** Dive on Wide schickt nichts über dich oder deine Nutzung irgendwohin — keine Statistik, keine Absturzberichte, keine Update-Abfragen. Ins Netz geht es nur für das, was du selbst startest (Websuche, ein Cloud-Anbieter, den du einträgst, Discord, ein Modell-Download); jede solche Anfrage steht im Ausgangsbuch — Empfänger, Art und Umfang, nie der Inhalt.

**Was verlässt den Rechner?** Jede Anfrage, die Dive on Wide nach draußen schickt (Websuche, abgerufene Seiten, Cloud-Modelle, Messenger, Modell-Downloads, fremde Diver), steht im **Ausgangsbuch** unter **Einstellungen → Ausgang**; das Dashboard zeigt „Heute nach draußen“. Wie viel hinausgehen darf, legst du dort in vier Stufen fest (siehe `docs/AUSGANG.md`).

Dive on Wide lauscht auf allen Schnittstellen, damit du die Oberfläche auch vom Tablet im selben WLAN erreichst. Daraus folgen drei Schutzmaßnahmen, die fest eingebaut sind:

- **Zugangsschlüssel für alle fremden Geräte.** Vom eigenen Rechner aus (localhost) ist keine Anmeldung nötig. Jede Anfrage von einem anderen Gerät braucht einen Schlüssel aus **Einstellungen → Zugänge** — dort legst du pro Alpha-Tester einen eigenen an (👑 Besitzer / 🧪 Gast) und löschst ihn nach dem Test. Gäste dürfen alles benutzen, aber keine Einstellungen ändern, keine Zugänge verwalten und kein Backup ziehen. Wer auch localhost absichern will, setzt in den Einstellungen „Auch auf diesem Rechner Schlüssel verlangen“. Zusätzlich werden Cross-Origin-Anfragen fremder Webseiten abgewiesen.
- **Web-Abrufe nur über http/https.** `file://`, `data:`, `ftp:` und ähnliche Schemata werden abgewiesen — sonst ließen sich über die Recherche-Funktion beliebige lokale Dateien auslesen.
- **Interne Adressen sind blockiert.** Zugriffe auf `localhost`, private Netze und Link-Local-Adressen (inklusive Cloud-Metadaten-Endpunkte) werden verweigert, auch nach Weiterleitungen. Wer bewusst ein lokales Wiki abrufen will, setzt `ALLOW_LOCAL_FETCH=1`.

Zusätzlich sind Anfragekörper auf 32 MB begrenzt, Dateinamen werden auf einen harmlosen Kern reduziert, und die Zahl gleichzeitiger Modellläufe ist über `MAX_PARALLEL_RUNS` gedeckelt, damit ein Schwung Aufgaben den Rechner nicht lahmlegt.

## Sicherheit der Code-Sandbox

Die Sandbox führt Code **wirklich auf deinem Rechner** aus. Sie begrenzt ihn auf den Workspace-Ordner und ein Zeitlimit — das ist eine Arbeitsgrenze, keine Sicherheitsgrenze. Prüfe generierten Code, bevor du ihn laufen lässt. Für echte Isolierung: Docker installieren und in den Einstellungen „Docker-Container" wählen (läuft dann ohne Netzwerk, mit Speicher- und CPU-Limit). Wer die Funktion nicht braucht, schaltet sie in den Einstellungen ab.

## Die Nachtschicht: aus Läufen wird dauerhaftes Wissen

Dive on Wide merkt sich heute zweierlei: kurze Notizen (`merken`) und den Volltext aller früheren Werkbank-Läufe (`erinnern`). Beides entsteht aber nur, wenn jemand im Lauf daran denkt. Was fehlte, ist der Schritt danach: **hinsetzen, die letzten Läufe durchsehen und festhalten, was beim nächsten Mal Zeit spart.**

Das ist die Nachtschicht (Rhythmus → *Nachtschicht*). Drei Regeln halten sie davon ab, die Sorte unbeaufsichtigte Automatik zu werden, die dieses Projekt nicht baut:

1. **Die Fakten kommen ohne Modell.** Wie viele Läufe, wie viele fertig, welche Werkzeuge, welche Dateien immer wieder, woran es scheiterte — gezählt, nicht erzählt. Selbst wenn das Modell Unsinn liefert, ist der Bericht wahr.
2. **Das Modell darf nur vorschlagen.** Jeder Vorschlag wird geprüft: Länge, Dubletten gegen den Bestand, keine Fragen, keine Floskeln. Was durchfällt, steht **mit Grund** im Bericht — nicht im Gedächtnis.
3. **Übernommen wird nur mit Erlaubnis.** Mit `KONSOLIDIERUNG_UEBERNEHMEN=0` (Standard) schreibt die Nachtschicht nichts, sie legt einen Bericht hin. Gelöscht wird nie etwas: Widersprüche werden benannt, nicht ausgeführt.

Ein echter Lauf über drei Werkbank-Aufträge (lokales 12-B-Modell):

```
| Läufe | 3 |  | davon fertig | 3 (100 %) |  | Schritte je Lauf | 7.7 |
Werkzeuge: ausfuehren 6×, schreiben 6×, ersetzen 3×, lesen 3×
Immer wieder angefasst: datensatz_pruefer.py (2×), test_datensatz_pruefer.py (2×)

Vorgeschlagene Notizen
- Fehler bei doppelter Ausgabe oder redundanten Aufrufen in main() müssen gezielt korrigiert werden.
…
Nicht übernommen — die Nachtschicht darf nur vorschlagen.
```

Jederzeit nachsehen, ohne der Nachtschicht den Zeitraum wegzunehmen:

```bash
python3 dowos_cli.py konsolidieren --tage 7
```

---

## Arbeit auslagern, Daten nicht

Dive on Wide läuft auf deinem Rechner — und es kann zusätzlich **von einem fremden Gerüst befehligt werden**: Claude Code, ein anderer Agent, ein Skript. Es schickt eine Aufgabe, dein **lokales** Modell arbeitet, und nur die Antwort geht zurück. Für die Gegenseite ist das billig (gemessen: ~150–300 Token über die Schnittstelle gegen 1 000–3 000 **je Klick**, wenn ein Gerüst die Oberfläche mit Bildschirmfotos bedient).

Der naheliegende Einwand ist der richtige: Was hinausgeht, ist draußen. Ein fremdes Gerüst kann nicht glaubhaft versprechen, etwas nicht zu lesen. Deshalb gilt:

> **Die Seite, die die Daten besitzt, entscheidet, was hinausgeht — nicht die Seite, die fragt.**

Vier Stufen unter Einstellungen → Ausgang (`AUSGANG_STUFE`), Voreinstellung **aus**:

| Stufe | Was ein fremdes Gerüst erfährt |
|---|---|
| `aus` | Nichts. Aufträge von außen werden abgewiesen. |
| `urteil` | Nur Maschinenfakten: fertig oder gescheitert, Schritte, Dauer, Werkzeugzählung, **Anzahl** geänderter Dateien. Kein Freitext, keine Dateinamen. |
| `zusammenfassung` | Dazu der Abschlusssatz, die Schrittliste und die **Namen** der geänderten Dateien. Keine Inhalte, keine Befehlsausgaben. |
| `alles` | Das vollständige Protokoll wie in der Oberfläche. |

Derselbe Auftrag ist auf Stufe `urteil` 285 Zeichen lang: Das Gerüst weiß, dass die Arbeit fertig ist, und nicht, wie die Datei heißt. Jede Antwort läuft zusätzlich durch einen Filter (private Schlüssel, API-Schlüssel, `*_KEY=`-Werte, Mailadressen, Heimatpfade) und landet im **Ausgangsbuch** mit Empfänger, Stufe und Zeichenzahl — sichtbar in den Einstellungen, im Lagebild und über `python3 dowos_cli.py ausgang buch`. Ein Gerüst-Schlüssel kommt nur an `/api/extern/…`; Einstellungen, Chats, Dokumente und Dateien antworten mit 403.

Alles dazu in [`docs/AUSGANG.md`](docs/AUSGANG.md).

---

## Wenn ein Modell nicht in den Speicher passt

Der häufigste Fehlschlag auf einem Notebook ist kein Programmfehler: Das Modell ist zu groß für den freien Speicher. Ollama beantwortet so einen Lauf mit einer nackten `HTTP 500`; der wirkliche Grund (`panic: mlx: [METAL] … Insufficient Memory`, danach `runtime OOM detected`) steht nur in seinem eigenen Protokoll. Eine Pipeline, die nach sechs Minuten mit „HTTP Error 500: Internal Server Error" stirbt, ist für den Besitzer eine Sackgasse.

Dive on Wide macht deshalb dreierlei:

1. **Übersetzen.** Jeder Modellfehler nennt Modell, Ursache und Abhilfe (kleineres Modell, kleineres `NUM_CTX`, nichts anderes gleichzeitig rechnen lassen) und zitiert Ollamas eigene Fehlerzeile, wenn sie frisch ist.
2. **Überleben.** Bei Speichernot werden die geladenen Modelle freigegeben und der Schritt **einmal** wiederholt — mit kleinerem Modell, wenn wirklich der Speicher zu klein war. Das gilt für den Orchestrator-Plan, für jeden Pipeline-Schritt und für Deep Research. Still passiert das nie: Der Ausweg steht als Schritt im Protokoll, und der Bericht nennt das Modell je Schritt.
3. **Wählen lassen.** `AUSWEICH_MODELL` (Einstellungen → Modelle oder `.env`) legt das Ersatzmodell fest. Ohne Eintrag nimmt Dive on Wide das kleinste installierte Modell ab 2 GB — ein 0,5-B-Zwerg bringt keinen Plan zustande.

Deep Research meldet außerdem seine Zeiten: Je Runde steht da, wie lange Anfrage, Suche, Auszug, Hypothesen und Synthese gebraucht haben. Die mechanischen Aufrufe laufen ohne lautes Mitdenken (gemessen: 26–140 s → 2–4 s je Aufruf) — daher kamen die Acht-Minuten-Runden.

### Kleine Rechner (8 GB, ohne Grafikkarte)

Dive on Wide läuft dort — langsam, aber vollständig: gemessen in einer Linux-VM mit 8 GB und 4 CPU-Kernen mit `qwen3:4b`.
Die Einrichtung erkennt das und schlägt kleine Modelle vor; der Kontext wird auf 8 192 Token gestellt. **Wichtig für
lange Läufe:** Ollamas Prompt-Cache abschalten (`LLAMA_ARG_CACHE_RAM=0`, Anleitung je System in `docs/FAQ.md`),
sonst beendet das System den Modellprozess nach einiger Zeit wegen Speichermangel.

| | Ergebnis (8 GB, CPU) |
|---|---|
| Chat | 15–90 s je Antwort |
| Werkbank: kleiner Fehler mit Test | 2,5–21 min, grün |
| Roadmap mit 2 Paketen (Cache aus) | beide grün, rund 90 min |
| Lotse | 24/24 Fragen richtig, ~100 s je Antwort |

### Ein großes Modell als Datei (GGUF), wenn Ollama zu viel Speicher nimmt

Manche Modelle passen knapp in den Speicher, laufen unter Ollama aber voll — Hybridmodelle wie Qwen3.8 legen je
Agentenschritt Zwischenstände an, die Ollama nicht begrenzen kann. Für solche Fälle gibt es unter **Einstellungen →
Modelle & Provider → „GGUF-Datei als Modell“** einen eigenen Anbietertyp: Pfad zur `.gguf`-Datei und Kontextgröße
eintragen. Dive on Wide startet `llama-server` erst, wenn das Modell gewählt wird, gibt vorher die Ollama-Modelle frei, beendet
ihn nach 5 Minuten Leerlauf (`GGUF_LEERLAUF_S`) und vor jeder Anfrage an ein lokales Ollama-Modell. Braucht `llama-server`
(macOS: `brew install llama.cpp`).

Gemessen (Mac mini M4 Pro, 24 GB): Qwen3.8 27B in 4 Bit läuft so stabil bis 15 000 Token (16,3 GiB, ~10 Token/s) und löst
41 von 72 Werkbank-Aufgaben — so viele wie Qwen 3.6 35B, aber gut viermal langsamer. Für die Werkbank bleibt der 35B die
Empfehlung.

---

## Hinweis zu Halluzinationen

Lokale Modelle erfinden ohne Web-Zugriff überzeugend klingende Fakten — inklusive Bibliotheken und Paketnamen, die es nicht gibt. Dagegen helfen drei eingebaute Werkzeuge: der 🌐-**Web-Toggle** im Chat, der Skill **@faktencheck** (markiert Behauptungen mit 🟢🟡🔴) und der Agent **Kritiker**, der Entwürfe gezielt auf erfundene Technologien prüft. Die Pipeline „Software-Werkstatt" schaltet den Kritiker automatisch zwischen Entwurf und Endfassung.

## Architektur

```
Browser (frontend/index.html — Vanilla-JS-SPA)
   │  REST + NDJSON-Streaming
   ▼
server.py (Python-Stdlib-HTTP-Server, Port 3000)
   ├── SQLite  → storage/dowos.db      (Chats, Agenten, Prompts, Skills, …)
   ├── Dateien → storage/artifacts/    (generierte Artefakte)
   └── Proxy   → Ollama REST-API       (http://localhost:11434)
```

Kontrollfluss einer Eingabe:
`Eingabe → Parser (@Skill | /Prompt) → Kontext-Injektion (Agent/Wissen) → Ollama-Pipeline → Antwort-Streaming / Artefakt-Generierung`

## Konfiguration (.env)

```ini
PORT=3000                                  # UI-Port
OLLAMA_BASE_URL=http://localhost:11434     # Ollama-Adresse
DEFAULT_MODEL=qwen2.5-coder:14b            # Standardmodell
NUM_CTX=16384                              # Kontextfenster
TEMPERATURE=0.7                            # Kreativität
```

UI-Einstellungen (unter ⚙️ Einstellungen) überschreiben die `.env`-Werte und werden in der Datenbank gespeichert.

## Optional: Docker

```bash
# In .env setzen: OLLAMA_BASE_URL=http://host.docker.internal:11434
docker compose up
```

## Weitere Unterlagen

| Datei | Inhalt |
|---|---|
| [`docs/ANWENDUNGEN.md`](docs/ANWENDUNGEN.md) | Zehn Alltagsaufgaben, gemessen: Hausordnung fragen, Kontoauszug auswerten, Screenshot nachbauen, Code-Review, ein Spiel aus einer Idee — mit den Handgriffen, die wirken |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Die vier Ideen, die den Code zusammenhalten — der Fluss, der Provider-Router, die zweistufige Web-Bridge, die Mesh-Schichten |
| [`docs/ERSTES_ERGEBNIS.md`](docs/ERSTES_ERGEBNIS.md) | Die erste gemessene Modellverbesserung (0 % → 13 % auf zurückgehaltenen Puzzles) — samt der zwei Fehlschläge davor und warum ein fallender Verlust nichts hieß |
| [`docs/LEHREN.md`](docs/LEHREN.md) | Zehn Regeln aus den Messläufen, jede mit der Zahl, aus der sie stammt — geschrieben für Agenten, die ohne Vorwissen dazukommen |
| [`docs/AUSGANG.md`](docs/AUSGANG.md) | Arbeit an das lokale Modell auslagern, ohne Daten auszulagern: vier Ausgangsstufen, Filter, Ausgangsbuch |
| [`SECURITY.md`](SECURITY.md) | Bedrohungsmodell, was schützt und was **nicht** — die Sandbox ist eine Arbeitsgrenze, keine Sicherheitsgrenze |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Grundregeln: keine Abhängigkeiten im Kern, nie etwas vortäuschen, jedes Feature mit einem Test, der auch fehlschlagen kann |
| [`CHANGELOG.md`](CHANGELOG.md) | Was sich geändert hat, samt der Fehler — das ist der lesenswerte Teil |

## Tipps

- **Modelle mischen:** Jedem Agenten und Skill kann ein eigenes Modell zugewiesen werden — z. B. `qwen2.5-coder:14b` für Code, `llama3` für Kreatives.
- **Zugriff im Netzwerk:** Der Server bindet auf `0.0.0.0` — andere Geräte im LAN erreichen die UI unter `http://<deine-IP>:3000`.
- **Backup:** Einfach den Ordner `storage/` sichern — dort liegt die gesamte Datenbank plus alle Artefakte.
- **Zurücksetzen:** `storage/dowos.db` löschen → beim nächsten Start werden die Seed-Daten (Agenten, Skills, Templates) neu angelegt.
