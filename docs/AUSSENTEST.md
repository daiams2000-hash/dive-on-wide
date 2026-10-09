# Außentests — was auf echten Geräten zu prüfen bleibt

Alles, was auf einem einzelnen Mac geprüft werden konnte, steht in `ABNAHME.md` und `SICHERHEIT.md`. Offen ist, was nur mit **anderen Geräten, anderen Systemen oder echten Anmeldungen** geht. Für jeden Test hier: was zu tun ist, wie lange es dauert, was „bestanden" heißt und **was zurückkommen soll**.

Die Werkzeuge liegen im Paket unter `werkzeuge/`. Ihre Berichte enthalten keine Rechnernamen, keine Pfade aus dem Heimatordner und keine Schlüssel — sie können vollständig zurückgeschickt werden.

---

## 1. Ein fremder Rechner (macOS, Linux, Windows) — 10 Minuten

Das Paket entpacken, im Ordner:

```
python3 werkzeuge/aussentest.py
```

Unter Windows `py werkzeuge\aussentest.py`. Der Bericht prüft System, Ollama samt Fähigkeiten der Modelle, die Sandbox mit drei echten Befehlen, Computer-Use, den Mesh-Knoten, die verteilte Inferenz, ob `claude`/`codex` installiert sind — und lässt am Ende die ganze Testsuite laufen. Mit `--schnell` ohne Testsuite.

**Bestanden:** Die Testsuite meldet „Alle … Tests bestanden", und die Sandbox-Zeilen stehen auf „richtig". Unter Windows gibt es keine Sandbox; dann muss dort stehen, dass jeder Befehl freigegeben werden muss.

**Zurück:** die Datei `aussentest_<system>_<datum>.txt`.

Danach Dive on Wide starten (`./start.sh`, unter Windows über das Installationspaket) und die Einrichtung einmal durchklicken: Wird ein Modell empfohlen, das in den Speicher passt?

## 2. Zwei Geräte im selben Netz — 15 Minuten

Gerät B hat ein Modell in Ollama und rechnet, Gerät A fragt:

```
# auf Gerät B
python3 werkzeuge/zwei_geraete.py rechnen

# auf Gerät A
python3 werkzeuge/zwei_geraete.py fragen
```

Gerät B nimmt von selbst das größte Modell, das in seinen Speicher passt; mit `--modell <name>` ein bestimmtes. Finden sich die beiden nicht — in Gäste- und Firmen-WLANs ist Multicast oft gesperrt —, gibt Gerät B beim Start eine Ticketzeile aus. Die kommt auf Gerät A hinter `--ticket "…"`.

**Bestanden:** Auf Gerät A steht `ERGEBNIS : BESTANDEN — echte Inferenz über zwei Geräte`. Steht dort „Netz bestanden, Modell nicht", hat das Netz funktioniert und nur das Modell auf Gerät B falsch gerechnet — dann dort ein größeres wählen.

**Zurück:** die Berichte **beider** Seiten, und ob es mit oder ohne Ticket ging.

## 3. Ein Modell über zwei Geräte verteilt — 30 Minuten, für Fortgeschrittene

Auf einem Rechner ist das belegt (24 Schichten auf drei Knoten, siehe `ABNAHME.md`). Über zwei Geräte fehlt noch ein eigenes Werkzeug; der Weg geht über die Oberfläche, beide Male unter **Netzwerk**: auf Gerät B „Dieses Gerät darf Schichten für andere halten“ anhaken, auf Gerät A „▶ Dieses Modell jetzt verteilt starten“. Dafür braucht jedes Gerät das offizielle llama.cpp-Paket seines Systems; fehlt es, steht unter **Netzwerk** die passende Anleitung (Tabelle auch in `README.de.md`).

**Achtung, bevor du das tust:** Die Rechenschnittstelle von llama.cpp hat **keine Anmeldung**. Wer sie im Netz erreicht, kann darauf rechnen lassen. Nur im eigenen, vertrauten Netz einschalten und danach wieder aus.

**Bestanden:** Das verteilte Modell erscheint in der Modellliste und beantwortet eine Frage. **Zurück:** wie lange die erste Antwort brauchte, und ob Gerät B dabei mitrechnet (Aktivitätsanzeige).

## 4. Dive on Wide beauftragt Claude Code — 15 Minuten

Voraussetzung: Die Claude-Code-Kommandozeile ist installiert und angemeldet (`claude`, dann `/login`). Auf diesem Mac ist sie das derzeit **nicht**.

In Dive on Wide unter **Werkbank → Profile → Externe Agenten als Unteragenten** Claude Code einschalten, dann eine Werkbank-Aufgabe geben, die eine Teilaufgabe abgibt. Jeder einzelne Aufruf muss vorher freigegeben werden.

**Wichtig zu wissen:** Claude Code läuft außerhalb der Sandbox von Dive on Wide und schickt die Aufgabe samt Projektinhalten an Anthropic. Die Oberfläche sagt das bei jeder Freigabe.

**Bestanden:** Die Teilaufgabe kommt zurück, und im Verlauf steht, welcher Agent sie erledigt hat. **Zurück:** der Verlauf des Laufs (Werkbank → Läufe).

## 5. Dive on Wide vom Handy — 5 Minuten

Im selben WLAN `http://<IP-des-Rechners>:3000` öffnen. Die Statuszeile muss „Zugangsschlüssel nötig" zeigen und ein Anmeldedialog erscheinen. Mit dem 👑-Schlüssel aus **Einstellungen → Zugänge** anmelden (ein 🧪-Gast-Schlüssel darf keinen Code ausführen), dann im Browsermenü „Zum Home-Bildschirm".

**Bestanden:** Anmelden klappt, ein Chat läuft, das Symbol startet Dive on Wide wie eine App. **Zurück:** Handymodell und was nicht ging.

## 6. Ein Bot (Telegram, Discord oder Slack) — 10 Minuten

Einen Bot beim jeweiligen Dienst anlegen (bei Telegram über @BotFather), den Schlüssel unter **Einstellungen** in der Karte des jeweiligen Dienstes (Telegram, Discord, Slack) eintragen, dem Bot eine Nachricht schicken.

**Bestanden:** Die Antwort kommt im Messenger an. **Zurück:** welcher Dienst, und ob die Antwort kam.

## 7. Mit einer echten Tastatur — 1 Minute

Im Chat: Enter sendet, Shift+Enter macht eine neue Zeile, `/` öffnet die Liste, die Pfeiltasten wandern (oben am Ende springt es nach unten), Tab übernimmt, Escape schließt. Die Behandlung ist mit erzeugten Tastaturereignissen belegt — ob eine echte Tastatur dieselben Ereignisse schickt, nicht.

---

## Was zurückkommen soll

Die Berichtsdateien aus Test 1 und 2 und zu jedem anderen Test ein, zwei Sätze. Mehr braucht es nicht, um jeden Fehler nachzustellen.
