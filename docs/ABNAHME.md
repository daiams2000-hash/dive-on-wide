# Abnahme des Harness — was wirklich geprüft ist

431 grüne Tests sagen, dass der Code tut, was die Tests erwarten. Sie sagen nicht, dass die Fähigkeit im echten Betrieb trägt — dieselbe Lücke, die im Trainingsteil eine schöne Verlustkurve von einem nutzlosen Modell trennte (siehe [`LEHREN.md`](LEHREN.md), Regel 1).

Dieses Dokument hält deshalb je Fähigkeit fest: **was sie verspricht, wie sie geprüft wurde, und was daran noch nie in echt lief.** Status ist bewusst dreiwertig:

- **belegt** — an dieser Maschine ausgeführt, mit Ergebnis
- **getestet** — durch Tests abgedeckt, aber nie im echten Betrieb benutzt
- **offen** — weder das eine noch das andere

---

## Teil 1: Vollständig lokal

| Fähigkeit | Status | Beleg |
|---|---|---|
| Oberfläche und Lagebild | **belegt** | Frischklon startet auf freiem Port, HTTP 200, Lagebild meldet Fähigkeiten korrekt (Sandbox an, Docker aus, Ausgang aus) |
| Lokale Modelle | **belegt** | 12 Modelle über Ollama erreichbar, Chat und Agentenläufe durchgeführt |
| Werkbank-Agent in der Sandbox | **belegt** | Hunderte echte Läufe: 295 Aufgabenversuche in der Datensammlung, 51–59 % bestanden, Tests als Schiedsrichter |
| Skills (`SKILL.md`) | **belegt** | Skill in `.dowos/skills/` angelegt, von Dive on Wide erkannt; Suchorte zusätzlich `.agents/skills` und `.claude/skills` |
| Eigene Befehle (Slash) | **belegt** | `/lehren` und `/pruefe` angelegt, beide erkannt samt Beschreibung und Herkunftsdatei |
| Regeln und Hooks | **belegt** | Vollständige Vertrauenskette geprüft, siehe unten |
| Gedächtnis (Notizen) | **belegt** | Fünf Notizen hinterlegt, erscheinen im Systemprompt jedes Laufs (740 Zeichen) |
| Rhythmus (Zeitpläne) | **belegt** | Nachtschicht als Zeitplan angelegt und sofort ausgeführt, Bericht erzeugt |
| Nachtschicht (Konsolidierung) | **belegt** | Echter Lauf über drei Werkbank-Aufträge, 4 Vorschläge, nichts ohne Erlaubnis übernommen |
| Code-Sandbox | **belegt** | `sandbox-exec`, ohne Netz; Agenten scheiterten reproduzierbar an `pip` — genau wie vorgesehen |
| Checkpunkte | **belegt** | Zwei Checkpunkte automatisch angelegt, per CLI zurückgesetzt (Datei wieder kaputt, Test wieder rot), dann rückgängig gemacht — **jedes Zurückrollen wird selbst zum Checkpunkt** |
| Schrittprotokoll | **belegt** | Werkzeug, Ziel, Dauer, Token, Checkpunkt-Marke und Gedanke je Schritt |
| Wiederholung ohne Modell | **belegt** | `wiederholen` spielte den Lauf nach: „5 Schritte — gleich ✅" |
| Abzweigen | **belegt** | Abzweig bei Schritt 2 mit anderer Anweisung: Projekt zurückgesetzt, 4 Schritte, alternative Lösung (eigene `sicher_text`-Funktion), Tests grün |
| Regeln und Hooks im echten Lauf | **belegt** | Vertrauensfrage beantwortet → „Projektregeln aktiv", Hook `nach_aenderung` feuerte nach der Änderung |
| Browser-Agent | **belegt** | Zwei Steuerungen (Dive-on-Wide-Elementliste, browser-use), echte Recherchen gefahren |
| Computer-Use | offen | Nur auf macOS erprobt, Grounding-Server nicht gestartet; Linux/Windows nie |

### Die Vertrauenskette für Projektregeln (geprüft am 22.09.2026)

Ein geklontes fremdes Repository darf dem Agenten nichts erlauben und keine Hooks einschleusen. Gemessen, nicht behauptet:

| Situation | verbieten | erlauben | Hooks |
|---|---|---|---|
| Nicht vertraut, niemand fragt | leer | leer | keine |
| Nutzer sagt Nein | leer | leer | keine |
| Nutzer sagt Ja | 3 Regeln | 1 Regel | 1 Hook |
| Danach, ohne erneute Frage | bleibt aktiv | bleibt aktiv | bleibt aktiv |
| **Datei nachträglich geändert** | leer | leer | keine |

