# -*- coding: utf-8 -*-
"""Prüfstand für das Hausmodell: zufällige Modellkataloge und ein Schiedsrichter aus Code.

Das Hausmodell soll lernen, den Katalog zu LESEN, der vor ihm liegt — nicht,
welche Modelle es auf diesem einen Rechner gibt (docs/HAUSMODELL.md). Deshalb:

  katalog(zufall, familien)  baut einen Katalog, wie ihn ein fremder Rechner
                             hätte: erfundene Namen, Größen, Speicher, Fähigkeiten,
                             manchmal gemessene Güte, manchmal ein Absturz.
  pruefen(roh, art, kat)     bewertet einen Plan ausschließlich mit Code und
                             nennt jeden Fehler in einem Satz, den der Planer
                             versteht — daraus werden Korrekturdialoge.

Trainingsfamilien und zurückgehaltene Familien sind getrennt. Besteht ein Modell
auf Katalogen mit Familien und Namen, die es nie gesehen hat, hat es die
Fertigkeit gelernt und nicht die Fakten.

Die Regeln hier sind dieselben, die ORCHESTRATOR_PROMPT dem Planer gibt, und
dieselben, die der Harness durchsetzt (modellwahl_pruefen, plan_bereinigen).
"""
import json
import re

TYPEN = ("agent", "web", "research", "code", "browser", "think", "image")

# Was ein Ziel dieser Art braucht und was es nicht darf. „muss": mindestens ein
# Typ aus jeder Menge. Die Grenzen folgen dem Prompt („So WENIGE Schritte wie
# moeglich", „code NUR wenn…", „image nur, wenn ein Bild verlangt wird").
ARTEN = {
    "code":      {"muss": [{"code"}], "verboten": {"image"}, "hoechstens": 4},
    "recherche": {"muss": [{"research", "web", "browser"}], "verboten": {"code", "image"}, "hoechstens": 4},
    "text":      {"muss": [], "verboten": {"code", "research", "web", "browser", "image"}, "hoechstens": 3},
    "bild":      {"muss": [{"image"}], "verboten": {"code", "research"}, "hoechstens": 3},
}

# Trainingsfamilien und zurückgehaltene Familien. Gemma und DeepSeek bleiben
# draußen, damit auch der echte Katalog dieses Rechners (gemma4) für das
# Hausmodell fremd ist.
FAMILIEN_TRAINING = ("qwen2.5", "qwen3", "llama3.1", "llama3.2", "mistral", "mistral-nemo",
                     "phi4", "granite3.3", "internlm3", "yi1.5", "command-r", "hermes3")
FAMILIEN_ZURUECK = ("gemma3", "deepseek-r1", "olmo2", "falcon3", "exaone3.5", "smollm2", "nemotron")

GROESSEN_B = (0.5, 1.5, 3, 4, 7, 8, 9, 12, 14, 22, 24, 27, 32, 35, 70)
RAM_GIB = (8, 16, 24, 32, 36, 64)


def _name(z, familie, b):
    art = z.random()
    groesse = ("%gb" % b).replace(".", "_") if z.random() < 0.1 else "%gb" % b
    if art < 0.12:
        return "%s-coder:%s" % (familie, groesse), {"coder"}
    if art < 0.2:
        return "%s-vision:%s" % (familie, groesse), {"vision"}
    if art < 0.3:
        teil = familie.replace(".", "").capitalize()
        return "hf.co/%s/%s-%gB-GGUF:Q4_K_M" % (z.choice(("unsloth", "bartowski", "lmstudio")), teil, b), set()
    if art < 0.4:
        return "%s:%s-instruct-q4_K_M" % (familie, groesse), set()
    return "%s:%s" % (familie, groesse), set()


