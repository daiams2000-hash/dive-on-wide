#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser-Läufer — führt einen browser-use-Auftrag in einer eigenen Umgebung aus.

Warum als eigener Prozess? `browser-use` zieht Playwright, ein Chromium und ein
halbes Dutzend weiterer Pakete nach. Dive on Wide selbst kommt mit der
Standardbibliothek aus und soll mit **jedem** Python starten — auch mit einem
„extern verwalteten“ Systempython, in den man nichts installieren darf. Deshalb
liegt die schwere Abhängigkeit in einer eigenen Umgebung, genau wie beim
Training (`TRAINING_PYTHON`), und Dive on Wide ruft sie über diesen Läufer auf.

Aufruf (macht der Server):
    <venv>/bin/python browser_laeufer.py            # Auftrag als JSON auf stdin
    <venv>/bin/python browser_laeufer.py --pruefen  # nur melden, was vorhanden ist

Ausgabe, Zeile für Zeile auf stdout:
    SCHRITT <n>            — der Agent hat einen Schritt getan
    ERGEBNIS <json>        — {"bericht": "...", "urls": [...]}
    FEHLER <text>          — Abbruch mit Grund
"""

import json
import os
import sys


def pruefen():
    """Was ist in dieser Umgebung vorhanden? Ehrlich, ohne etwas vorzutäuschen."""
    aus = {"python": sys.executable, "browser_use": False, "version": "",
           "ollama_client": False, "chrome": "", "hinweise": []}
    try:
        import browser_use
        aus["browser_use"] = True
        aus["version"] = getattr(browser_use, "__version__", "") or "unbekannt"
    except Exception as e:
        aus["hinweise"].append("browser-use fehlt (%s)" % e)
    try:
        import ollama  # noqa: F401
        aus["ollama_client"] = True
    except Exception:
        aus["hinweise"].append("Python-Paket 'ollama' fehlt")
    # browser-use ab 0.13 spricht CDP direkt und braucht kein Playwright mehr;
    # es genügt ein Chrome oder Chromium auf dem Rechner.
    for kandidat in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "/Applications/Chromium.app/Contents/MacOS/Chromium",
                     "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
                     os.path.expanduser("~/AppData/Local/Google/Chrome/Application/chrome.exe")):
        if os.path.exists(kandidat):
            aus["chrome"] = kandidat
            break
    else:
        aus["chrome"] = ""
        aus["hinweise"].append("Kein Chrome oder Chromium gefunden — ohne Browser kein Browser-Auftrag.")
    return aus


def laufen(auftrag, melden=None):
    """Führt den Auftrag aus — mit Dive on Wides eigener Steuerung oder browser-use."""
    melden = melden or (lambda n: print("SCHRITT %d" % n, flush=True))
    if (auftrag.get("steuerung") or "dowos") == "dowos":
        import asyncio
        return asyncio.run(eigene_steuerung(auftrag, melden))
    return browser_use_agent(auftrag, melden)


def browser_use_agent(auftrag, melden):
    """Die Agentenschleife von browser-use — verlangt strukturiertes JSON je Schritt."""
    import asyncio
    from browser_use import Agent, BrowserProfile, ChatOllama

    llm = ChatOllama(model=auftrag["modell"], host=auftrag.get("ollama") or "http://localhost:11434",
                     timeout=float(auftrag.get("frist") or 600.0))
    schritte = {"n": 0}

    async def bei_schritt(*_a, **_kw):
        schritte["n"] += 1
        melden(schritte["n"])

    async def arbeiten():
        # Sicht nur, wenn das Modell Bilder kann. Sonst antwortet Ollama auf
        # jeden Schritt mit 400 („Multimodal data provided…“), der Agent tappt
        # blind herum und liefert am Ende ein leeres Ergebnis — gemessen am
        # 16.09.2026 mit qwen2.5-coder:14b.
        agent = Agent(task=auftrag["aufgabe"], llm=llm,
                      browser_profile=BrowserProfile(**profil_bauen(auftrag)),
                      use_vision=bool(auftrag.get("sicht")),
                      register_new_step_callback=bei_schritt)
        return await agent.run(max_steps=int(auftrag.get("max_schritte") or 25))

    historie = asyncio.run(arbeiten())
    bericht, urls = "", []
    try:
        bericht = historie.final_result() or ""
    except Exception:
        pass
    try:
        urls = [u for u in (historie.urls() or []) if u]
    except Exception:
        pass
    if not bericht:
        try:
            teile = [t for t in (historie.extracted_content() or []) if t]
            bericht = "\n\n".join(teile[-5:])
        except Exception:
            bericht = str(historie)[:4000]
    return {"bericht": bericht or "(Der Browser-Agent lieferte kein Ergebnis.)",
            "urls": list(dict.fromkeys(urls)), "schritte": schritte["n"]}


# --------------------------------------------------------------------------
# Dive-on-Wide-eigene Steuerung: Planer + Seitenelemente (und Grounder, wo nötig)
#
# Warum nicht die Agentenschleife von browser-use? Gemessen am 16.09.2026 auf
# diesem Rechner: browser-use verlangt je Schritt ein verschachteltes JSON
# (AgentOutput) und schickt Bildschirmfotos mit. gemma4:12b lieferte dafür
# ungültiges JSON, qwen3.8:27b lief in den Metal-Speicherfehler. Beides sind
# keine Modellfehler im engeren Sinn, sondern Folgen des Formats und der
# Nutzlast.
#
# Diese Schleife macht es wie Computer-Use in Dive on Wide: Der Planer entscheidet,
# WAS zu tun ist, und bekommt als Augen die **Elementliste der Seite** — Text,
# nummeriert, klein. Das ist im Browser die genauere Erdung als Pixel: Die
# Seite sagt selbst, was anklickbar ist. Nur wenn das nicht reicht (Bild,
# Zeichenfläche), fragt das Werkzeug "zeigen" den Grounding-Server nach
# Koordinaten — dieselbe Stelle, die Computer-Use benutzt.
# --------------------------------------------------------------------------

STEUER_PROMPT = """Du steuerst einen Browser für Dive on Wide und löst damit die Aufgabe des Nutzers.

