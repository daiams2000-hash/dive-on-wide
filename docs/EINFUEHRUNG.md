# Einführung in Dive on Wide

Diese Seite liest der Starter-Assistent, wenn jemand fragt „Was ist Dive on Wide?“ oder „Erkläre mir Dive on Wide“.
Sie beschreibt jeden Bereich der Oberfläche. Ein Test prüft, dass kein Menüpunkt fehlt.

## Was ist Dive on Wide?

Dive on Wide ist ein KI-Arbeitsplatz, der **auf deinem eigenen Rechner** läuft. Deine Modelle laufen lokal (über
Ollama, eine GGUF-Datei oder einen anderen lokalen Server wie LM Studio oder vLLM), deine Daten bleiben bei dir.
Der Name heißt: neugierig bleiben und immer tiefer in eine Sache hineintauchen.

Es ist mehr als ein Chatfenster. Die Modelle tun etwas:
- Aufgaben über mehrere Modelle planen,
- Code schreiben, ausführen und prüfen,
- im Netz recherchieren,
- auf Wunsch den Bildschirm bedienen und Rechenleistung mit deinen anderen Geräten teilen.

Alles, was deinen Rechner, dein Netz oder deine Daten anfasst, ist **ab Werk aus**. Die Einrichtung fragt einmal,
was Dive on Wide darf. Es gibt keine Telemetrie. Was doch nach draußen geht (Websuche, Cloud-Anbieter), steht im
Ausgangsbuch.

## Die Bereiche

### Chat
Mit jedem Modell schreiben. Du wählst oben Modell und Diver. Mit `/` holst du Prompts und Wissen, mit `@` startest du
Skills, mit ＋ öffnest du Werkzeuge (Web, Orchestrator, Deep Research, Coding-Diver, Browser). Passt etwas aus deinem
**Wissen** zur Frage, wird es von selbst mitgegeben, und unter der Antwort steht, was genutzt wurde.

### Dashboard und Inbox
Das **Dashboard** zeigt den echten Zustand aller Bausteine: Verbindung zu den Modellen, was an und was aus ist, letzte
Läufe und Artefakte. Die **Inbox** sammelt Meldungen fertiger Hintergrundläufe.

### Templates, Diver, Prompts, Skills
- **Prompts:** gespeicherter Text, im Chat mit `/` abrufbar. Tut selbst nichts.
- **Templates:** Formulare (z. B. Blog-Optimierer). Du füllst Felder aus, daraus wird ein Prompt.
- **Diver im Chat:** Rollen mit eigenem Systemprompt und Modell, z. B. Code-Experte. Sie bestimmen, wer antwortet —
  sie lesen keine Dateien und führen nichts aus.
- **Skills:** mehrstufige Abläufe, die im Hintergrund laufen und ein Artefakt liefern.
- **Werkbank-Diver** (erkunder, reviewer, tester …) handeln wirklich: Sie lesen, schreiben, führen Befehle aus und
  prüfen mit Tests. Sie stehen auf der Diver-Seite unter den Rollen.

### Dokumente & Artefakte
Alles, was Läufe erzeugen (Berichte, Code, Pläne), liegt hier als Datei. Du kannst es ansehen, herunterladen und im
Chat wieder anhängen.

### Wissen
Deine eigene Wissensbasis, komplett offline. Dateien und ganze Ordner ziehst du einfach hinein. Der Chat sucht darin
von selbst, wenn eine Frage dazu passt. Der Ordner **Second Brain** übernimmt auf Wunsch Erkenntnisse aus deinen Chats.

### Pipelines und Orchestrator
**Pipelines** verketten Schritte: Diver, Web-Recherche, Deep Research, Coding-Diver, Browser, Bild. Jeder Schritt darf
ein anderes Modell nutzen. Der **Orchestrator** baut so einen Ablauf selbst aus einem Ziel in deinen Worten und wählt
für jeden Schritt das passende Modell.

### Schwarm
Mehrere Diver bauen gemeinsam: Ein Planer zerlegt dein Ziel in Dateien, kleine Diver schreiben je eine mit eigenem,
frischem Kontext, der Planer führt zusammen, prüft mit Tests und schickt bei Bedarf eine weitere Runde los. Auf einem
Rechner ist das nicht schneller als ein einzelnes Modell — der Gewinn sind kleine, saubere Kontexte; schneller wird es
erst mit mehreren Geräten. Im Chat über ＋ → Schwarm.