def katalog(z, familien=FAMILIEN_TRAINING, mindestens=1, hoechstens=8):
    """(Einträge, Güte, RAM) — Einträge im Format von server.modell_katalog().

    `z` ist ein random.Random; derselbe Startwert ergibt denselben Katalog."""
    import server   # Speicherstufen genau wie im Harness
    ram = z.choice(RAM_GIB)
    eintraege, gesehen = [], set()
    for _ in range(z.randint(mindestens, hoechstens)):
        familie, b = z.choice(familien), z.choice(GROESSEN_B)
        label, merkmale = _name(z, familie, b)
        if label in gesehen:
            continue
        gesehen.add(label)
        groesse = int(b * 0.62 * 1024 ** 3 * z.uniform(0.9, 1.15))
        faehig = ["completion"]
        if "coder" in merkmale or z.random() < 0.05:
            faehig.append("insert")
        if "vision" in merkmale or z.random() < 0.2:
            faehig.append("vision")
        if b >= 4 and z.random() < 0.45:
            faehig.append("thinking")
        if z.random() < 0.7:
            faehig.append("tools")
        eintraege.append({"name": "ollama@@" + label, "label": label, "anbieter": "Ollama",
                          "groesse": groesse, "parameter": "%gB" % b, "faehigkeiten": faehig,
                          "kontext": z.choice((8192, 32768, 131072, 262144)),
                          "speicher": server.speicher_stufe(groesse, ram),
                          "passt": server.modell_passt(groesse, ram)})
    # Mindestens ein Modell muss passen — sonst gibt es keine richtige Antwort.
    if not any(e["speicher"] != "zu_gross" for e in eintraege):
        b = z.choice((1.5, 3, 4))
        label = "%s:%gb" % (z.choice(familien), b)
        groesse = int(b * 0.62 * 1024 ** 3)
        eintraege.append({"name": "ollama@@" + label, "label": label, "anbieter": "Ollama",
                          "groesse": groesse, "parameter": "%gB" % b, "faehigkeiten": ["completion", "tools"],
                          "kontext": 32768, "speicher": server.speicher_stufe(groesse, ram),
                          "passt": True})
    z.shuffle(eintraege)
    guete = {}
    passend = [e for e in eintraege if e["speicher"] != "zu_gross"]
    if z.random() < 0.45 and passend:
        for e in z.sample(passend, min(len(passend), z.randint(1, 3))):
            guete[e["label"]] = {"geloest": z.randint(3, 60), "aufgaben": 72,
                                 "min_je_aufgabe": round(z.uniform(0.4, 4.0), 1)}
    if z.random() < 0.15 and len(passend) > 1:
        opfer = z.choice(passend)
        opfer.update(speicher="zu_gross", passt=False, absturz="hier unter Last aus dem Speicher gelaufen")
        guete.pop(opfer["label"], None)
    return eintraege, guete, ram


def katalog_zeilen(eintraege, guete):
    import server
    return server.katalog_text(eintraege, guete=guete)


def nachrichten(ziel, eintraege, guete):
    """Genau das, was run_orchestrator dem Planer schickt."""
    import server
    return [{"role": "system", "content": server.ORCHESTRATOR_PROMPT},
            {"role": "user", "content": "Verfügbare Modelle:\n%s\n\nZiel: %s" % (katalog_zeilen(eintraege, guete), ziel)}]


def _plan_lesen(roh):
    import server
    plan = server.extract_json_or_none(roh or "")
    if not isinstance(plan, dict):
        return None
    schritte = plan.get("schritte")
    if not isinstance(schritte, list) or not schritte or not all(isinstance(s, dict) for s in schritte):
        return None
    return schritte


def bestes_code_modell(eintraege, guete):
    """(Labels, die für einen Code-Schritt richtig sind, Begründung) — wie modellwahl_pruefen."""
    passend = [e for e in eintraege if e.get("speicher") != "zu_gross"]
    gemessen = [e for e in passend if isinstance(guete.get(e["label"]), dict) and guete[e["label"]].get("aufgaben")]
    if gemessen:
        hoch = max(guete[e["label"]]["geloest"] for e in gemessen)
        return {e["label"] for e in gemessen if guete[e["label"]]["geloest"] == hoch}, \
            "das hat hier die meisten Werkbank-Aufgaben gelöst"
    coder = [e for e in passend if "insert" in (e.get("faehigkeiten") or []) or "coder" in e["label"].lower()]
    if coder:
        return {e["label"] for e in coder}, "ein Modell mit dem Merkmal Code"
    return {e["label"] for e in passend}, "irgendein passendes Modell"