Antworte in JEDEM Schritt mit genau einem JSON-Objekt und sonst nichts:
{"gedanke":"kurz: was du vorhast und warum","werkzeug":"<name>","argumente":{...}}

Werkzeuge:
- oeffnen   {"url":"https://…"}                 Seite aufrufen
- klicken   {"nummer":42}                       Element mit dieser Nummer anklicken
- tippen    {"nummer":9,"text":"…"}             Text in dieses Feld schreiben und Enter drücken
- scrollen  {"richtung":"runter"}               runter oder hoch
- lesen     {}                                  den Text der Seite lesen
- zeigen    {"beschreibung":"der blaue Knopf"}  nur wenn die Liste nicht reicht: im Bild suchen lassen
- fertig    {"antwort":"die Antwort für den Nutzer"}

Regeln:
- Die Nummern in eckigen Klammern sind die Elemente der Seite. Benutze nur Nummern, die dastehen.
- Erst schauen, dann handeln. Steht die Antwort schon auf der Seite, nimm "fertig".
- Kein Text außerhalb des JSON-Objekts."""


async def eigene_steuerung(auftrag, melden):
    """Die Schleife: Seite ansehen, Planer fragen, handeln — bis fertig."""
    import ollama
    from browser_use import BrowserSession, BrowserProfile
    from browser_use.browser.events import (ClickElementEvent, NavigateToUrlEvent,
                                            ScrollEvent, TypeTextEvent)

    sitzung = BrowserSession(browser_profile=BrowserProfile(**profil_bauen(auftrag)))
    await sitzung.start()
    klient = ollama.Client(host=auftrag.get("ollama") or "http://localhost:11434")
    grenze = int(auftrag.get("seiten_zeichen") or 6000)
    verlauf = [{"role": "system", "content": STEUER_PROMPT},
               {"role": "user", "content": "Aufgabe: " + auftrag["aufgabe"]}]
    besucht, antwort, modellfehler = [], "", 0
    try:
        for schritt in range(1, int(auftrag.get("max_schritte") or 20) + 1):
            zustand = await sitzung.get_browser_state_summary()
            url = zustand.url or ""
            if url:
                besucht.append(url)
            seite = ""
            try:
                seite = zustand.dom_state.llm_representation() or ""
            except Exception as e:
                seite = "(Seitenstruktur nicht lesbar: %s)" % e
            # Nur die AKTUELLE Seite steht im Verlauf. Hängte man jede Seitenliste
            # an, wüchse der Kontext mit jedem Schritt — auf 24 GB platzte der
            # Speicher zuverlässig ab Schritt drei (gemessen 16.09.2026). Was
            # vorher war, bleibt als eine Zeile stehen, damit der Planer den
            # Faden behält.
            for eintrag in verlauf:
                if eintrag["role"] == "user" and eintrag["content"].startswith("Seite: "):
                    kopf = eintrag["content"].split("\n", 1)[0]
                    eintrag["content"] = kopf + "\n(Elementliste dieser Seite ist nicht mehr im Verlauf.)"
            # Kleine Modelle drehen sich gern im Kreis. Ein Satz darüber, dass
            # sich nichts bewegt, holt sie meist heraus — kostet nichts.
            gleich = sum(1 for u in besucht[-4:] if u == url) if url else 0
            stockt = ("\nACHTUNG: Du bist seit %d Schritten auf derselben Seite. Ändere dein Vorgehen: "
                      "andere Nummer anklicken, scrollen, oder mit \"fertig\" antworten, wenn die "
                      "Antwort schon dasteht." % gleich) if gleich >= 3 else ""
            verlauf.append({"role": "user", "content":
                            "Seite: %s%s\n%s" % (url or "(noch keine)", stockt, seite[:grenze])})
            melden(schritt)
            try:
                # num_predict deckelt die Antwort: Der Planer braucht ein kurzes
                # JSON, kein Essay. Ohne Deckel verhedderte sich gemma4:12b in
                # Wiederholungen („token repeat limit reached“) und riss den
                # ganzen Lauf mit (gemessen 16.09.2026).
                roh = klient.chat(model=auftrag["modell"], messages=verlauf, format="json",
                                  options={"temperature": 0.1, "repeat_penalty": 1.15,
                                           "num_predict": 300})["message"]["content"]
            except Exception as e:
                modellfehler += 1
                if modellfehler > 3:
                    raise
                grenze = max(1500, grenze // 2)      # kürzer fragen und weitermachen
                verlauf = verlauf[:2] + verlauf[-4:]
                verlauf.append({"role": "user", "content":
                                "Der letzte Versuch scheiterte (%s). Antworte kurz: genau ein JSON-Objekt."
                                % str(e)[:120]})
                continue
            verlauf.append({"role": "assistant", "content": roh})
            try:
                plan = json.loads(roh)
                werkzeug = str(plan.get("werkzeug") or "").strip()
                args = plan.get("argumente") or {}
            except ValueError:
                verlauf.append({"role": "user", "content":
                                "Das war kein gültiges JSON. Antworte mit genau einem JSON-Objekt."})
                continue
            if werkzeug == "fertig":
                antwort = str(args.get("antwort") or "").strip()
                break
            ergebnis = await werkzeug_ausfuehren(sitzung, zustand, werkzeug, args, auftrag,
                                                 ClickElementEvent, NavigateToUrlEvent,
                                                 ScrollEvent, TypeTextEvent)
            verlauf.append({"role": "user", "content": "Ergebnis von %s: %s" % (werkzeug, ergebnis[:1500])})
            if len(verlauf) > 14:        # Kopf behalten, Mitte vergessen
                verlauf = verlauf[:2] + verlauf[-10:]
        if not antwort:
            antwort = "Kein Ergebnis: Der Planer hat die Aufgabe in %d Schritten nicht abgeschlossen." \
                      % int(auftrag.get("max_schritte") or 20)
    finally:
        try:
            await sitzung.kill()
        except Exception:
            pass
    return {"bericht": antwort, "urls": list(dict.fromkeys(besucht)), "schritte": schritt}


def profil_bauen(auftrag):
    profil = {"headless": bool(auftrag.get("headless"))}
    if (auftrag.get("modus") or "eigenes") == "chrome":
        if not auftrag.get("cdp_url"):
            raise RuntimeError("Modus „chrome“ braucht die Fernsteuerungsadresse (BROWSER_CDP_URL).")
        profil["cdp_url"] = auftrag["cdp_url"]
    else:
        if auftrag.get("chrome"):
            profil["executable_path"] = auftrag["chrome"]
        ordner = auftrag.get("profil_ordner") or os.path.expanduser("~/.dowos-browserprofil")
        os.makedirs(ordner, exist_ok=True)
        profil["user_data_dir"] = ordner
    return profil


async def werkzeug_ausfuehren(sitzung, zustand, werkzeug, args, auftrag,
                              ClickElementEvent, NavigateToUrlEvent, ScrollEvent, TypeTextEvent):
    """Ein Werkzeug des Planers ausführen und knapp zurückmelden."""
    async def element(nummer):
        karte = zustand.dom_state.selector_map
        el = karte.get(int(nummer))
        if el is None:
            raise KeyError("Element %s gibt es auf dieser Seite nicht. Vorhandene Nummern stehen "
                           "in der Liste." % nummer)
        return el

    try:
        if werkzeug == "oeffnen":
            url = str(args.get("url") or "").strip()
            if not url.startswith("http"):
                return "Abgelehnt: nur vollständige http(s)-Adressen."
            await sitzung.event_bus.dispatch(NavigateToUrlEvent(url=url))
            return "Seite geöffnet."
        if werkzeug == "klicken":
            el = await element(args.get("nummer"))
            await sitzung.event_bus.dispatch(ClickElementEvent(node=el))
            return "Geklickt."
        if werkzeug == "tippen":
            el = await element(args.get("nummer"))
            await sitzung.event_bus.dispatch(TypeTextEvent(node=el, text=str(args.get("text") or ""),
                                                           clear=True))
            return "Text eingetragen."
        if werkzeug == "scrollen":
            runter = str(args.get("richtung") or "runter").lower().startswith("r")
            await sitzung.event_bus.dispatch(ScrollEvent(direction="down" if runter else "up", amount=600))
            return "Gescrollt."
        if werkzeug == "lesen":
            try:
                text = await sitzung.get_current_page_text()
            except Exception:
                text = zustand.dom_state.llm_representation() or ""
            return (text or "")[:4000] or "(kein Text)"
        if werkzeug == "zeigen":
            return await zeigen(sitzung, str(args.get("beschreibung") or ""), auftrag)
        return "Unbekanntes Werkzeug. Erlaubt: oeffnen, klicken, tippen, scrollen, lesen, zeigen, fertig."
    except Exception as e:
        return "Fehler: %s" % e


async def zeigen(sitzung, beschreibung, auftrag):
    """Grounding wie bei Computer-Use: Bild an den Grounder, Koordinaten zurück.

    Im Browser ist das der Notnagel — die Elementliste der Seite ist genauer und
    billiger. Gebraucht wird es für das, was im DOM nicht steht: Bilder,
    Zeichenflächen, eingebettete Ansichten.
    """
    url = (auftrag.get("grounding_url") or "").rstrip("/")
    if not url:
        return ("Kein Grounding-Server eingerichtet (GROUNDING_API_URL). Nimm die Nummern aus der "
                "Elementliste — sie sind genauer als Koordinaten.")
    import base64
    import urllib.request
    from browser_use.browser.events import ScreenshotEvent
    ereignis = await sitzung.event_bus.dispatch(ScreenshotEvent())
    png = await ereignis.event_result()
    if isinstance(png, str):
        png = base64.b64decode(png)
    nutzlast = {"model": auftrag.get("grounding_modell") or "nvidia/LocateAnything-3B",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," +
                     base64.b64encode(png).decode()}},
                    {"type": "text", "text": "Locate the following UI element on this screenshot: %s. "
                     "Answer with the bounding box coordinates." % beschreibung}]}]}
    anfrage = urllib.request.Request(url + "/v1/chat/completions",
                                     data=json.dumps(nutzlast).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(anfrage, timeout=180) as r:
        antwort = json.loads(r.read().decode("utf-8"))
    text = antwort["choices"][0]["message"]["content"] or ""
    import re as _re
    treffer = _re.search(r"<box>\((\d+),(\d+)\),\((\d+),(\d+)\)</box>", text)
    if not treffer:
        return "Der Grounder hat nichts gefunden. Rohantwort: %s" % text[:200]
    x1, y1, x2, y2 = (int(g) for g in treffer.groups())
    mx, my = (x1 + x2) // 2, (y1 + y2) // 2
    try:
        el = await sitzung.get_dom_element_at_coordinates(mx, my)
        if el is not None:
            return "Gefunden bei (%d, %d) — das ist Element <%s>. Klicke es über seine Nummer." % (
                mx, my, getattr(el, "tag_name", "?"))
    except Exception:
        pass
    return "Gefunden bei (%d, %d) im Bild; im DOM ist dort kein Element." % (mx, my)


def main():
    if "--pruefen" in sys.argv:
        print("ERGEBNIS " + json.dumps(pruefen(), ensure_ascii=False))
        return 0
    try:
        auftrag = json.loads(sys.stdin.read() or "{}")
    except ValueError as e:
        print("FEHLER Auftrag ist kein gültiges JSON: %s" % e, flush=True)
        return 2
    if not auftrag.get("aufgabe") or not auftrag.get("modell"):
        print("FEHLER Auftrag braucht 'aufgabe' und 'modell'.", flush=True)
        return 2
    try:
        print("ERGEBNIS " + json.dumps(laufen(auftrag), ensure_ascii=False), flush=True)
        return 0
    except Exception as e:
        print("FEHLER %s: %s" % (type(e).__name__, e), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