### Deep Research
Ein Recherche-Diver in Runden: Teilfragen planen, im Netz suchen, zusammenfassen, Lücken in die nächste Runde. Am Ende
steht ein Bericht mit Quellen.

### Werkbank
Der Diver für echte Projekte, wie Claude Code oder Codex, aber mit deinem lokalen Modell. Er liest, sucht, ändert genau
eine Stelle, führt Tests aus und meldet erst „fertig“, wenn geprüft ist. **Roadmap abarbeiten:** Eine Liste von Paketen
wird nacheinander erledigt, nach jedem laufen die Tests. Die Werkbank ist ab Werk aus. Unter macOS und Linux laufen
Befehle in einer Sandbox ohne Netz, unter Windows fragt sie vor jedem Befehl. Auch im Terminal: `dowos werkbank "…"`.

### Code-Sandbox
Arbeitsordner mit Editor, Dateiliste und Konsole, in denen der Coding-Diver Code ausführt.

### Rhythmus
Dive on Wide arbeitet auch, wenn niemand zusieht: täglich zu einer Uhrzeit, in einem Abstand oder einmalig. Ein
Diver, Skill, Orchestrator oder Werkbank-Auftrag läuft dann von selbst, z. B. als Morgen-Briefing. Ein Eintrag kann
auf dem letzten Ergebnis anderer Einträge **aufbauen**: Ein Diver recherchiert morgens, ein zweiter lädt das abends
und widerspricht, ein dritter vergleicht beide.

### Netzwerk (Diving Net und Weite)
Optional teilst du Rechenleistung mit deinen eigenen Geräten. Peer-to-peer, verschlüsselt, ohne zentralen Server,
nichts auf der Platte. Das **Diving Net** ist dein eigenes Netz zu Hause: Geräte finden sich von selbst. Die
**Weite** ist das offene Netz, betreten über einen Anker, den man persönlich tauscht.

### Bote und Forum
Der **Bote** ist ein verschlüsselter Messenger über das Netz, ohne Konto und ohne Telefonnummer. Im **Forum** gibt es
Fäden, die von selbst verfallen, mit einem eigenen Pseudonym je Faden.

### Modelle
Welche Modelle installiert sind, welche zu deinem Rechner passen und wie gut sie hier gemessen abschneiden. Ein
Modell lässt sich mit einem Klick laden. Anbieter (Ollama, LM Studio, vLLM, llama.cpp, eine GGUF-Datei, auf Wunsch
Cloud-Dienste) richtest du unter Einstellungen → Modelle & Provider ein.

### Training
Aus guter Arbeit ein eigenes Modell machen. Gut bewertete Läufe werden ein Datensatz, darauf lernt ein kleines
Schülermodell (LoRA). Auf Wunsch löst ein stärkeres Lehrermodell Übungsaufgaben, die ein unabhängiger Prüfer
kontrolliert. Ein Lehrer aus der Cloud schickt die Aufgaben nach draußen. Darauf weist die Seite deutlich hin.

### Einstellungen
Modelle & Provider, Code-Sandbox, Ausgang (was nach draußen darf), Recherche, Zugänge für andere Geräte, Discord,
Telegram, Slack, Backup und Sprache.

## Hilfe

- **Lotse (🧭 unten rechts):** beantwortet Fragen zu Dive on Wide aus der Doku und rechnet aus, welches Modell auf
  deinen Rechner passt.
- **Häufige Fragen:** docs/FAQ.md.
- **Fehler gefunden?** Bitte auf GitHub (Issues) oder im Discord melden. Dive on Wide ist ein Hobbyprojekt in einer
  frühen Version, Rückmeldungen helfen am meisten.

## Erste Schritte

1. Unter **Modelle** prüfen, ob ein passendes Modell da ist. Sonst schlägt die Seite eines vor.
2. Im **Chat** etwas fragen, dann mit ＋ den **Orchestrator** ein Ziel lösen lassen.
3. Ein paar Dateien ins **Wissen** ziehen und im Chat danach fragen.
4. Wer programmiert: die **Werkbank** einschalten und ihr eine kleine Aufgabe in einem Projekt geben.