def pruefen(roh, art, eintraege, guete):
    """{"ok", "fehler": [Sätze], "schritte", "typen"} — nur Code, kein Modell."""
    schritte = _plan_lesen(roh)
    if schritte is None:
        return {"ok": False, "fehler": ["Die Antwort ist kein gültiges JSON mit einer Liste „schritte“."],
                "schritte": 0, "typen": [], "gueltig": False}
    fehler = []
    labels = {e["label"]: e for e in eintraege}
    richtig_code, grund_code = bestes_code_modell(eintraege, guete)
    typen = []
    for nr, s in enumerate(schritte, 1):
        typ = s.get("typ")
        typen.append(typ)
        if typ not in TYPEN:
            fehler.append("Schritt %d: Den Typ „%s“ gibt es nicht. Erlaubt sind: %s." % (nr, typ, ", ".join(TYPEN)))
        modell = str(s.get("modell") or "").strip()
        if not modell:
            fehler.append("Schritt %d: Es fehlt „modell“. Wähle ein Modell aus der Liste." % nr)
            continue
        e = labels.get(modell)
        if e is None:
            fehler.append("Schritt %d: Das Modell „%s“ steht nicht in der Liste. Schreibe den Namen exakt so, "
                          "wie er dort steht." % (nr, modell[:80]))
            continue
        if e.get("speicher") == "zu_gross":
            fehler.append("Schritt %d: „%s“ ist als PASST NICHT markiert und darf nie gewählt werden." % (nr, modell))
            continue
        if typ == "code" and modell not in richtig_code:
            fehler.append("Schritt %d (code): Nimm „%s“ — %s." % (nr, sorted(richtig_code)[0], grund_code))
    regel = ARTEN[art]
    vorhanden = set(typen)
    for menge in regel["muss"]:
        if not vorhanden & menge:
            fehler.append("Für dieses Ziel fehlt ein Schritt vom Typ %s." % " oder ".join(sorted(menge)))
    for typ in sorted(vorhanden & regel["verboten"]):
        fehler.append("Ein Schritt vom Typ „%s“ passt nicht zu diesem Ziel." % typ)
    if {"web", "research"} <= vorhanden:
        fehler.append("„web“ und „research“ nicht zusammen: research sucht bereits selbst im Web.")
    for a, b in zip(typen, typen[1:]):
        if a == b and a != "agent":
            fehler.append("Zwei gleiche Schritte „%s“ direkt hintereinander." % a)
            break
    if len(schritte) > regel["hoechstens"]:
        fehler.append("Zu viele Schritte (%d); für dieses Ziel reichen höchstens %d." % (len(schritte), regel["hoechstens"]))
    return {"ok": not fehler, "fehler": fehler, "schritte": len(schritte), "typen": typen, "gueltig": True}


def rueckmeldung(ergebnis):
    """Der Text, mit dem der Harness einen falschen Plan zurückgibt (Korrekturdialog)."""
    return ("Der Plan wurde nicht angenommen:\n- " + "\n- ".join(ergebnis["fehler"]) +
            "\nSchreibe den ganzen Plan neu, wieder NUR als JSON.")


def ziel_passt(ziel, art):
    """Würde der Harness diesen Plan überhaupt so ausführen? plan_bereinigen
    wirft Code-Schritte ohne Codewörter und Bild-Schritte ohne Bildwörter
    heraus — ein Ziel, bei dem das passieren würde, taugt nicht als Beispiel."""
    import server
    z = (ziel or "").lower()
    will_code = any(w in z for w in server.CODE_WOERTER)
    will_bild = any(w in z for w in ("bild", "grafik", "illustration", "foto", "logo"))
    if art == "code":
        return will_code and not will_bild
    if art == "bild":
        return will_bild and not will_code
    return not will_code and not will_bild


def als_text(plan):
    return json.dumps(plan, ensure_ascii=False)


def einfach(roh):
    """Nur das JSON — so wird ein angenommener Plan zum Trainingsbeispiel (ohne ```-Zäune)."""
    import server
    plan = server.extract_json_or_none(roh or "")
    return als_text(plan) if isinstance(plan, dict) else re.sub(r"^```\w*\n|\n```$", "", (roh or "").strip())