Zusätzlich: Eine fehlerhafte Regeldatei wird mit Begründung abgewiesen („Hook `nach_aenderung`: jede Angabe braucht `befehl`") statt still ignoriert.

---

## Teil 2: Von einem anderen Agenten gesteuert

| Fähigkeit | Status | Beleg |
|---|---|---|
| Ausgang (`/api/extern`) | **belegt** | Gerüst-Schlüssel angelegt, Auftrag von außen gestartet, vier Stufen gemessen (285 → 634 Zeichen), Schranke geprüft: derselbe Schlüssel bekommt an `/api/settings` 403 |
| Ausgangsbuch | **belegt** | Jede Antwort mit Empfänger, Stufe, Zeichenzahl protokolliert; Tagessumme im Lagebild |
| Filter (Schlüssel, Pfade, Mail) | **belegt** | Unit-geprüft an echten Mustern; harmlose Zeilen bleiben unangetastet |
| ACP (Zed, JetBrains, Neovim) | **belegt** | Protokoll direkt gesprochen: `initialize` liefert Fähigkeiten, `session/new` eröffnet eine Sitzung im Projekt samt wählbarer Rechtemodi. Noch nie in einem echten Editor-Fenster |
| Webhooks (GitHub, Gitea, CI) | **belegt** | Falsche Signatur → 401, keine → 401, echte → 202 mit Lauf, **dieselbe Zustellung erneut → „doppelt", kein zweiter Lauf**. Im Lauf griff die Rechtestufe „nur lesen": `ersetzen gesperrt` |
| MCP als Client | **belegt** | Gegen einen nach Spezifikation geschriebenen Fremdserver (kein Dive-on-Wide-Code): Werkzeuge entdeckt, aufgerufen, Ergebnisse korrekt, unbekanntes Werkzeug sauber abgelehnt. Kein Drittanbieter-Produkt — Node fehlt auf dieser Maschine |
| Externe Agenten (Claude Code, Codex) | offen | Anbindung vorhanden, braucht einmalige Anmeldung — nie ausgeführt |
| Boten (Telegram, Discord, Slack) | getestet | Ohne echte Zugangsschlüssel nie zugestellt |

---

## Teil 3: Mesh und Netzwerk-Compute

| Fähigkeit | Status | Beleg |
|---|---|---|
| Mesh-Paket (Kademlia, Transport, Krypto, Ratsche, Forum) | getestet | 101 Tests |
| **Zwei Knoten auf einer Maschine** | **belegt** | 22.09.2026, `werkzeuge/mesh_abnahme.py` |
| **Nachbarschaft über UDP-Multicast** | **belegt** | Beide Knoten sehen einander nach **1 Sekunde**, ohne Verzeichnisdienst |
| **Außenadresse (Durchstich)** | **belegt** | Knoten erkennt `192.168.0.100`, Antworten einig |
| **Speicherangebot** | **belegt** | Knoten bietet 6,4 GB an, Statthalter respektiert die Obergrenze |
| **Auftragsverteilung mit echter Fremdarbeit** | **belegt** | A verteilt, **B rechnet wirklich** (Rückruf belegt), Ergebnis in 1 s zurück |
| Zwei Geräte im selben Netz | offen | Multicast über einen echten Switch/WLAN ungeprüft |
| Verteilte Inferenz über mehrere Geräte | offen | Bisher nur mit einem Echo-„LLM", nie mit einem echten Modell über zwei Geräte |

### Was die erste Zwei-Knoten-Abnahme zutage förderte

Zwei Schnittstellen-Eigenheiten, die in keinem der 100 Einzeltests auffallen konnten und jeden Anwender kosten würden:

1. **Die Modellliste sind Namen, keine Objekte.** `modelle=lambda: [{"name": "x"}]` wird stillschweigend zu `"{'name': 'x'}"` — und kein anderer Knoten findet das Modell je wieder. Fehlermeldung: „Kein Knoten im Netz hat …", obwohl beide Knoten es haben.
2. **Der LLM-Rückruf ist `llm(modell, prompt)`** — Modell zuerst. Vertauscht rechnet der Knoten den Modellnamen als Prompt.

Beides ist im Server korrekt umgesetzt; beides ist jetzt durch einen Test festgenagelt, damit es so bleibt.

---

## Was die Abnahme an echten Fehlern fand

**1. „Vom Nutzer abgebrochen" — ohne dass jemand abgebrochen hatte.** Ein echter Werkbank-Lauf endete nach exakt 300 Sekunden mit dieser Meldung. Die Wahrheit: Das Projekt brachte eigene Einstellungen mit, Dive on Wide fragte nach Vertrauen, und die Frage stand unbeantwortet im Leeren. Im Protokoll stand kein einziger Schritt darüber — ein Betreiber hätte den Fehler garantiert an der falschen Stelle gesucht.

Das Abbrechen selbst ist richtig (keine Antwort = kein Vertrauen). Behoben wurde die Meldung:

```
Die Rückfrage blieb 5 Minuten unbeantwortet, deshalb wurde der Lauf
sicherheitshalber beendet — niemand hat ihn abgebrochen.

Gefragt war: Das Projekt bringt eigene Werkbank-Einstellungen mit …

Abhilfe: die Frage in der Oberfläche beantworten, oder den Lauf ohne
Aufsicht starten (dann wird nicht gefragt und Ungefragtes bleibt aus).
```

Dazu steht die Frage jetzt als Schritt im Protokoll („Wartet auf Freigabe (300 s): …"), nicht nur im flüchtigen Zustand. Die neue Ausnahme erbt von `RunCancelled`, damit alle bestehenden Abbruchpfade unverändert greifen.

**2. Zwei Mesh-Schnittstellen ohne Netz.** Modellliste als Namen statt Objekte, LLM-Rückruf als `llm(modell, prompt)` — beides in keinem der 101 Einzeltests sichtbar, beides jetzt festgenagelt.

**3. Ein Syntaxfehler in der Oberfläche blieb für alle 433 Tests unsichtbar.** Beim Durchklicken habe ich die Oberfläche selbst zerschossen: ein gerades Anführungszeichen mitten in einem deutschen Satz beendete eine JavaScript-Zeichenkette zu früh. Danach lud das Menü (statisches HTML), aber **kein Klick tat mehr etwas** — dasselbe Bild, das der Besitzer Tage zuvor gemeldet hatte. Die gesamte Testsuite blieb grün, denn sie prüft die Schnittstelle, nicht das Skript.

Neu: `tests/js_pruefer.py` prüft jede Zeichenkette im Skriptteil der Oberfläche. Der Automat unterscheidet reguläre Ausdrücke (`/"/g`), verschachtelte Template-Literale (`` `a ${b ? `c` : "d"}` ``) und Kommentare — sonst meldet er Unsinn, und Fehlalarme nimmt niemand ernst. Der Test prüft zusätzlich sich selbst: Er weist nach, dass der Prüfer einen bekannten Fehler noch findet.

**4. Ein stilles `catch()` beim Dienstarbeiter.** Die Anmeldung scheitert im eingebetteten Browserfenster (`fetch` auf `sw.js` liefert 200, der sichere Kontext stimmt — nur `register()` wird dort nicht erlaubt). Das war in einem leeren `catch(() => {})` verschwunden. Jetzt steht eine Erklärung in der Konsole, denn ein leeres catch ist genau das Schweigen, gegen das dieses Projekt gebaut ist. In einem gewöhnlichen Browser über `localhost` ist die Anmeldung erlaubt — ungeprüft.

**5. Die Einstellungen funkten bei jedem Öffnen ins Netz.** Gemessen beim Durchklicken: Die Ansicht brauchte **877 ms**, und in dieser Zeit stand die zuvor geöffnete Ansicht noch da. Einzelmessung der zehn Abfragen:

```
/api/settings            18 ms      /api/providers      4 ms
/api/health              30 ms      /api/ausgang        2 ms
/api/tokens               1 ms      /api/telegram       3 ms
/api/computer/diagnose  133 ms      /api/web/diagnose  751 ms  ←
```

`/api/web/diagnose` stellt eine **echte Suchanfrage**. Das ist nicht nur langsam: Ein Werkzeug, das lokal arbeitet, sollte nicht ungefragt ins Netz funken, bloß weil jemand seine Einstellungen anschaut. Behoben zweifach — die übrigen Abfragen laufen jetzt gleichzeitig statt nacheinander, und die Suchprobe läuft nur auf Knopfdruck, mit dem Satz „Die Probe stellt eine echte Suchanfrage ins Netz" daneben. **877 ms → 175 ms.** Der Knopf liefert weiterhin „● Aktiv — Quelle: keyless (Mojeek/DDG/Wikipedia) (Testsuche: 3 Treffer)".

**6. Alle 19 Ansichten rendern fehlerfrei.** Nach der Reparatur des Syntaxfehlers durchgeklickt: Dashboard, Inbox, Templates, Agenten, Dokumente, Prompts, Skills, Wissen, Pipelines, Deep Research, Werkbank, Code-Sandbox, Rhythmus, Netzwerk, Bote, Forum, Modelle, Training, Einstellungen — kein einziger JavaScript-Fehler, Renderzeiten 53–207 ms außer Dashboard (512 ms) und Einstellungen (jetzt 175 ms).

**7. Zwei Tests hängen an fremden Gruppen.** `laeufe` fällt isoliert durch, weil `G["triggers"]` und `G["agents"]` von `seeds`/`crud` stammen. Im Gesamtlauf grün, aber `--nur laeufe` ist irreführend. Notiert, nicht behoben.

---

## Zwei Fehlalarme — und warum sie hier stehen

Beim Prüfen der Oberfläche habe ich zweimal einen Defekt zu sehen geglaubt, wo keiner war. Beide Irrtümer kosten den nächsten Prüfer sonst dieselbe Stunde:

**„Enter sendet nicht."** Getippter Text landet im Feld, aber der Druck auf Enter bleibt wirkungslos. Der Mitschnitt der Tastaturereignisse zeigte: Das Ereignis kommt mit `key: ""` und `keyCode: 0` an — das eingebettete Browserfenster der Prüfautomatik sendet keine echten Sondertasten. Dive on Wide prüft korrekt auf `key === "Enter"`; ein künstlich erzeugtes Ereignis sendet sofort. **Tastenkürzel sind über dieses Werkzeug nicht prüfbar** — dafür braucht es einen echten Browser und eine echte Tastatur.

**„Der Chat-Verlauf ist leer."** `state` ist eine Modulvariable, kein `window.state`. Jede Abfrage der Form `window.state && state.messages` liefert deshalb `[]`, während die Oberfläche längst Frage und Antwort zeigt. Richtig ist der bloße Bezeichner `state`. Der Server hatte die Sitzung die ganze Zeit korrekt geführt (`POST /api/chat` → 200, Antwort gespeichert).

Der Chat selbst ist damit **belegt**: Frage gestellt, Antwort erhalten („Sokoban ist ein klassisches Puzzle-Spiel, bei dem der Spieler Kisten … schieben muss."), Sitzung angelegt, Verlauf im Fenster sichtbar.

---

## Der Frischklon: Dive on Wide zum ersten Mal, ohne Vorwissen

Alles Bisherige lief in einem Ordner, der seit Wochen benutzt wird. Das ist nicht der Weg, den ein neuer Nutzer nimmt. Also: Paket gebaut (das Bauskript `paket_bauen.sh` liegt beim Betreuer außerhalb des Pakets, weil es den äußeren Baum zusammensetzt — → `Dive-on-Wide-0.1037.zip`, 3,1 MB, 391 Dateien), in einen leeren Ordner ausgepackt, geprüft, dass nichts Privates mitgereist ist (`storage/` leer, kein `.env`, keine Datenbank, kein `.git`), und auf Port 3099 gestartet — ohne Vorwissen bedient.

**Was von selbst stimmte.** Der Start nennt Adresse, Speicherort und einen erzeugten Zugangsschlüssel samt Erklärung. `/api/einrichtung` meldet `noetig: true`, und alle vier riskanten Schalter stehen aus. Nach dem Abschluss der Einrichtung steht in der Datenbank genau das: `SANDBOX_ENABLED=0`, `BRAIN_AUTOSYNC=0`, `COMPUTER_USE_ENABLED=0`, `ALLOW_LOCAL_FETCH=0`. Chat, Modellwahl und alle Ansichten liefen auf dem frischen Stand ohne Nacharbeit.

**Vier Befunde, alle behoben:**

**8. Die Einrichtung schlug ein Modell vor, das nicht in den Rechner passt.** Die Auswahlliste nennt Modelle mit Anbieter-Vorsatz (`ollama@@qwen2.5-coder:14b`), der gespeicherte Standard steht ohne (`qwen2.5-coder:14b`). Der Vergleich schlug also **nie** an, und der Browser zeigte den ersten Listeneintrag — hier ein 27-B-Modell mit 17 GiB Bedarf bei 24 GiB Speicher. Wer „Fertig" drückte, überschrieb damit seinen Standard, und die allererste Frage wäre mit einem Speicherfehler beantwortet worden: **genau der HTTP 500, dessen Übersetzung in Befund 1 steckt** — eine Ebene früher, wo er noch vermeidbar ist.

Behoben an der Wurzel: Der Server gleicht Vorsatz-Formen ab, liefert zu jedem Modell seine Größe mit und schlägt aktiv eines vor — das größte, das noch die Hälfte des Arbeitsspeichers frei lässt (Faustregel Dateigröße × 1,2). Passt keines, wird das kleinste genannt, und ohne bekannte Größen wird nichts behauptet. Die Liste zeigt jetzt „qwen2.5-coder:14b — 8.4 GB · empfohlen" und darunter den Grund: „Dieser Rechner hat 24 GB."

**9. Dive on Wide stellte sich als Produkt von OpenAI vor.** Die erste Frage eines neuen Nutzers — „Was bist du?" — wurde beantwortet mit: „ein lokaler KI-Arbeitsplatz, entwickelt von OpenAI." Der Systemprompt sagte, *was* Dive on Wide ist, aber nicht, *woher* es kommt, und ein Basismodell lässt eine Leerstelle nicht stehen: Es füllt sie. Behoben durch einen Herkunftssatz, der auch das Nichtwissen erlaubt. Die Antwort lautet jetzt: „Ich wurde nicht von OpenAI, Anthropic oder Google entwickelt, aber genaue Informationen über meinen Hersteller sind nicht öffentlich bekannt." Der Satz steht zweimal — in `server.py` und im Skript, weil der Chat ohne Agenten seinen Systemprompt im Browser baut; ein Test hält beide Fassungen wortgleich.

**10. Die Seitenleiste schob in kleinen Fenstern die ganze Seite weg.** Menü und Verlauf waren starre Geschwister; das Menü allein misst 867 px. In einem 868-px-Fenster — ein Laptopdeckel, ein nicht maximiertes Fenster — wuchs die Seite auf 1156 px, verrutschte um 288 px und nahm Chat, Einstellungen und Umschalter mit aus dem Bild, obwohl `overflow:hidden` galt. Ein Flex-Element schrumpft nie unter seinen Inhalt, solange ihm `min-height:0` fehlt. Behoben: Menü und Verlauf teilen sich einen Scrollbereich, die Fußzeile bleibt außerhalb. Nachgemessen bei 868 px und bei 900×600: Seitenhöhe gleich Fensterhöhe, Fußzeile sichtbar.

**11. Ein Krypto-Hinweis stand vor der Entscheidung, zu der er gehört.** „Nicht konstantzeitig — gegen einen Angreifer auf DIESEM Rechner nicht geeignet" erschien im Einleitungssatz des Netzwerk-Abschnitts, also auch dann, wenn der Neuling das Netzwerk gar nicht einschaltet. Verschoben unter die Auswahl, mit Bedingung davor: „Falls du Klause oder Weite wählst: … Für den Anfang ohne Netzwerk spielt das keine Rolle."

Befunde 8 bis 11 sind je durch einen Test festgehalten (`einrichtung`, `frontend`). Alle 437 Tests bestanden.

---

## Netzwerk-Compute: trägt das Netz eine echte Antwort?

`werkzeuge/mesh_abnahme.py` belegte den Weg durchs Netz — aber mit einem Echo als „Modell". Damit blieb die eine Frage offen, um die es beim Netzwerk-Compute geht. `werkzeuge/mesh_inferenz.py` beantwortet sie so, dass der Aufbau nicht schummeln kann:

* Knoten **B** meldet ein echtes Ollama-Modell und rechnet auch damit.
* Knoten **A** meldet **keine** Modelle. Was A zurückbekommt, kann A nicht selbst erzeugt haben.
* Gefragt wird nach `17 × 23` — eine Antwort, die kein Echo erzeugen kann.

```
BESTANDEN — echte Inferenz ueber das Mesh
  · Nachbarschaft nach 1 s
  · A findet 'qwen2.5-coder:14b' im Netzkatalog (1 Geraet(e)) — so waehlt es auch ein Mensch aus
  · Auftrag verteilt (8a888438eb05)
  · Antwort nach 1 s von 47a43d286072: '391'
  · Inhalt geprueft: 391 steht in der Antwort — ein Echo haette das nicht gekonnt
```

Der Katalogschritt steht bewusst mit drin: Ein Modell, das rechnet, aber im Katalog fehlt, ist in der Oberfläche unauffindbar — es gäbe es für den Nutzer nicht.

Der erste Lauf war lehrreich und ging mit Absicht anders aus. Mit dem Standardmodell `qwen2.5:0.5b` kam zurück: „Ich werde die Zahl 17 23 ausgeben." Das Werkzeug meldete **„Modellfehler, kein Netzfehler — der Weg durchs Netz hat getragen"** und trennt beides auch im Rückgabewert (2 statt 1). Ein Prüfwerkzeug, das ein schwaches Modell als Netzausfall meldet, schickt den nächsten Menschen in die falsche Richtung.

**Was damit belegt ist:** Entdeckung, Katalog, Auftragsverteilung, echte Inferenz, Rückweg — auf einer Maschine, über echtes UDP-Multicast. **Was nicht:** zwei physische Geräte und damit echte Netzlatenz.

### Verteilte Inferenz: ein Modell über mehrere Rechenknoten

Ein Auftrag, der zu einem anderen Knoten wandert und dort **ganz** gerechnet wird, ist noch keine verteilte Inferenz. Dabei wird ein Modell in Schichten zerlegt, und jeder Knoten hält nur einen Teil — erst das lässt ein Modell laufen, das auf keinem Gerät allein Platz hätte. Zwei Geräte braucht es dafür nicht, um den **Weg** zu prüfen: Zwei Rechenknoten auf `127.0.0.1` sind für llama.cpp zwei Rückseiten wie alle anderen. `werkzeuge/mesh_verteilt_probe.py` tut genau das.

```
Modell qwen2.5:0.5b: 0.37 GB, 24 Schichten
Plan: hier: 5 Schichten (fuehrt) · rpc50252: 9 Schichten · rpc50253: 10 Schichten
Antwort nach 0.2 s: 'EINEM Wort. Frankreich hat die Hauptstadt Paris …'
Rechenknoten 50252: 21 Kernel uebersetzt
Rechenknoten 50253: 22 Kernel uebersetzt
BESTANDEN — ein Modell, ueber 3 Knoten gespannt, hat geantwortet
```

Der Beleg steht bewusst in den Protokollen der **Rechenknoten**, nicht in dem des Hauptprozesses: `llama-server` schreibt die Rückseiten-Adressen nirgends hin, aber ein Knoten ohne zugewiesene Schichten übersetzt auch keine Metal-Kernel für dieses Modell. Die erste Fassung der Probe suchte im falschen Protokoll und meldete einen Fehlschlag, wo keiner war.

**Zwei eigene Irrtümer, beide lehrreich genug zum Aufschreiben.** Erstens: Im Protokoll des Hauptprozesses stand `Remote RPC server crashed or returned malformed response` — eine Meldung, die nach einem schweren Fehler aussieht. Die Zeitstempel zeigten: Sie kommt **nach** `cleaning up before exit`, also nachdem die Probe selbst die Prozesse beendet hatte. Die Reihenfolge war falsch herum; jetzt endet erst der Hauptprozess, dann die Rechenknoten, und die Meldung bleibt aus. Zweitens: Die Prüfschleife hieß ihre Variable `text` — genau wie die Modellantwort. Sie überschrieb sie, und die Probe verglich ein Protokoll mit dem erwarteten Wort.

**Was nicht geprüft ist:** echte Netzlatenz zwischen Geräten, und ein Modell, das groß genug ist, dass es auf einem Gerät allein nicht liefe.

### Wenn ein Knoten mitten im Auftrag verschwindet

Der Fall, der beim Online-Gang zuerst eintritt — ein Laptop klappt zu, ein WLAN bricht weg. `werkzeuge/mesh_ausfall.py` stellt ihn mit echten Knoten über echtes UDP nach.

**Die Mehrfachvergabe trägt.** B und C können beide rechnen, B stirbt direkt nach der Vergabe: Antwort von C nach 2 s. Genau dafür geht derselbe Auftrag an mehrere Knoten.

**15. Bei Einzelvergabe stand der Auftrag für immer auf „offen“.** Stirbt der einzige fähige Knoten, kann keine Antwort mehr kommen — aber nichts an der Lage verriet das. `offen: 1`, `wartet_s` wächst, sonst nichts. Wer darauf wartete, wartete unbegrenzt. Behoben: Der Auftrag merkt sich jetzt, **wen** er gefragt hat, nicht nur wie viele; `auftrag_lage` zählt die Verstummten und sagt `vergeblich`, wenn von den noch Offenen nichts mehr kommt. Gemessen: **nach 29 s erkannt** bei einer Verfallsfrist von 30 s — schneller geht es nicht, denn vorher ist ein stiller Knoten von einem langsamen nicht zu unterscheiden.

**16. Ein beendeter Knoten warf aus dem Rechen-Thread.** Wird ein Knoten beendet, während er einen fremden Auftrag rechnet, ist seine Sitzungsidentität vernichtet und das Absenden der Antwort scheitert. Der Fehlerweg rief dieselbe Stelle noch einmal auf — die zweite Ausnahme fing niemand, und der Thread starb mit einem Rückverfolgungsprotokoll auf der Fehlerausgabe. Ein geordnetes Ende darf nicht wie ein Absturz aussehen, sonst sucht der nächste Mensch einen Fehler, den es nicht gibt, und übersieht die echten in derselben Ausgabe. Beide Wege sind jetzt abgesichert; der Test fällt ohne den Fix durch (nachgeprüft).

## Skills in einem echten Lauf

Bisher waren Skills nur gefunden, nie benutzt. Geprüft mit einer Aufgabe, deren Ergebnis widerlegbar ist: **Fakten-Check** auf einen Text mit einer absichtlich erfundenen Bibliothek (`pandas-turbowindow 3.2`). Ergebnis: **🔴 vermutlich erfunden** — die Falle sitzt. Der Lauf lief im Hintergrund mit Lauf-Nummer, legte ein Artefakt an und meldete sich in der Inbox.

Das Modell stufte zusätzlich NumPy 2.0 als erfunden ein und nannte 2024 „ein zukünftiges Jahr" — sein Wissensstand ist älter, und der Skill sagt am Ende selbst, was per Web zu prüfen sei. Das ist eine Modellgrenze, kein Fehler von Dive on Wide.

**12. Ein Artefakt war unter seiner eigenen Adresse nicht abrufbar.** Der Lauf liefert eine `artifact_id`, die Inbox schreibt „als Artefakt gespeichert" — und `GET /api/artifacts/<id>` antwortete **404**, obwohl das Artefakt in der Liste stand. Es gab nur `/download` und `/content`. Für ein fremdes Gerüst, das Dive on Wide über die Schnittstelle steuert, war das eine Sackgasse genau an der Stelle, an der es das Ergebnis abholen will. Behoben: Die Adresse liefert jetzt Kopfdaten, Inhalt und den Weg zum Download in einer Antwort; eine unbekannte Kennung antwortet weiterhin mit 404 und einem deutschen Grund.

---

## Die übernommenen Merkmale, mit einer echten Aufgabe geprüft

Checkpunkte, Abzweig, ACP, MCP und Webhooks waren schon belegt. Offen waren Slash-Befehle, Regeln und Hooks, Wiederholen, Verlauf und Gedächtnis. Geprüft in einem eigens gebauten Projekt, das alle gleichzeitig fordert: eine Verbotsregel (`lesen(.env)` bei einer echten `.env` mit einem Geheimnis), ein Hook nach jeder Änderung, ein eigener `/zeilen`-Befehl — und die Aufgabe, eine Funktion zu ergänzen.

**Was auf Anhieb stimmte.** Der Befehl wurde gefunden (`/zeilen <datei> · Projekt (.dowos/befehle)`). Der Lauf las, änderte, prüfte seine eigene Änderung mit `python3 -c … → exit=0 10` und legte einen Checkpunkt mit klarer Rückgängig-Anweisung an. Der Verlauf zeigt jeden Schritt mit Zeit, Kontextgröße und Checkpunkt. Mit Vertrauen feuerte der Hook (`Hook nach_aenderung: sh pruefen.sh zaehler.py`, Spur in der Datei).

**13. Ohne Vertrauen fiel auch der Schutz weg.** Die Vertrauenskette verlangt, dass ein Projekt seine `.dowos/einstellungen.json` bestätigt bekommt, bevor sie gilt — richtig, denn `erlauben` nimmt Rückfragen weg und `hooks` führen Befehle aus. Aber es fiel **alles** zusammen weg, auch `verbieten`. Im nicht interaktiven Lauf gibt es die Vertrauensfrage gar nicht; wer `verbieten: ["lesen(.env)"]` schrieb, stand also ungeschützt da und glaubte das Gegenteil. Im Versuch las der Agent die `.env` und das Geheimnis stand danach im Schrittprotokoll.

Behoben nach der Asymmetrie, die tatsächlich besteht: Ein Verbot kann den Agenten nur **einschränken** — ein bösartiges Projekt erreicht damit höchstens, dass er weniger tut. Verbote gelten deshalb auch ohne Vertrauen, Freigaben und Hooks nicht. Der Hinweis sagt jetzt beides: „nicht vertraut — die Verbote gelten trotzdem (lesen(.env)), Freigaben und Hooks bleiben aus." Im Wiederholungslauf wurde Schritt 1 geblockt, der Agent fand selbst einen anderen Weg, und das Geheimnis kam nicht mehr vor.

**14. Die Wiederholung lief ohne Regeln — und konnte deshalb nicht finden, wofür sie da ist.** `wiederholung.py` sagt über sich, eine Abweichung verrate „ein Werkzeug, **eine Regel**, die Sandbox". Tatsächlich rief sie `werkbank.arbeiten` ohne `regeln` auf. Gegenprobe an einem echten Lauf: Eine neu eingeführte Verbotsregel für genau die gelesene Datei — Meldung trotzdem „gleich ✅". Ein Prüfwerkzeug, das nur bestehen kann, prüft nichts.

Behoben: Die Wiederholung bekommt die **heutigen** Regeln, angewendet auf den **alten** Stand — genau die Frage, die sie stellt. Dieselbe Gegenprobe liefert jetzt:

```
Wiederholung von cli5c5c7ac5c5: 5 Schritte — 2 Abweichung(en) ⚠️
  Schritt 1 (lesen):
    damals: 1| def zaehle_woerter(text): …
    jetzt:  Fehler: Diese Aktion ist durch die Regel lesen(zaehler.py) verboten.
```

Rückgabe 0 bei „gleich", 1 bei Abweichungen — wie dokumentiert. (Beim ersten Messen las ich `$?` hinter einer Pipe und damit den Status von `tail`; die Rückgabe war die ganze Zeit richtig.)

**Nicht behoben, sondern verstanden:** `--freigabe alles` ist die **strengste** Stufe („vor jeder Änderung fragen"), `nie` heißt „nicht nachfragen". Im nicht interaktiven Terminal lehnt Dive on Wide freigabepflichtige Aktionen dann ab, statt stillschweigend durchzuwinken — so dokumentiert und so richtig. Ich hatte die Namen verwechselt und kurz einen Fehler gesehen, wo Vorsicht war.

---

## Von einem fremden Agenten gesteuert — der ganze Weg

Der zweite Betriebsfall von Dive on Wide, durchgespielt auf der frischen Installation, mit echten Aufrufen von außen.

**Die Voreinstellung wehrt ab.** `GET /api/ausgang` auf dem frischen Stand: `stufe: "aus"`, keine Schlüssel, „Heute ist nichts nach draußen gegangen." Ein Auftrag mit gültigem Harness-Schlüssel wird mit **403** abgewiesen — und die Meldung sagt, wo der Besitzer es einschaltet, statt nur nein zu sagen.

**Der Schlüssel ist eingesperrt.** Derselbe Harness-Schlüssel an anderen Türen:

| Aufruf | Antwort |
|---|---|
| `/api/sessions` | **403** |
| `/api/settings` | **403** |
| `/api/extern/info` | 200 |

**Zweite Schicht dahinter.** Mit offenem Ausgang, aber ausgeschalteter Sandbox antwortet Dive on Wide: „Code-Ausführung ist abgeschaltet — Einstellungen → Code-Sandbox." Ein fremdes Gerüst kann den Ausgang nicht dazu benutzen, den Sandbox-Schalter zu umgehen. Zwei Entscheidungen des Besitzers, beide nötig.

**Der Auftrag läuft wirklich.** Nach beiden Freigaben: `{"id": "a9877a35ab11", "zustand": "laeuft", "weiter": "GET /api/extern/auftrag/a9877a35ab11"}` — die Antwort sagt dem fremden Agenten selbst, wie es weitergeht. Nach 28 s fertig, und die Datei liegt tatsächlich da, mit dem verlangten Inhalt: `storage/workspaces/extern/hallo.txt` → `Hallo Welt`. **In einem eigenen Arbeitsordner** — das fremde Gerüst kommt nicht an die Projekte des Nutzers.

**Die Abstufung ist echt, nicht kosmetisch.** Derselbe Auftrag, drei Stufen:

| Stufe | Felder | Was zusätzlich sichtbar wird |
|---|---|---|
| `urteil` | 9 | nur Maschinenfakten: 5 Schritte, 28,3 s, Werkzeugzählung, `dateien: {neu: 1}` — **kein** Freitext, **kein** Dateiname |
| `zusammenfassung` | 13 | `dateinamen`, `schritte_liste`, und der Satz des Agenten |
| `alles` | 14 | zusätzlich `verlauf` |

**Jede Antwort steht im Buch.** Sechs Einträge für diesen einen Auftrag, jeder mit Zeit, Empfänger, Pfad, Stufe und Zeichenzahl: „Heute 6 Antworten, 881 Zeichen nach draußen — Fremdes Gerüst (Abnahme) (6)." Wer wissen will, was sein Rechner herausgegeben hat, kann es nachlesen statt es zu glauben.

## Gedächtnis und Profile

`gedaechtnis` las **10 frühere Läufe** ein und fand zu „zaehle_zeichen" drei davon per Volltextsuche. Projektnotizen und globale Notizen landen im Systemprompt — darunter der Verweis, der frische Agenten auf `docs/LEHREN.md` schickt, bevor sie etwas neu erfinden. Vier Agentenprofile (`erkunder`, `minimal`, `reviewer`, `tester`) werden mit ihren Rechten und Werkzeugen aufgelistet; `anwenden` macht ein Profil nie lockerer als die Einstellung.

---

## Was ein Fremder im selben Netz sieht

Dive on Wide lauscht voreingestellt auf **allen** Schnittstellen (`HOST=0.0.0.0`) — damit ein Handy im WLAN darauf kann. Also gehört geprüft, was ohne Schlüssel von dort aus erreichbar ist. Gefragt wurde nicht über `localhost`, sondern über die echte LAN-Adresse.

**Was hält.** Der Schutz hängt an der echten TCP-Gegenstelle (`client_address`), nicht an einem Kopfzeilen-Feld wie `X-Forwarded-For` — also nicht fälschbar. Ohne Schlüssel gibt es 401 auf alles außer der Hülle (`/`, `index.html`, Manifest, `sw.js`, Symbol) und dem Gesundheits-Check. Cross-Origin wird abgewiesen.

**17. Der offene Gesundheits-Check verriet die ganze Lage.** `/api/health` ist mit Absicht ohne Schlüssel erreichbar — ein Container, eine Überwachung, ein Anlaufskript muss fragen können, ob der Dienst lebt. Er antwortete aber mit:

```json
{"browser_use": true, "browser_ready": true, "sandbox_enabled": true,
 "ollama": "http://localhost:11434", "models": 12, "provider_ids": ["ollama"]}
```

Jeder im selben WLAN konnte damit erfahren, dass hier ein Arbeitsplatz **mit eingeschalteter Codeausführung** läuft. Das ist Aufklärung, kein Gesundheitscheck. Jetzt kommt ohne Schlüssel `{"ok": true, "eingeschraenkt": true}` — die kürzestmögliche wahre Antwort — und mit Schlüssel oder vom eigenen Rechner weiterhin alles.

**Und der Fehler, den ich dabei selbst einbaute.** Die Statuszeile liest `h.models`. Ohne Kennzeichnung hält sie die kurze Antwort für eine vollständige und schreibt „Ollama verbunden (undefined Modelle)“. Deshalb trägt die kurze Antwort das Merkmal `eingeschraenkt`, und die Oberfläche sagt stattdessen „Zugangsschlüssel nötig“. Über die LAN-Adresse nachgesehen: genau das steht da, samt Anmeldedialog.

### Dateizugriffe, wirklich durchprobiert

Der gefährlichste Teil einer Anwendung, die online geht. Nicht am Code abgelesen, sondern mit echten Anfragen probiert — auch mit kodierten Trennern, die eine Regex gern übersieht.

| Versuch | Antwort |
|---|---|
| `/api/sandbox/file/..%2f..%2f..%2fserver.py` | 404 |
| `/api/sandbox/file/....%2f....%2fserver.py` | 404 |
| `/api/sandbox/file/%2e%2e%2f%2e%2e%2fserver.py` | 404 |
| `/api/sandbox/file/..;/server.py` | 404 |
| `/api/artifacts/..%2f..%2fserver.py/download` | 404 |

Und beim Anlegen von Artefakten, wo der Name aus der Anfrage in einen Pfad wandert:

| Angegebener Name | Wo die Datei landete |
|---|---|
| `../../../../Users/du/…/geheim.txt` | `storage/artifacts/geheim.txt` |
| `..%2f..%2fserver.py` | `storage/artifacts/2f..-2fserver.py` |
| `....//....//server.py` | `storage/artifacts/server.py` |
| `/etc/passwd` | `storage/artifacts/passwd` |
| `a/../../b.txt` | `storage/artifacts/b.txt` |

Alle fünf auf den reinen Dateinamen zurückgeschnitten, alle **innerhalb** des Artefaktordners. Zur Gegenprobe lag eine Datei mit erkennbarem Inhalt im Heimatordner: unverändert. Die echte `server.py` ebenfalls.

Dazu ein Befund, der keiner ist, aber beruhigt: Es gibt **keinen allgemeinen Dateiserver**. `send_file` hat genau drei Aufrufer — zweimal ein fester Pfad (`frontend/index.html`, `assets/favicon.svg`), einmal der Artefaktordner. Die ganze Oberfläche ist eine einzige HTML-Datei. Das ist der Grund, warum die Angriffsfläche so klein ist.

## Dauerbetrieb unter echter Last — vier Stunden

Der Leerlauftest unten sagt nur etwas über Leseabfragen. Ein Leck entsteht dort, wo gearbeitet wird. `werkzeuge/dauerlast.py` fährt deshalb im Kreis kleine Arbeitsschichten: eine Chatfrage über den Streaming-Weg, ein Skill-Lauf bis zum Artefakt, ein Coding-Agent mit eingeschalteter Sandbox, ein Artefakt anlegen–lesen–löschen. Eigener Wegwerf-Speicher; der echte Ordner wird nicht angefasst.

```
Dauer 4.00 h · 690 Runden · 0 Fehler
RSS              66.2 MB ->     35.3 MB  (-30.8, -47 %)   Hoechstwert 67.1
Dateizeiger      42.0    ->     42.0     (+0.0,  +0 %)    Hoechstwert 43
Threads           3.0    ->      3.0     (+0.0,  +0 %)    Hoechstwert 4
Datenbank         0.2 MB ->      2.4 MB  (+2.2)           Hoechstwert 2.36
Agent-Dateien      2.0   ->    689.0     (+687)           Hoechstwert 690
```

**Dateizeiger und Threads sind konstant.** Das sind die beiden Zahlen, an denen ein Leck sichtbar würde: Sie gehören zu einem einzelnen Lauf und müssen danach wieder verschwinden. Über 690 Läufe blieben sie bei 42 und 3, mit je einem kurzen Ausschlag auf 43 und 4 — eine Anfrage, die im Moment der Messung noch lief.

**Der Arbeitsspeicher fiel um 47 %, und das ist kein Erfolg, sondern eine Erklärung wert.** Der Verlauf: 66,1 MB zu Beginn, Anstieg auf 67,1 bis Runde 81, dann in **einem einzigen Schritt** auf 31,2 MB, danach dreieinhalb Stunden ein Band zwischen 26 und 36 MB. So verhält sich kein Leck — ein Leck wächst monoton. So verhält sich der Arbeitsspeicher eines Prozesses, dessen kalte Seiten das Betriebssystem einzieht. Nachgemessen, statt vermutet: Die Maschine stand unter schwerem Druck — 4,27 von 5,12 GB Auslagerung in Gebrauch, 3,1 GB komprimiert, 12,8 Millionen Dekompressionen. Zum Vergleich: Im Leerlauftest ohne Modellarbeit blieb der RSS 50 Minuten lang bei 66 MB.

**Und die Zahl, die das erst wertvoll macht:** Die Rundendauer war im ersten Fünftel **20,7 s** und im letzten **20,6 s**. Dive on Wide arbeitete nach vier Stunden genauso schnell wie zu Beginn — während die Maschine 4 GB auslagerte.

**Es wurde wirklich gearbeitet, nicht nur nicht gescheitert.** Das Werkzeug prüft das ausdrücklich: Bleibt die Zahl der Dateien im Arbeitsordner stehen, gilt die Runde als stiller Ausfall, auch wenn sie „done“ meldet. 690 Runden, 690 Dateien. Und sie sind verschieden — Stichprobe aus Runde 97, 102 und 413:

```python
assert verdopple(5) == 10, 'Test fehlgeschlagen: verdopple(5) sollte 10 sein'
assert verdopple(3) == 6,  "Test fehlgeschlagen: verdopple(3) sollte 6 sein"
assert verdopple(2) == 4   # dazu: assert verdopple(0) == 0
```

**Drei eigene Fehler beim Bau des Werkzeugs**, jeder hätte den Lauf wertlos gemacht. Erst zählte es nur Bytes des Datenstroms — ein Fehlerpaket ist auch ein paar hundert Bytes lang, ein stiller Ausfall hätte vier Stunden wie Arbeit ausgesehen. Dann las es `content` auf der obersten Ebene, wo keiner steht (das Format ist `{"message": {"content": …}}`), und hielt jede Antwort für leer. Zuletzt zählte eine Mindestlänge von fünf Zeichen die richtige Antwort `47` auf „Nenne eine Primzahl“ als Ausfall.

**Und eine Verwechslung, die fast als Absturz durchgegangen wäre.** Ein abgebrochener Vorlauf schrieb seinen Abschlussbericht in dieselbe Protokolldatei, die der Nachfolger schon geleert hatte — samt „Server gestorben in Runde 4“. Der Server war von mir beendet worden, und der echte Lauf stand zu dem Zeitpunkt unbeirrt bei Runde 45 mit null Fehlern. Der Bericht geht seitdem zusätzlich in den eigenen Wegwerf-Ordner des Laufs.

**Was das nicht sagt:** vier Stunden, nicht vier Tage. Und alle Arbeit kam von einem einzigen Aufrufer — nichts über viele gleichzeitige Nutzer.

### Zwölf Stunden, Stand 30.09.2026 (nach den Windows-Korrekturen)

`werkzeuge/dauerlast.py --stunden 12`: **2054 Runden, 0 Fehler.** Arbeitsspeicher 68 → 34 MB (Höchstwert 69), Dateizeiger 42 → 42, Threads 3 → 3. Die Inbox bleibt bei gut 5000 Einträgen stehen — die Aufbewahrungsregel greift. Datenbank 0,2 → 6,8 MB: gesammelte Arbeit, kein Leck.

## Dauerbetrieb im Leerlauf

Die Abnahme sagte bisher nichts über längeren Betrieb. Ein eigener Server, 50 Minuten, alle 20 Sekunden sieben Abfragen quer durch die Schnittstelle, dazu Messung von Arbeitsspeicher, offenen Dateizeigern und Threads — den drei Dingen, an denen ein langlebiger Serverprozess tatsächlich stirbt.

```
Dauer 50 min · 1050 Anfragen · 0 Fehler
RSS  66.0 MB -> 66.2 MB  (+0.3 MB, +0 %)
Dateizeiger  42 -> 42  (Hoechstwert 42)
Threads      4 -> 3  (Hoechstwert 4)
```

Kein Wachstum bei Speicher, Dateizeigern oder Threads. **Was das nicht sagt:** Gemessen wurde der Leerlauf mit Leseabfragen — keine Agentenläufe, keine Modellaufrufe, keine Artefakte über Stunden. Ein Leck, das erst unter echter Last entsteht, wäre hier unsichtbar geblieben.

---

## Code-Sandbox: eine kleine Anwendung, von Dive on Wide gebaut

Auf einer Wegwerf-Instanz (eigener Speicher, damit die echten Arbeitsordner unberührt bleiben), Sandbox ausdrücklich eingeschaltet. Aufgabe: eine Kassen-Anwendung mit Artikeln, Summe und 19 % Mehrwertsteuer, dazu `tests.py` mit mindestens drei Fällen, darunter die leere Kasse — und die Tests sollen bestehen.

Der Agent brauchte zwei Durchgänge: „Iteration 1/5 — Fehler, korrigiere …", dann „Iteration 2/5 — läuft fehlerfrei". Insgesamt rund 60 Sekunden.

**Nachgeprüft, nicht geglaubt.** Die Tests selbst ausgeführt, außerhalb von Dive on Wide:

```
test_einzelner_artikel ... ok
test_leere_kasse ... ok
test_mehrere_artikel ... ok
Ran 3 tests in 0.000s — OK
```

Drei Fälle, die leere Kasse dabei, Mehrwertsteuer korrekt (`assertAlmostEqual(0.285)` bei 1,50 €).

**Und was nicht geliefert wurde.** Die Aufgabe verlangte eine *Kommandozeilen*-Anwendung; `kasse.py` hat 13 Zeilen und keinen Einstiegspunkt — kein `__main__`, kein Argumentweg. Die Logik stimmt, die Anwendung fehlt. Das ist eine Grenze des Modells (qwen2.5-coder:14b, fünf Durchgänge), nicht des Gerüsts: Dive on Wide hat gemeldet, was wahr ist — „läuft fehlerfrei" — und nicht „Aufgabe erfüllt". Das Erfolgskriterium des Coding-Agenten ist, dass der Code läuft und die Tests bestehen; ob die Aufgabe damit erfüllt ist, entscheidet weiterhin ein Mensch. Gut so, solange es niemand anders behauptet.

---

## Tastaturbedienung

Bisher stand hier, Tastenkürzel seien nicht prüfbar, weil die Browser-Ansicht keine echten Sondertasten sendet (sie schickt `key: ""`, `keyCode: 0`). Das stimmt für den Weg **vom Betriebssystem in den Browser** — und der gehört nicht Dive on Wide. Was Dive on Wide gehört, ist die Behandlung der Ereignisse, und die lässt sich mit korrekt gebauten `KeyboardEvent`s vollständig prüfen.

Zuerst die Bestandsaufnahme, die selbst ein Ergebnis ist: Es gibt **keine globalen Tastenkürzel**. Kein `Cmd`/`Ctrl`-Griff, kein `keydown` auf dem Dokument. Die Tastaturbedienung besteht aus dem Eingabefeld und der Vervollständigung.

| Taste | Erwartet | Gemessen |
|---|---|---|
| `Enter` | sendet, kein Zeilenumbruch | `defaultPrevented: true`, `send()` einmal gerufen |
| `Shift+Enter` | Zeilenumbruch, sendet nicht | `defaultPrevented: false`, nicht gesendet |
| `/` | Liste öffnet | sichtbar, **8** Einträge |
| `↓` / `↑` | Auswahl wandert | 0→1→2→1 |
| `↑` bei Eintrag 0 | läuft um | → 7 (letzter Eintrag) |
| `Escape` | Liste schließt | `display: none` |
| `Tab` | übernimmt | `/` → `/research `, Liste zu |

Und die Endprobe mit echtem Durchstich: Text ins Feld, `Enter`, Feld leer, Modell antwortete `Tastaturprobe`. Die Kette trägt.

Ein Test hält den Vertrag fest — besonders die Prüfung auf `shiftKey`. Fällt sie weg, sendet Dive on Wide bei jedem Zeilenumbruch; das merkt man erst beim Tippen, und kein Testlauf würde rot.

**Was offen bleibt:** ob eine physische Tastatur diese Ereignisse auch so erzeugt. Das ist Sache des Browsers, nicht von Dive on Wide — aber geprüft ist es hier nicht.

---

## Was diese Abnahme **nicht** behauptet

- Sie sagt nichts über Windows und Linux. Der Kern enthält nichts Plattformgebundenes, aber „guter Grund" ist nicht „geprüft".
- Über Dauerbetrieb sagt sie, was vier Stunden echte Arbeit und 50 Minuten Leerlauf zeigen (siehe oben) — nichts über Tage, und nichts über viele gleichzeitige Nutzer.
- Sie sagt nichts über fremde Nutzer. Alles hier lief unter einem Besitzer auf einer Maschine.
- Sie sagt nichts über das Tippen mit einer echten Tastatur — der Weg vom Betriebssystem in den Browser ist nicht geprüft. Die Behandlung der Tasten schon, siehe unten.
- Der Frischklon lief auf **demselben Rechner** — mit demselben Ollama und denselben zwölf Modellen. Geprüft ist damit ein leerer Ordner, nicht eine fremde Maschine. Der Fall „Ollama gar nicht installiert“ ist in der Einrichtung vorgesehen und im Text beantwortet, aber nicht erlebt.

## Nachttest 26./27.09.2026 — Fuzz, Wackeltests, sechs Stunden Last

Alles in Wegwerf-Instanzen, nichts am echten Speicher.

- **Fuzz** (`werkzeuge/fuzz.py`, neu): 308 763 kaputte, übergroße und feindliche Anfragen an eine Freigabeliste harmloser Schnittstellen, dazu handgemachte HTTP-Anfragen und ein Kanarienvogel-Ordner gegen Pfadausbrüche. Server am Ende lebend, **kein Pfadausbruch, kein Hänger**. Gefunden und behoben:
  - Ein Rumpf, der kein JSON-Objekt ist (Liste, `null`, Zahl), ließ 30 Schnittstellen mit 500 scheitern → zentral in `read_body` 400.
  - Felder mit falschem Typ (Liste statt Text) liefen bis in SQLite oder `write()` → 500 in 15 Schnittstellen → `feld_text()`, 400.
  - Eine Anfrage, die weniger schickt, als sie ankündigt, hielt ihren Thread **unbegrenzt** → Frist 60 s, dann 400.
  - Jede `ValueError` hieß 413 („zu groß“) → nur noch, was wirklich zu groß ist; sonst 400.
  - Überlange Workspace- und Dateinamen → 500 → 400.
  - Ein Aufrufer, der vor der Antwort auflegt, schrieb einen vollen Traceback ins Protokoll (692 im Probelauf) → eine Zeile.
  - Nach den Korrekturen: 43 571 Anfragen, **0 Befunde**.
- **Wackeltests:** Testsuite dreimal, Volldurchlauf über zwei Instanzen zweimal — alles grün.
- **Dauerlast** mit echtem Modell: 6,1 h, 1 053 Runden (Chat, Skill, Coding-Agent, Artefakt), **0 Fehler**. Arbeitsspeicher 66 → 30 MB, Dateizeiger 42 → 42, Threads 3 → 3. Datenbank und Inbox wachsen mit der Arbeit (3 Einträge je Runde) — kein Leck. Die Inbox räumt jetzt selbst auf: Gelesenes nach 30 Tagen, höchstens 5 000 Einträge, Ungelesenes bleibt immer.

## Erster Lauf auf echtem Linux (27.09.2026)

Ubuntu 26.04 LTS in einer Lima-VM (ARM, 4 Kerne, 4 GB, **ohne jede Freigabe von Mac-Ordnern**), eingerichtet wie von jemandem, der Dive on Wide zum ersten Mal sieht: Paket entpacken, README lesen, Installer im Prüfmodus, starten, Einrichtung im Browser. Ollama läuft auf dem Mac und wird aus der VM über `http://host.lima.internal:11434` benutzt.

**Was hielt:** Chat über das Ollama des Macs (16 s), der Außentest-Bericht, die **Linux-Sandbox mit bubblewrap** (Schreiben im Projekt erlaubt, außerhalb gesperrt, Netz gesperrt — erstmals auf echtem Linux belegt, auch unter Ubuntus AppArmor-Sperre), die Computer-Use-Diagnose auf einem virtuellen X11-Schirm (Xvfb: Foto über `maim`, Steuerung über `xdotool`, Steuerung ab Werk aus). **Testsuite auf Linux: 465 von 470**, die fünf Abweichungen unten.

**Gefunden und behoben:**

| Stolperstein | Wirkung | behoben |
|---|---|---|
| Frisches Ubuntu hat kein `unzip`; Python und manche Entpacker verlieren die Ausführrechte | `./dowos-starten`: „Permission denied“ | Linux-Paket zusätzlich als `.tar.gz` (ohne macOS-Dateiattribute, die GNU tar mit Hunderten Warnungen quittierte) |
| Einrichtung hielt ein fehlendes Ollama für „erreichbar, ohne Modell“ | riet zu `ollama pull` auf einem Rechner ohne Ollama | wird ausdrücklich geprüft; Hinweis nennt Ollama auf anderem Rechner |
| Neue Ollama-Adresse wurde nicht geprüft | Modellfeld blieb leeres Textfeld | Adresse wird beim Eintippen geprüft, Modelle erscheinen |
| Ollama auf anderem Rechner wurde am Speicher der VM gemessen | jedes Modell „zu groß“, „empfohlen“ war nur der Wert aus `.env.example` | entfernt → keine Speicherbewertung; vor der Einrichtung zählt `.env.example` nicht als Wahl |
| Installer kannte die Linux-Sandbox nicht | Werkbank ohne Sandbox, niemand sagte es | eigener Baustein, prüft auch, ob bubblewrap unter AppArmor wirklich läuft |
| Installer: nur das erste fehlende Paket, kein `apt-get update`, Befehle ohne Anführungszeichen | 404 auf frischem Ubuntu, kopierter Befehl kaputt | alle Pakete auf einmal, `update` davor, `shlex.join` |
| llama.cpp-Hinweis nannte x64-Pakete auf ARM | Paket startet nicht | Architektur zählt mit; Bauschalter steht dabei |
| `xdotool mousemove --sync` | Klick auf die Stelle, an der der Zeiger schon steht: 15 s Hänger, dann Absturz | ohne `--sync`, 0,16 s je Klick |
| Prüfstand `18_leistung` | auf einer schnellen VM bestand der langsame Ausgangscode | 40 000 statt 20 000 Einträge (innerhalb der Aufgabe) |

Dazu zwei Tests, die nur auf macOS stimmten (macOS-Rechtehinweis, großer Temp-Ordner), jetzt plattformbewusst.

**Nicht geprüft in der VM:** Wayland, Mesh mit Geräten im WLAN (die VM sitzt hinter NAT), Grafikkarte, ein vollständiger Computer-Use-Lauf (braucht das Grounding-Modell).

## Erster Lauf auf Windows (29.09.2026)

Windows 11 Pro 25H2, **ARM64, deutsch**, in einer UTM-VM auf dem Mac (4 Kerne, 6 GB), unbeaufsichtigt installiert. Python 3.12.10 vom offiziellen python.org-Installer. Ollama kam vom Mac, einmal über einen Tunnel als „anderer Rechner“ (Gemma 4 12B). Gesteuert über SSH und die echte Oberfläche im Browser.

**Ergebnis:** Die ganze Testsuite läuft unter Windows — **492 Tests, 7 mit Grund übersprungen** (fünf brauchen eine Sandbox, zwei bauen Linux-Werkzeuge als Shell-Skripte nach). Beim ersten Lauf waren es 50 Fehlschläge, und ein Test hing über eine Stunde.

| Alltagsaufgabe unter Windows | Ergebnis |
|---|---|
| Starter ohne Python | klare Anleitung (python.org, „Add to PATH“) |
| Einrichtung | Hardware richtig (CPU, 6 GB, „bis etwa 3 GB Modellgröße“), passende Vorschläge; Ollama auf anderem Rechner per IP erkannt |
| Chat | richtig, 13 s |
| Code-Sandbox | Umlaute und Emoji kommen heil an |
| Werkbank: Rechnungsfehler in `C:\Users\…\Übungen\rechnung` | richtig behoben, mit Test, 2,1 min, **6 Freigaben** |
| Werkbank: Kontoauszug (Windows-1252) | alle sechs Monatssummen auf den Cent, 13 min |

**Gefunden und behoben** (jeder Punkt mit Test; Commits 3bb4721, 3db2cbb, 3e605b8):

| Befund | Folge |
|---|---|
| Starter und `install.ps1` hielten den Microsoft-Store-Platzhalter `python.exe` für Python | Browser auf eine tote Seite, englische Windows-Meldung, keine Anleitung |
| Umgeleitete Ausgabe ist Windows-1252; ╔ ✓ ✅ beendeten Server, Export, Browser-Läufer | kein Server als Dienst oder mit Logdatei |
| Mesh: die Modellliste rief sich selbst auf, bis zur Rekursionsgrenze | auf dem Mac unsichtbar (aber ~65 000 Ollama-Anfragen je Stunde); unter Windows hing es |
| `localhost` fragte erst IPv6; abgewiesene Verbindungen dauern unter Windows 2 s | jede Modellanfrage 2 s langsamer, Statusseite 8 s |
| cmd.exe bekam die Befehlszeile als Liste (`\"`-Maskierung) | jeder Befehl mit Anführungszeichen scheiterte |
| Code-Sandbox kannte nur `sh`, `python3`, `.venv/bin` | „Code ausführen“ ging gar nicht |
| **Pfadregeln verglichen `docs\a.md` mit `docs/*`** | **kein Pfadverbot griff** — SICHERHEIT.md, Befund 13 |
| Zeitlimit und Stopp beendeten nur den obersten Prozess | gestartete Programme, Trainings, Exporte liefen weiter |
| Checkpunkte trauten Größe + Dateizeit; Windows zählt die Zeit nur alle ~15 ms | Zurücksetzen ließ eine Änderung stehen, die Sicherung davor hielt den alten Inhalt — Datenverlust möglich (betrifft jedes System, unter Windows aufgetreten) |
| Werkbank-Befehle ohne Windows-Systemvariablen | Ordner namens `%SystemDrive%` im Projekt |
| Modelle schreiben `python3` | unter Windows der Store-Platzhalter; jetzt umgeleitet, der Prompt nennt cmd.exe |
| Zeitüberschreitung des Modells | beendete den Lauf als „Fehler“; jetzt ein neuer Versuch, dann ein sauberes Ende |
| Ausgangsfilter kannte nur `/`-Pfade | der Ordnername konnte hinausgehen |
| Hinweise für das falsche System (apt, Lima) | unter Windows jetzt WSL2 bzw. nichts Falsches |
| Einrichtung meldete bei HTTP 403 „kein Ollama“ | Ollama ist da und verweigert — jetzt so gesagt |

**Computer-Use unter Windows (30.09.2026):** eigene Rückseite über die Win32-API (BitBlt, SendInput) ohne zusätzliches Programm. In der VM geprüft, in der angemeldeten Sitzung: Bildschirmfoto, Klick auf die richtige Stelle, „Grüße ✓ 42“ per Unicode getippt, Eingabetaste, Knopf. Gefunden dabei: Ohne ausdrückliche Typen presste ctypes 64-Bit-Handles in 32 Bit — das zweite Foto scheiterte. Über SSH oder als Dienst gibt Windows kein Bild heraus; das sagt die Diagnose.

**Was unter Windows fehlt, mit Absicht:** eine Sandbox für die Befehle des Agenten (deshalb jede Freigabe einzeln), Prüfstand, DowBench und Destillation (fremder Code nur mit Sandbox), Training (nur Apple Silicon). Für den ganzen Ablauf von /zeig und /steuern braucht es zusätzlich einen Grounding-Server; ob der unter Windows läuft, ist nicht geprüft. **Nicht geprüft:** Nvidia/CUDA, x64-Hardware, WSL2 (braucht verschachtelte Virtualisierung, die die Mac-VM nicht hat).

## Verbrauchertest: ein Spiel aus einer Idee, rein lokal (28.09.2026)

Dive on Wide 0.1048 in der Linux-VM, eingerichtet wie von einem neuen Nutzer, Modell qwen3.6-35b auf dem Mac. Als Nutzer nur die Idee eingegeben: „Frostwerk“, Sokoban mit Eis, Druckplatten/Türen, Förderbändern, Teleportern, Wasser/Brücken, Rückgängig, Löser, sechs Levels, curses. **Den Code schrieb ausschließlich das lokale Modell.**

**Was gelang:** Der Orchestrator baute in 105 s eine vernünftige Roadmap (8 Pakete: Kern, je Mechanik eins, Oberfläche, Levels/Löser). Die Werkbank erzeugte in 2 h 45 min ein echtes Projekt: Engine (780 Zeilen), Löser, curses-Oberfläche, 6 Levels, 57 Tests. Gezielte Aufträge („nur dieser eine Test“) liefen gut: vier Fehler in je 1–2 Minuten behoben. Der Agent fragte zurück, als ein Test sich selbst widersprach, statt ihn still abzuschwächen.

**Was nicht gelang:** Kein brauchbares Spiel. Nach gut sechs Stunden schlagen 15 von 57 Tests fehl, und der eigene Löser findet für kein Level eine Lösung. Beim Verzahnen der Mechaniken (Förderband + Eis + Türen) machte das Modell mit einer Reparatur oft drei andere Tests kaputt.

**Gefunden und behoben im Harness:**

| Befund | behoben |
|---|---|
| Der Modellkatalog maß die Modelle des Macs am Speicher der VM (2,8 GB) — für die Roadmap setzte der Harness das 0,5-B-Modell durch, Zeitüberschreitung nach 10 min | `adresse_entfernt()` in Katalog, Einrichtung und Mesh-Angebot |
| Alle 16 Werkbank-Läufe endeten am Schrittlimit; einer führte 29-mal fast denselben Befehl mit demselben Ergebnis aus — der alte Hinweis verglich Befehlstexte und griff nie | Fortschrittswächter am **Ergebnis**: Hinweis, dann Ende als „festgefahren“ |

**Gelernt, noch nicht gebaut:**
- Die Werkbank bekommt 67 % von `NUM_CTX` als Arbeitsgedächtnis — bei 16k rund 11 000 Token. Eine 780-Zeilen-Datei füllt das fast allein; der Agent vergisst und liest neu. Größerer Kontext kostet Speicher, den der Mac mit VM nicht hat (bei 32k: 9 % frei, 12 GB ausgelagert).
- Pakete dürfen nicht weiterlaufen, solange die Tests rot sind. Ein Abarbeiten der Roadmap sollte das selbst durchsetzen, statt dass der Nutzer es tut.
- Ein 35B-Modell mit 3 B aktiven Parametern trägt ein Projekt dieser Verzahnung nicht allein. Kleinerer Umfang je Idee oder ein stärkeres Modell.

## Erstnutzer auf Englisch: Linux und Windows (02.10.2026)

Frischer Speicher, Oberfläche auf Englisch, jede der 20 Ansichten einmal geöffnet; gezählt wurden Zeilen
mit deutschen Wörtern. Linux: Ubuntu-VM (Lima), **ohne** Ollama und ohne Grafikkarte — der Weg, den ein
neuer Linux-Nutzer zuerst sieht. Windows: die deutsche Windows-VM, Ollama vom Mac über einen Tunnel.

| | vorher (Paket 1.0.0-dev.1) | nachher |
|---|---|---|
| Einrichtung Linux | 14 deutsche Zeilen | 0 |
| Einrichtung Windows | — | 0 von 52 |
| Alle Ansichten Linux | — | 11 von 930 → behoben |
| Alle Ansichten Windows | — | 2 von 384 → behoben |
| Terminal (Banner, Starter, Installer) | nur Deutsch | Sprache des Systems; `DOWOS_SPRACHE=de/en` erzwingt |

**Ursachen, nicht nur Symptome:** Das Werkzeug, das die Texte für das Wörterbuch sammelt, übersah
Fragen (ein „?“ am Ende galt als Code), Texte über mehrere Zeilen, Namen ohne Leerzeichen
(„Code-Erklärer“), Sätze mit „;“ oder „ = “, ein einzelnes Zeichen in Anführungszeichen verschob die Paare
einer ganzen Zeile, und Meldungen anderer Programmteile wurden gar nicht gelesen. Gefunden wurden danach
rund 1 000 weitere Texte, lokal übersetzt (Qwen 3.6 35B, Stapel zu 25, je 20–35 s).

**Wortsalat statt Deutsch:** Der Ersatz bekannter Wendungen innerhalb einer Zeile tauschte auch einzelne
Wörter — „kann **no** Bilder verarbeiten — es sieht **not** the screen“. Jetzt nur Wendungen aus mehreren
Wörtern oder Namen, und bleibt Deutsch übrig, bleibt der ganze Satz deutsch. Meldungen mit eingesetzten
Werten (Modellname, Adresse, Fehlertext) stehen als Vorlage mit `{}` im Wörterbuch; die Oberfläche setzt
die Werte in die englische Fassung ein.

**Nebenbei gefunden:** Der Installer meldete Computer-Use auf Windows als „noch nicht gebaut“ und als
fehlend, obwohl es seit dem 29.09. läuft; sein Hinweis „auf Linux/Windows nie gelaufen“ war ebenfalls
überholt. Die Übersetzung „Outdoors today“ für „Heute nach draußen“ (was heute den Rechner verlassen hat)
heißt jetzt „Outbound today“.

**Bleibt:** Fehlermeldungen des Betriebssystems (z. B. „Es konnte keine Verbindung hergestellt werden“ auf
deutschem Windows) kommen in dessen Sprache. Inhalte des Nutzers und Antworten der Modelle werden nie übersetzt.

## Qwen3.8 27B in 4 Bit auf 24 GB — als Datei-Modell (03.10.2026)

`Qwen3.8-27B-UD-Q4_K_M.gguf` (15,3 GiB) direkt in `llama-server`, Mac mini M4 Pro, 24 GB:

| Einstellung | Ergebnis |
|---|---|
| alle Schichten auf der GPU, Standard-Stapel | „Insufficient Memory“ schon bei 1 100 Token |
| `-ub 256 -b 256 -ctk q8_0 -ctv q8_0`, Checkpoints 4, kein RAM-Cache | stabil bis 15 000 Token, 16,3 GiB, kein Auslagern |

Gemessen: Einlesen ~90 Token/s, Antworten ~10 Token/s; zwölf Agentenschritte mit wachsendem Verlauf
(1 000 → 12 000 Token) je 18–20 s. Freier Speicher dabei 10 % — mehr darf nebenher nicht laufen.

Daraus der Anbieter-Typ **„GGUF-Datei“** (`gguf_dienst.py`, Einstellungen → Modelle & Provider): Dive on Wide
startet `llama-server` erst, wenn das Modell gebraucht wird, gibt vorher die Ollama-Modelle frei, beendet ihn
nach 5 Minuten Leerlauf (`GGUF_LEERLAUF_S`) und vor jeder Anfrage an ein lokales Ollama-Modell — nie mitten in
einer Anfrage. Reste eines hart beendeten Laufs räumt der nächste Start auf. Echt durchgespielt: 27B →
Ollama 0.5B → 27B, Wiederanlauf aus dem Dateicache in ~4 s. Die Güte in 4 Bit ist noch nicht gemessen
(3 Bit: 40/72 bei 2,3 min je Aufgabe, 35B-A3B: 42/72 bei 0,7 min).

**Güte, Nacht 03./04.10.2026** (dieselben 72 Werkbank-Aufgaben, T=0, Vorhersage vorher in
`storage/datenwert/vergleich/voranmeldung.md`): Qwen3.8 27B Q4 **41/72** in 3,1 min je Aufgabe, ohne
Server-Neustart, Speicher stabil. Kontrolle Qwen 3.6 35B-A3B mit heutigem Stand **41/72** in 0,7 min.
Gleichstand — die Schwelle „mindestens +3“ ist verfehlt, der 35B bleibt die Empfehlung (4,4-mal schneller).
Bemerkenswert: Jedes Modell löst 7 Aufgaben, die das andere nicht schafft (gemeinsam 48/72).

## Roadmap abarbeiten mit dem lokalen 35B (04.10.2026)

Werkbank → „Roadmap abarbeiten“, drei Pakete (Temperatur-Modul, Kelvin, Kommandozeile), frisches Projekt
mit `DOWOS.md` (Testbefehl), Qwen 3.6 35B, Freigabe „nie“:

| Lauf | Ergebnis |
|---|---|
| 1 | **Halt bei Paket 1** nach 3 Reparaturen, Pakete 2 und 3 nie begonnen. Ursache war der Auftragskopf von Dive on Wide selbst: „Projekt in fp-temperatur“ las das Modell als Anweisung, einen Unterordner dieses Namens anzulegen — die Tests lagen dann nicht an der Wurzel. Die Sperre hat gegriffen; der Bericht nannte nur „Code 1“ und nennt jetzt die letzte Fehlerzeile. |
| 2 (Kopf ohne Ordnernamen) | **alle drei grün, ohne Reparatur, 2 min**: 10 → 20 → 30 Tests; nachgeprüft: Nullpunkt-Grenze, Hin-und-zurück, Rückgabecode 2 bei unbekannter Einheit. |

## Arbeitsgedächtnis der Werkbank und Aufräumen (05.10.2026)

- **Gliederung statt Vergessen:** Eine große Datei zeigt beim Lesen, was hinter dem Ausschnitt steht
  („weiter unten: Z.333 def … ; Z.912 class Spiel“, Anfang und Ende bleiben). Muss eine alte Leseausgabe
  dem Budget weichen, bleibt statt „[gekürzt]“ ihre Gliederung mit Pfad und Zeilenbereich stehen — der
  Agent weiß weiter, wo was stand, und liest gezielt mit von/bis. DowBench danach: 31/34, 0 Verstöße
  (vorher 31/34; die Aufgaben dort sind klein, der Gewinn liegt bei großen Dateien wie im Frostwerk-Test).
- **Gefunden beim Deinstallations-Check:** Jeder Werkbank-Lauf ließ seinen Arbeitsordner im TMPDIR liegen
  (`dowos_werkbank_*`, acht Stück vom 04.10.), der Volldurchlauf der Freigabe seinen Wegwerf-Speicher.
  Beides räumt jetzt selbst auf.

## Der Lotse, gemessen (05.10.2026)

26 Fragen (`pruefstand/lotse_fragen.json`, nur Messmaterial), automatisch bewertet: Schlüsselfakt enthalten, bei
„passt auf N GB?“ kein Modell genannt, das laut Dive on Wides eigener Rechnung nicht passt, bei Undokumentiertem ehrlich.
`werkzeuge/lotse_messen.py`.

| Modell | erste Messung | mit FAQ | s je Frage |
|---|---|---|---|
| Qwen 3.6 35B | 24/26 | **26/26** | 8 |
| Qwen 3.5 4B | 21/26 | 23/26 | 8 |
| Gemma 4 12B | 21/26 | 23/26 | 14 |

Gefunden in der ersten Messung: Die Frage „Was schickt Dive on Wide ins Internet?“ scheiterte bei allen drei Modellen — die
Suche landete bei „Recherche: Wie Dive on Wide ins Internet kommt“. Abhilfe ist `docs/FAQ.md` mit Fragen in den Worten der
Nutzer. Der Lotse nimmt deshalb das stärkste eingerichtete Modell (Rolle Lotse, sonst Werkbank-Modell, sonst Standard).
Ein trainiertes Lotsen-Modell ist damit nicht nötig: Die Lücken lagen in der Doku, nicht im Verständnis.

## Kleine Hardware (8 GB, nur CPU) — 05./06.10.2026

Lima-VM mit Ubuntu, 8 GB, 4 Kerne, keine Grafikkarte; Ollama und `qwen3:4b` **in** der VM (nicht vom Mac).

| Runde | Einstellung | Chat | Werkbank | Roadmap (2 Pakete) | Lotse |
|---|---|---|---|---|---|
| 1 | NUM_CTX 16 384, Ollama-Standard | 84 / 37 / 16 s | grün, 2,5 min | **Speicher voll nach 12 min** | — (Ollama tot) |
| 2 | NUM_CTX 8 192 | — | — | **Speicher voll nach 16 min** | 8/8, dann Abbruch |
| 3 | 8 192 + `LLAMA_ARG_CACHE_RAM=0` | — | — | **beide grün**, ~87 min | **24/24**, 104 s je Frage |
| 4 | wie 3, frisch | 88 / 67 / 15 s | grün, 21 min | über 75 min (Zeitgrenze), kein Abbruch | — |

Speicherspitze der VM: Runden 1–2 bis 7,8 GB (Linux beendete Ollamas `llama-server` mit 7,6 GB), Runden 3–4 stabil
bei 4,7 GB. **Ursache:** Ollamas Prompt-Cache (bis 8 GB) wächst über viele Agentenschritte — derselbe Mechanismus, der
am 25.09. das 27B auf dem Mac sprengte. Ein kleinerer Kontext half nicht, der abgeschaltete Cache schon.

**Daraus gebaut:**
- Dive on Wide sah nur „Remote end closed connection“; jetzt ist das eine erkannte Speichernot mit Abhilfe und Ausweichmodell.
- Einrichtung stellt `NUM_CTX` nach Rechner (unter 12 GB Modellspeicher: 8 192).
- Hinweis in der Einrichtung, Lotse, FAQ und README: auf kleinen Rechnern `LLAMA_ARG_CACHE_RAM=0` für Ollama
  (Dive on Wide ändert das nicht selbst — es ist Ollamas Einstellung). Auf Linux nachgemessen; macOS/Windows nach Ollamas
  Doku für Umgebungsvariablen, nicht gemessen.
- Einrichtung erkannte den Rechner richtig („CPU, ~3,9 GB für Modelle“) und schlug Qwen 3 4B vor.

## Eisrutsch mit der Roadmap-Funktion — 06.10.2026

Dieselbe Spielidee und `DOWOS.md` wie Anwendungsfall 10, diesmal mit der Produktfunktion „Roadmap abarbeiten“
(Tests-Sperre, Gliederung großer Dateien im Gedächtnis), Qwen 3.6 35B, vier Pakete.

| Lauf | Paket 1 (Regeln) | Paket 2 (Eis) | Pakete 3–4 |
|---|---|---|---|
| 28.09., Testtreiber | rot nach 3 Reparaturen | — | — |
| 06.10., 3 Reparaturen | rot (3 von 11), 26 min | nicht begonnen | nicht begonnen |
| 06.10., 5 Reparaturen | **grün, 16 Tests, ohne Reparatur, 7 min** | rot (5 von 22) nach 5 Reparaturen, 90 min | nicht begonnen |

Die Sperre hielt beide Male richtig an — kein Paket baute auf roten Tests weiter. Paket 1 gelang erstmals; die
Eis-Mechanik (Rutschen bis zum Hindernis) bleibt für dieses Modell zu schwer, wieder an den Rastertests.
**Gefunden:** Die Roadmap las den Testbefehl aus `DOWOS.md` nicht, weil hinter dem Befehl noch Text stand
(„— vor „fertig“ müssen ALLE Tests grün sein“); sie nahm den Standardbefehl. Behoben.

## Qwen3.8 35B-A3B Distill (IQ3_M, Datei) gegen Qwen 3.6 35B — 06.10.2026

`empero-ai/Qwen3.8-35B-A3B-Distill-GGUF`, Datei `IQ3_M` (16,3 GB, Prüfsumme geprüft), über den Anbieter „GGUF-Datei“
(llama-server, `-ub/-b 256`, KV q8_0, Checkpoints 4, kein RAM-Cache). Gleiche Aufgaben, T=0.

| | Qwen3.8 35B Distill IQ3 | Qwen 3.6 35B (3 Bit, Ollama) |
|---|---|---|
| Speicher | stabil bis 15 000 Token, Prozess 10,2 GB, 14 % frei, kein Auslagern | stabil |
| Tempo | 45–48 Token/s, Agentenschritt 4–8 s | ähnlich |
| DowBench (34) | 27/34 — Code 14/20 (3,5 min/Aufgabe), Terminal 4/4, Injektion 5/6 (kein Verstoß), Regeln 4/4 | **31/34** (0,5 min) |
| Werkbank-72 | 35/72 in 1,0 min/Aufgabe | **41/72** in 0,7 min |
| Lotse (26) | 26/26, 11 s | 26/26, 8 s |

**Urteil:** Der Distill ersetzt den 35B nicht — weniger gelöst, bei Code-Aufgaben deutlich langsamer. Er ist aber
der erste Qwen3.8-35B, der auf 24 GB stabil läuft, und für Erklären (Lotse) gleich gut. Datei bleibt unter
`~/DowOS/modelle/`; Anbieter „Qwen3.8 35B Distill (IQ3, Datei)“ ist eingetragen, startet nur bei Bedarf.

## 0.5.0-Paket in Linux- und Windows-VM (07.10.2026)

Das fertig gebaute Paket (nicht der Arbeitsstand) in beiden VMs ausgepackt und die ganze Suite laufen lassen.

| System | Ergebnis | Befund |
|---|---|---|
| Ubuntu (Lima-VM, ARM64) | 527/529, danach 529/529 | Beide Fehlschläge lagen am Test: Pakete enthalten absichtlich nur den eigenen Starter; Lima kennt `localhost` nicht über IPv6. Server startet, neue Startseite, die Suche nach lokalen Servern findet das Ollama der VM. |
| Windows 11 (UTM-VM, ARM64) | 526/529, danach alle betroffenen Gruppen grün | **Echter Fehler:** Ohne Sandbox fragt die Werkbank vor jedem Befehl — bei einer Roadmap hing die Frage am unsichtbaren Unterlauf des Pakets, die Roadmap stand still. Jetzt erscheint sie am Roadmap-Lauf. **Echter Fehler:** Speichernot meldet Windows als `WinError 10053`, das wurde nicht erkannt. Der Telegram-Fehlschlag war eine Folge der hängenden Roadmap. |

Noch nicht geprüft: echter x64-PC mit Nvidia-Karte, WSL2 (siehe `~/DowOS/PC-Test`).

## Stresstest der neuen Funktionen (08.10.2026)

Werkzeuge: `werkzeuge/stress_neu.py` (Scheinmodell, Wegwerf-Speicher) und `werkzeuge/fuzz.py` (jetzt mit allen neuen
Schnittstellen).

| Befund | Wirkung | Behoben |
|---|---|---|
| Wissensindex bei jeder Chat-Nachricht neu gebaut | 3 000 Einträge: 0,84 s vor jeder Antwort; 40 parallele Chats 36 s Median; 659 MB | Index je Wissensstand: 0,017 s, 200 Chats in 1,7 s, 218 MB |
| `messages: ["text"]` | Serverfehler 500 (Fuzz, 62 000 Anfragen, einziger Befund) | 400 mit Erklärung |
| Ausgangsbuch ohne Grenze | 200 000 Zeilen = 29 MB, Dashboard 0,6 s | ältere als 30 Tage ins Archiv (nichts gelöscht), Summe einmal |
| Rhythmus-Nachfolger zur selben Minute fällig | konnte das Ergebnis des Vorgängers von **gestern** laden | wartet auch auf fällige/wartende Vorgänger, ohne einen Laufplatz zu belegen; Vorgänger + 9 Nachfolger, 3 Plätze: alle in richtiger Reihenfolge |
| Angeheftetes ohne Gesamtgrenze | 10 Stücke = 80 000 Zeichen > 16k-Kontext | 24 000 Zeichen gesamt; Server kürzt einen zu großen Systemprompt und sagt es |
| Roadmap-Zuordnung Unterlauf → Roadmap | wuchs mit jedem Paket | wird nach jedem Paket entfernt |

**Schwarm-Parallelität, gemessen:** Drei Anfragen an dasselbe Modell laufen in Ollama 0.35 strikt nacheinander
(Faktor 1,00 — auch mit `OLLAMA_NUM_PARALLEL=3`, Ollama startet Qwen 3.5 mit einem Platz). Drei verschiedene Modelle
laufen verschränkt, sind aber nur 15 % schneller (Faktor 0,85): Eine Grafikkarte ist beim Erzeugen durch die
Speicherbandbreite begrenzt. Der Schwarm bringt auf einem Rechner saubere Kontexte, kein Tempo — die Texte sagen das jetzt.

Lange Unterhaltung: 300 Nachrichten mit Tabellen und Code zeichnen in ~50 ms.
