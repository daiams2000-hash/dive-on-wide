# Häufige Fragen

Kurze Antworten mit dem Weg in der Oberfläche. Ausführlich steht es in README und docs/ — der Lotse (🧭) liest beides.

### Was schickt Dive on Wide ins Internet, und wo sehe ich das?

Alles, was den Rechner verlässt (Websuche, abgerufene Seiten, Cloud-Modelle, Messenger, Modell-Downloads, fremde Diver), steht im **Ausgangsbuch** — Empfänger, Art und Umfang, nie der Inhalt — unter
**Einstellungen → Ausgang**. Das Dashboard zeigt „Heute nach draußen“. Wie viel hinausgehen darf, legst du dort in vier
Stufen fest. Lokale Modelle über Ollama schicken nichts nach draußen.

### Wie deinstalliere ich Dive on Wide?

Dive on Wide beenden, dann `python3 install.py --entfernen` (Windows: `py install.py --entfernen`). Der Installer sichert
zuerst deine Daten als Zip und fragt jedes Teil einzeln ab. Ollama, git und deine Ollama-Modelle bleiben.

### Welches Modell passt zu meinem Rechner?

Der Lotse rechnet es mit denselben Regeln wie die Einrichtung: Auf einem Mac bekommt ein Modell etwa drei Viertel des
Arbeitsspeichers, auf einem PC ohne Grafikkarte etwa die Hälfte, mit Grafikkarte deren Speicher. „Knapp“ heißt: läuft,
aber große Programme daneben schließen. Frag einfach „Welches Modell passt auf …“.

### Warum ist die Werkbank aus?

Weil sie Code auf deinem Rechner ausführt. Einschalten unter **Einstellungen → Code-Sandbox**. Unter macOS und Linux
laufen die Befehle in einer Sandbox ohne Netz; unter Windows fragt die Werkbank vor jedem Befehl.

### Wie bringe ich meine lokalen Modelle in meinen Discord?

**Einstellungen → Discord-Server-Chat**: Bot anlegen, Token eintragen, Einladungslink öffnen, Kanäle ankreuzen. Jede
Frage öffnet einen privaten Chat; das Modell wählt man aus einer Liste.

### Warum antwortet der Discord-Bot nicht?

Läuft Dive on Wide nicht, ist der Bot offline. Antwortet er nur auf @Erwähnung, fehlt im Developer Portal der „Message Content
Intent“. Im Kanal muss er freigegeben sein (Einstellungen → Discord-Server-Chat → Kanäle).

### Ein Modell ist zu groß — was tun?

Ein kleineres wählen (der Lotse nennt eines, das bequem passt), `NUM_CTX` verkleinern oder nichts anderes gleichzeitig
rechnen lassen. Für Modelle, die unter Ollama den Speicher sprengen: **GGUF-Datei als Modell** (Einstellungen → Modelle &
Provider).

### Wie mache ich die Websuche zuverlässig?

Ohne eigenen Suchdienst hängt die Suche an DuckDuckGo und dessen Bot-Prüfung. Eine eigene SearXNG-Instanz oder ein
API-Schlüssel (Brave, Tavily, Serper) unter **Einstellungen → Recherche** macht sie stabil.

### Lernt Dive on Wide von mir?

Nur wenn du es einschaltest, und nur auf diesem Rechner. Die Nachtschicht schlägt Notizen aus deinen Werkbank-Läufen vor;
übernommen wird nur, was du freigibst.

### Mein Rechner hat wenig Speicher (8 GB) — worauf achten?

Kleine Modelle (um 4 B) wählen; die Einrichtung schlägt sie vor und stellt den Kontext auf 8 192 Token. Für lange
Werkbank-Läufe und Roadmaps **Ollamas Prompt-Cache abschalten**, sonst wächst der Modellprozess über viele Schritte,
bis das System ihn beendet (gemessen in einer 8-GB-Linux-VM: Abbruch bei 7,6 GB; mit abgeschaltetem Cache stabil bei
4,7 GB). Die Umgebungsvariable heißt `LLAMA_ARG_CACHE_RAM=0`:

- **Linux:** `sudo systemctl edit ollama`, dort `[Service]` und `Environment="LLAMA_ARG_CACHE_RAM=0"` eintragen, dann
  `sudo systemctl restart ollama`. (So nachgemessen.)
- **macOS:** `launchctl setenv LLAMA_ARG_CACHE_RAM 0`, danach die Ollama-App beenden und neu öffnen.
- **Windows:** Systemsteuerung → Umgebungsvariablen → neue Benutzervariable `LLAMA_ARG_CACHE_RAM` mit Wert `0`, dann
  Ollama beenden und neu starten.

Auf CPU ohne Grafikkarte ist alles langsamer: gemessen 15–90 s je Chatantwort, 2–21 min für eine kleine
Werkbank-Reparatur, rund 90 min für eine Roadmap mit zwei Paketen.
