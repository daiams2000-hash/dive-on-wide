# -*- coding: utf-8 -*-
"""Der Lotse: ein Assistent, der Dive on Wide kennt — aus der echten Doku und den echten Zahlen dieses Rechners.

Er wird nicht auf Dive on Wide trainiert, er liest nach. Dive on Wide ändert sich mit jeder Version; ein
trainiertes Modell wäre nach dem nächsten Release veraltet, die Doku nicht. Drei Quellen:

1. **Fakten** (ohne Modell erhoben): Version, Rechner, installierte Modelle, die Empfehlung aus
   hardware.empfehlungen() und — falls vorhanden — die auf diesem Rechner gemessenen Ergebnisse.
2. **Doku-Ausschnitte**: README, docs/*.md, AGENTS.md, nach Überschriften zerlegt, per Stichwortsuche
   (BM25, reine Standardbibliothek) die passendsten Abschnitte.
3. **Erste Schritte**: eine Liste, was eingerichtet ist und was noch fehlt — aus dem Zustand berechnet.

Das Modell darf nur erklären, was dort steht. Empfehlungen nennt es so, wie die Fakten sie liefern.
"""
import math
import os
import re

from hardware import _kern

STOPP = set("""der die das und oder ein eine einen einem einer ist sind wie was wo wer wann warum mit für auf
in im am an zu zum zur von vom den dem des es sich ich du er sie wir ihr mein dein kann können muss soll
nicht kein keine auch noch nur schon mal bei aus über unter nach vor the a an and or is are how what where
why with for on in to of it this that can do does i you my your be not""".split())


# Gleichbedeutendes auf einen Stamm — „deinstallieren“ soll den Abschnitt „Entfernen“ finden
SYNONYME = {"deinstalli": "entfern", "deinstall": "entfern", "uninstall": "entfern", "remov": "entfern",
            "einricht": "einricht", "setup": "einricht", "aufsetz": "einricht", "instal": "install",
            "speich": "speicher", "ram": "speicher", "memory": "speicher", "arbeitsspeich": "speicher",
            "grafikkart": "gpu", "graphic": "gpu", "vram": "gpu", "modell": "modell", "model": "modell",
            "sicherheit": "sicher", "security": "sicher", "workbench": "werkbank", "rhythm": "rhythmus",
            # „Was schickt Dive on Wide ins Internet?“ — die Antwort steht unter „Ausgang“ (Ausgangsbuch)
            "schick": "ausgang", "verlass": "ausgang", "outbound": "ausgang", "ausgangsbuch": "ausgang",
            "nach drauß": "ausgang", "draußen": "ausgang"}


def woerter(text):
    """Kleinschreibung, Wörter ab 3 Zeichen, grobe Endungen ab — „Modelle“ findet „Modell“."""
    aus = []
    # Bindestriche trennen: „Discord-Chat“ soll „Discord“ finden
    for w in re.findall(r"[a-zäöüß0-9][a-zäöüß0-9._]{2,}", (text or "").lower().replace("-", " ")):
        if w in STOPP:
            continue
        for endung in ("ungen", "ung", "en", "er", "es", "e", "s"):
            if len(w) > len(endung) + 3 and w.endswith(endung):
                w = w[:-len(endung)]
                break
        for vorne, stamm in SYNONYME.items():
            if w.startswith(vorne):
                w = stamm
                break
        aus.append(w)
    return aus


def ist_englisch(text):
    w = re.findall(r"[a-zäöüß]+", (text or "").lower())
    en = sum(1 for x in w if x in ("the", "how", "what", "which", "why", "does", "can", "is", "do", "my", "to", "a", "i"))
    de = sum(1 for x in w if x in ("der", "die", "das", "wie", "was", "welche", "welches", "warum", "kann", "ist", "ich", "mein"))
    return en > de


def dokumente(app_dir, englisch=False, extra=()):
    """Abschnitte (Quelle, Überschrift, Text) aus der Doku — höchstens ~2500 Zeichen je Abschnitt."""
    dateien = ["README.md" if englisch else "README.de.md", "AGENTS.md"]
    docs = os.path.join(app_dir, "docs")
    if os.path.isdir(docs):
        dateien += ["docs/" + f for f in sorted(os.listdir(docs)) if f.endswith(".md")]
    abschnitte = []
    for rel in dateien + list(extra):
        pfad = rel if os.path.isabs(rel) else os.path.join(app_dir, rel)
        if os.path.isabs(rel):
            rel = "Eigene FAQ" if os.path.basename(rel) == "faq_eigen.md" else os.path.basename(rel)
        if not os.path.isfile(pfad):
            continue
        text = open(pfad, encoding="utf-8", errors="replace").read()
        teile = re.split(r"^(#{1,3} .+)$", text, flags=re.M)
        titel, kapitel = rel, ""
        for i, teil in enumerate(teile):
            if re.match(r"^#{1,3} ", teil):
                ebene, ueberschrift = len(teil) - len(teil.lstrip("#")), teil.lstrip("#").strip()
                if ebene <= 2:
                    kapitel = titel = ueberschrift
                else:                            # Unterabschnitt behält sein Kapitel: „Das Mesh › Was du bekommst“
                    titel = "%s › %s" % (kapitel, ueberschrift) if kapitel else ueberschrift
                continue
            inhalt = teil.strip()
            while inhalt:
                stueck, inhalt = inhalt[:2500], inhalt[2500:]
                if len(stueck) > 20:             # auch kurze eigene FAQ-Antworten zählen
                    abschnitte.append({"quelle": rel, "titel": titel, "text": stueck})
    return abschnitte


def doku_stand(app_dir):
    """Jüngste Änderung der Doku — billig genug, um es bei jeder Frage zu prüfen."""
    pfade = [os.path.join(app_dir, f) for f in ("README.md", "README.de.md", "AGENTS.md")]
    docs = os.path.join(app_dir, "docs")
    if os.path.isdir(docs):
        pfade += [os.path.join(docs, f) for f in os.listdir(docs) if f.endswith(".md")]
    return max((os.path.getmtime(p) for p in pfade if os.path.isfile(p)), default=0), len(pfade)


class Suche:
    """BM25 über die Abschnitte — genug, um „wie richte ich Discord ein“ auf den richtigen Absatz zu führen."""

    def __init__(self, abschnitte, k1=1.4, b=0.75):
        self.abschnitte = abschnitte
        self.tf = []
        df = {}
        for a in abschnitte:
            w = woerter(a["titel"] + " " + a["titel"] + " " + a["text"])   # Überschrift zählt doppelt
            zaehler = {}
            for x in w:
                zaehler[x] = zaehler.get(x, 0) + 1
            self.tf.append((zaehler, len(w)))
            for x in zaehler:
                df[x] = df.get(x, 0) + 1
        n = max(1, len(abschnitte))
        self.idf = {x: math.log(1 + (n - d + 0.5) / (d + 0.5)) for x, d in df.items()}
        self.avg = sum(l for _, l in self.tf) / n if self.tf else 1
        self.k1, self.b = k1, b

    def finden(self, frage, k=4):
        q = set(woerter(frage))
        werte = []
        for i, (zaehler, laenge) in enumerate(self.tf):
            s = 0.0
            for x in q:
                f = zaehler.get(x)
                if f:
                    s += self.idf[x] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * laenge / self.avg))
            if s > 0:
                werte.append((s, i))
        werte.sort(reverse=True)
        # Wie viel der Frage deckt der beste Abschnitt ab — seltene Wörter zählen mehr, Unbekannte am meisten
        hoechst = max(self.idf.values(), default=1.0)
        gewicht = {x: self.idf.get(x, hoechst) for x in q}
        abdeckung = 0.0
        if werte and gewicht:
            bester = self.tf[werte[0][1]][0]
            abdeckung = sum(g for x, g in gewicht.items() if x in bester) / sum(gewicht.values())
        return [dict(self.abschnitte[i], wert=round(s, 2), abdeckung=round(abdeckung, 2)) for s, i in werte[:k]]


def gedachter_rechner(frage):
    """„Welches Modell passt auf 16 GB?“ — die Frage nennt einen anderen Rechner. Gerechnet wird mit denselben
    Schwellen wie in der Einrichtung, nicht vom Modell."""
    m = re.search(r"(\d{1,3})\s*(?:gb|gib|gigabyte)", (frage or "").lower())
    if not m:
        return []
    gb = float(m.group(1))
    f = frage.lower()
    ohne_gpu = re.search(r"(ohne|keine?n?|no|without)\s+(dedizierte\s+|eigene\s+|a\s+)?(grafikkarte|gpu|graphics)|integriert", f)
    if not ohne_gpu and re.search(r"vram|nvidia|grafikkarte|gpu|rtx|radeon", f):
        return [("PC mit %g GB Grafikspeicher (und 32 GB RAM angenommen)" % gb,
                 {"system": "Windows", "ram_gib": 32, "unified": False, "gpus": [{"art": "nvidia", "vram_gib": gb}]})]
    varianten = [("Mac mit %g GB gemeinsamem Speicher" % gb, {"system": "Darwin", "ram_gib": gb, "unified": True, "gpus": []}),
                 ("PC ohne Grafikkarte mit %g GB RAM" % gb, {"system": "Linux", "ram_gib": gb, "unified": False, "gpus": []})]
    if "mac" in f:
        return varianten[:1]
    if ohne_gpu or re.search(r"\bpc\b|windows|linux|laptop", f):
        return varianten[1:]
    return varianten


def fakten_text(lage):
    """Was ohne Modell feststeht — in Stichpunkten, damit das Modell nichts umrechnen muss."""
    z = ["Dive-on-Wide-Version: %s" % lage.get("version", "?")]
    hw = lage.get("hardware") or {}
    if hw:
        z.append("Rechner: %s %s, %s GB Arbeitsspeicher, rechnet auf: %s, Modelle bis etwa %s GB, %s GB Platte frei"
                 % (hw.get("system", "?"), (hw.get("cpu") or {}).get("name", ""), hw.get("ram_gib", "?"),
                    hw.get("rechnet_auf", "?"), hw.get("modell_speicher_gib", "?"), hw.get("platte_frei_gib", "?")))
    modelle = lage.get("modelle") or []
    z.append("Installierte Modelle: %s" % (", ".join(modelle[:20]) if modelle else "keine (Ollama fehlt oder ist leer)"))
    z.append("Standardmodell: %s · Werkbank-Modell: %s" % (lage.get("standardmodell") or "—", lage.get("werkbank_modell") or "—"))
    for rolle, e in (lage.get("empfehlungen") or {}).items():
        beste = (e or {}).get("beste")
        if beste:
            z.append("Empfehlung für %s: %s (%s GB, %s%s)%s" % (
                e.get("text", rolle), beste.get("name"), beste.get("groesse_gb"), beste.get("stufe", ""),
                ", installiert" if beste.get("installiert") else ", nicht installiert",
                " — gemessen: " + beste["gemessen"] if beste.get("gemessen") else ""))
    for name, text in (lage.get("erfahrung") or [])[:8]:
        z.append("Im Alltag auf diesem Rechner (Werkbank): %s — %s" % (name, text))
    if lage.get("num_ctx"):
        z.append("Kontext (NUM_CTX): %s Token" % lage["num_ctx"])
    for name, g in list((lage.get("guete") or {}).items())[:8]:
        if g.get("geloest") is not None:
            z.append("Auf diesem Rechner gemessen: %s löste %s von %s Werkbank-Aufgaben, %.1f min je Aufgabe"
                     % (name, g["geloest"], g["aufgaben"], g.get("min_je_aufgabe") or 0))
        elif g.get("absturz"):
            z.append("Auf diesem Rechner gescheitert: %s — %s" % (name, g["absturz"][:160]))
    for beschreibung, e in lage.get("gedacht") or []:
        teile = []
        for rolle, r in e.items():
            if (r or {}).get("beste"):
                b = r["beste"]
                bequem = next((w for w in r.get("weitere") or [] if w.get("stufe") == "passt"), None)
                teile.append("%s: %s (%s GB, %s)%s" % (r.get("text", rolle).split(" — ")[0], b["name"], b["groesse_gb"],
                             b.get("stufe", ""), "; bequem passt %s (%s GB)" % (bequem["name"], bequem["groesse_gb"])
                             if b.get("stufe") == "knapp" and bequem else ""))
        z.append("Rechnung für einen %s: %s" % (beschreibung, "; ".join(teile) if teile else "kein passendes Modell im Katalog"))
    schalter = lage.get("schalter") or {}
    if schalter:
        z.append("Eingeschaltet: %s · Aus: %s" % (", ".join(k for k, v in schalter.items() if v) or "nichts",
                                                 ", ".join(k for k, v in schalter.items() if not v) or "nichts"))
    for p in probleme(lage):
        z.append("Gerade erkanntes Problem: %s" % p["text"])
    return "\n".join("- " + x for x in z)


ERSTE_SCHRITTE = [
    # Schlüssel, erledigt wenn …, Titel (de, en), wie (de, en)
    ("provider", lambda l: l.get("ollama_ok"), ("Modell-Server verbinden", "Connect a model server"),
     ("Ollama (ollama.com) oder LM Studio starten; Einstellungen → Modelle & Provider → „Lokale Server suchen“.",
      "Start Ollama (ollama.com) or LM Studio; Settings → Models & Providers → “Find local servers”.")),
    ("modelle", lambda l: bool(l.get("modelle")), ("Ein Modell laden", "Load a model"),
     ("Unter „Modelle“ steht, was zu deinem Rechner passt — ein Klick lädt es.",
      "“Models” shows what fits your machine — one click downloads it.")),
    ("chat", lambda l: l.get("chats", 0) > 0, ("Den ersten Chat führen", "Have your first chat"),
     ("„Erkläre mir Dive on Wide“ ist ein guter Anfang.", "“Explain Dive on Wide to me” is a good start.")),
    ("wissen", lambda l: l.get("wissen", 0) > 0, ("Eigenes Wissen hinzufügen", "Add your own knowledge"),
     ("Dateien oder Ordner ins Wissen ziehen — der Chat sucht dann selbst darin.",
      "Drag files or folders into Knowledge — the chat then searches it by itself.")),
    ("sandbox", lambda l: (l.get("schalter") or {}).get("Code ausführen"), ("Werkbank einschalten", "Switch on the Workbench"),
     ("Einstellungen → Code-Sandbox. Danach arbeitet der Agent in echten Projekten (Werkbank).",
      "Settings → Code sandbox. Then the agent works in real projects (Workbench).")),
    ("rhythmus", lambda l: l.get("zeitplaene", 0) > 0, ("Etwas regelmäßig erledigen lassen", "Schedule something"),
     ("Rhythmus → z. B. ein Morgenbriefing an Werktagen.", "Rhythm → e.g. a morning briefing on weekdays.")),
]


OHNE_ANTWORT = re.compile(r"steht nichts in der doku|nicht in der doku|keine angabe in der doku|dazu steht nichts|"
                         r"not (in|covered by) the doc|nothing in the doc", re.I)


def unbeantwortet(antwort, treffer):
    """Hat der Lotse gesagt, dass die Doku dazu schweigt — oder fand die Suche kaum etwas?"""
    return bool(OHNE_ANTWORT.search(str(antwort or ""))) or not treffer or treffer[0].get("abdeckung", 1) < 0.5


def erste_schritte(lage, englisch=False):
    i = 1 if englisch else 0
    return [{"schluessel": k, "aktion": k, "erledigt": bool(test(lage)), "titel": t[i], "wie": w[i]}
            for k, test, t, w in ERSTE_SCHRITTE]


# ---------------------------------------------------------------------------
# Helfen statt nur antworten (07.10.2026: „was ist das für ein Quatsch, man soll da selber Antworten eintragen?“)
# ---------------------------------------------------------------------------
# Der Lotse sieht, was gerade klemmt, und hängt an jede Antwort Knöpfe, die die Sache erledigen. Welche Knöpfe,
# entscheidet dieser Code — nicht das Modell. So kann es keine Menüs oder Befehle erfinden, die es nicht gibt.
# Aktionen sind Schlüssel; was sie tun, steht in der Oberfläche (LOTSE_AKTIONEN), nirgends sonst.

def _bester(lage, rolle="werkbank"):
    v = ((lage.get("empfehlungen") or {}).get(rolle) or {}).get("beste")
    return v if v and v.get("tag") else None


def probleme(lage, englisch=False):
    """Was gerade klemmt — [{text, aktion, label}], wichtigstes zuerst, höchstens drei."""
    t = (lambda de, en: en if englisch else de)
    aus = []
    if not lage.get("ollama_ok"):
        aus.append({"text": t("Kein Modell-Server erreichbar. Läuft Ollama (oder LM Studio)?",
                              "No model server reachable. Is Ollama (or LM Studio) running?"),
                    "aktion": "provider", "label": t("Server suchen", "Find servers")})
    elif not lage.get("modelle"):
        aus.append({"text": t("Es ist noch kein Modell geladen.", "No model is loaded yet."),
                    "aktion": "modelle", "label": t("Modelle ansehen", "See models")})
    standard = str(lage.get("standardmodell") or "").split("@@")[-1]
    if lage.get("modelle") and standard and standard not in lage["modelle"]:
        aus.append({"text": t("Das Standardmodell „%s“ ist nicht (mehr) da." % standard,
                              "The default model “%s” is not available (any more)." % standard),
                    "aktion": "einstellung:Modell-Standardwerte", "label": t("Anderes wählen", "Pick another")})
    b = _bester(lage)
    if lage.get("ollama_ok") and b and not b.get("installiert"):
        aus.append({"text": t("Für deinen Rechner empfohlen: %s (%s GB) — noch nicht geladen." % (b["name"], b["groesse_gb"]),
                              "Recommended for your machine: %s (%s GB) — not downloaded yet." % (b["name"], b["groesse_gb"])),
                    "aktion": "laden:" + b["tag"], "label": t("Laden", "Download")})
    return aus[:3]


# (Stichwörter, Aktion, Beschriftung de, en) — Reihenfolge = Vorrang
AKTIONEN = [
    (r"modell|model|passt|fits?|empfehl|recommend|laden|download|gguf|qwen|gemma|ram|grafik|gpu|vram",
     "modelle", ("Modelle öffnen", "Open models")),
    (r"werkbank|workbench|roadmap|projekt|project|code|programm", "werkbank", ("Werkbank öffnen", "Open workbench")),
    (r"sandbox|code ausf|befehl|command", "einstellung:Code-Sandbox", ("Code-Sandbox", "Code sandbox")),
    (r"wissen|knowledge|datei|file|ordner|folder|dokument", "wissen", ("Wissen öffnen", "Open knowledge")),
    (r"schwarm|swarm|team|parallel", "schwarm", ("Schwarm starten", "Start swarm")),
    (r"orchestr|ziel|goal|ablauf|pipeline", "orchestrator", ("Ziel an den Orchestrator", "Goal for the orchestrator")),
    (r"anbieter|provider|lm studio|vllm|llama\.cpp|server|cloud|openai|chatgpt|claude|gemini|grok|openrouter|api",
     "einstellung:Modelle & Provider", ("Modelle & Provider", "Models & providers")),
    (r"discord", "einstellung:Discord", ("Discord-Einstellungen", "Discord settings")),
    (r"rhythmus|rhythm|briefing|zeitplan|schedule|regelmäßig|jeden (tag|morgen)", "rhythmus", ("Rhythmus öffnen", "Open rhythm")),
    (r"training|trainier|lehrer|teacher|schüler|student|lora", "training", ("Training öffnen", "Open training")),
    (r"netz|mesh|diving net|fischernetz|gerät|device", "netz", ("Netzwerk öffnen", "Open network")),
    (r"recherch|research|web|suche|search", "einstellung:Recherche", ("Recherche-Einstellungen", "Research settings")),
]


def aktionen(frage, lage, englisch=False, beantwortet=True):
    """Knöpfe für eine Antwort: höchstens drei, aus der Frage abgeleitet und am Zustand geprüft."""
    i = 1 if englisch else 0
    f = (frage or "").lower()
    aus, gesehen = [], set()
    for muster, aktion, label in AKTIONEN:
        if re.search(muster, f) and aktion not in gesehen:
            gesehen.add(aktion)
            aus.append({"aktion": aktion, "label": label[i]})
    # Zustand: Werkbank gefragt, aber Code-Ausführung aus → zuerst einschalten
    if "werkbank" in gesehen and not (lage.get("schalter") or {}).get("Code ausführen") and "einstellung:Code-Sandbox" not in gesehen:
        aus.insert(0, {"aktion": "einstellung:Code-Sandbox", "label": ("Code-Ausführung einschalten", "Switch on code execution")[i]})
    # Modell gefragt und das empfohlene fehlt → gleich zum Laden anbieten
    b = _bester(lage)
    if "modelle" in gesehen and b and not b.get("installiert") and lage.get("ollama_ok"):
        aus.insert(0, {"aktion": "laden:" + b["tag"], "label": ("%s laden (%s GB)" % (b["name"], b["groesse_gb"]),
                                                              "Download %s (%s GB)" % (b["name"], b["groesse_gb"]))[i]})
    # Empfohlenes Modell ist da, aber nicht der Standard → mit einem Klick dazu machen
    if "modelle" in gesehen and b and b.get("installiert"):
        standard = str(lage.get("standardmodell") or "").split("@@")[-1]
        name = next((m for m in lage.get("modelle") or [] if _kern(m) == _kern(b["tag"])), "")
        if name and name != standard:
            aus.insert(0, {"aktion": "standard:" + name, "label": ("%s als Standard nehmen" % b["name"],
                                                                   "Make %s the default" % b["name"])[i]})
    if not beantwortet:
        aus.append({"aktion": "melden", "label": ("Frage an die Entwicklung schicken", "Ask the developers")[i]})
    return aus[:3]


VORSCHLAEGE = {
    "modelle": [("Welches Modell passt auf meinen Rechner?", "Which model fits my machine?"),
                ("Was ist der Unterschied zwischen Qwen 3.6 und Qwen 3.8?", "What is the difference between Qwen 3.6 and Qwen 3.8?")],
    "werkbank": [("Wie arbeite ich mit der Werkbank eine Roadmap ab?", "How do I work through a roadmap with the workbench?"),
                 ("Warum fragt die Werkbank vor jedem Befehl?", "Why does the workbench ask before every command?")],
    "knowledge": [("Wie nutzt der Chat mein Wissen?", "How does the chat use my knowledge?")],
    "training": [("Wie viel Rechenleistung braucht ein 4B-Schüler?", "How much compute does a 4B student need?")],
    "mesh": [("Wie verbinde ich meine Geräte im Diving Net?", "How do I connect my devices in the Diving Net?")],
    "settings": [("Was schickt Dive on Wide ins Internet?", "What does Dive on Wide send to the internet?")],
    "chat": [("Was ist der Schwarm?", "What is the swarm?"), ("Was macht der Orchestrator?", "What does the orchestrator do?")],
}
VORSCHLAEGE_IMMER = [("Was kann Dive on Wide?", "What can Dive on Wide do?"),
                     ("Etwas funktioniert nicht — was tun?", "Something doesn't work — what now?")]


def vorschlaege(ansicht, englisch=False):
    i = 1 if englisch else 0
    return [x[i] for x in (VORSCHLAEGE.get(ansicht or "", []) + VORSCHLAEGE_IMMER)][:4]


SYSTEM = """Du bist der Lotse von Dive on Wide — ein lokaler Assistent, der Nutzern Dive on Wide erklärt, beim Einrichten hilft und
Modelle passend zum Rechner empfiehlt.

Regeln:
- Antworte nur aus den FAKTEN und den DOKU-AUSSCHNITTEN unten. Steht etwas dort nicht, sag ehrlich: „Dazu steht nichts in
  der Doku“ und schlage vor, wo man nachsehen kann. Erfinde keine Menüs, Befehle, Zahlen oder Modellnamen.
- Empfehlungen zu Modellen nur so, wie die FAKTEN sie nennen (Empfehlung, Größe, gemessene Ergebnisse).
- Nenne konkrete Wege in der Oberfläche (z. B. „Einstellungen → Modelle & Provider“), wenn die Doku sie nennt.
- Fragt jemand, warum etwas nicht geht: Nenne zuerst die „gerade erkannten Probleme“ aus den FAKTEN, falls es welche gibt.
- Kurz und vollständig: das Wichtigste zuerst, kein Vorgeplänkel — aber jede konkrete Angabe aus der Doku, die zur
  Frage gehört, bleibt drin: Befehle, Dateinamen und ihr genaues Format, Menüwege, Zahlen, Programmnamen.
  Freundlich, in der Sprache der Frage. Die Oberfläche zeigt unter deiner Antwort passende Knöpfe — beschreibe
  den Weg trotzdem in Worten. Keine Quellenangaben im Text, die zeigt die Oberfläche selbst.

FAKTEN (von Dive on Wide erhoben, ohne Modell):
%s

DOKU-AUSSCHNITTE:
%s"""


def nachrichten(frage, lage, treffer, verlauf=()):
    doku = "\n\n".join("[%s — %s]\n%s" % (t["quelle"], t["titel"], t["text"]) for t in treffer) or "(keine passenden Abschnitte)"
    aus = [{"role": "system", "content": SYSTEM % (fakten_text(lage), doku)}]
    for n in list(verlauf)[-6:]:
        if n.get("role") in ("user", "assistant") and n.get("content"):
            aus.append({"role": n["role"], "content": str(n["content"])[:2000]})
    aus.append({"role": "user", "content": frage})
    return aus


STOERUNG = re.compile(r"funktioniert nicht|geht nicht|klappt nicht|fehler|kaputt|hängt|haengt|doesn.t work|not working|"
                      r"broken|error|stuck|problem", re.I)


def ist_allgemeine_stoerung(frage):
    """„Etwas funktioniert nicht“ ohne Einzelheiten — dann hilft eine Diagnose mehr als eine Doku-Antwort."""
    return bool(STOERUNG.search(frage or "")) and len(set(woerter(frage))) <= 4


def diagnose(lage, englisch=False):
    """Ein Prüfbericht ohne Modell: was läuft, was nicht — und die Bitte, genauer zu sagen, was klemmt."""
    t = (lambda de, en: en if englisch else de)
    s = lage.get("schalter") or {}
    hw = lage.get("hardware") or {}
    standard = str(lage.get("standardmodell") or "").split("@@")[-1]
    zeilen = [
        (lage.get("ollama_ok"), t("Modell-Server erreichbar", "Model server reachable")),
        (bool(lage.get("modelle")), t("%d Modell(e) da" % len(lage.get("modelle") or []), "%d model(s) available" % len(lage.get("modelle") or []))),
        (not standard or standard in (lage.get("modelle") or []), t("Standardmodell vorhanden (%s)" % (standard or "—"),
                                                                  "Default model present (%s)" % (standard or "—"))),
        (None if s.get("Code ausführen") is None else s.get("Code ausführen"),
         t("Code-Ausführung (Werkbank, Schwarm-Tests)", "Code execution (workbench, swarm tests)")),
    ]
    if hw.get("platte_frei_gib") is not None:
        zeilen.append((hw["platte_frei_gib"] > 10, t("Freier Platz: %s GB" % hw["platte_frei_gib"], "Free disk: %s GB" % hw["platte_frei_gib"])))
    text = t("Ich habe nachgesehen:", "I checked:") + "\n\n" + "\n".join(
        "- %s %s" % ("✅" if ok else "⚠️" if ok is False else "·", z) for ok, z in zeilen)
    p = probleme(lage, englisch)
    if p:
        text += "\n\n" + t("**Das fällt auf:** ", "**What stands out:** ") + " ".join(x["text"] for x in p)
    text += "\n\n" + t("Was genau geht nicht? Beschreib, was du tust und was passiert (oder die Fehlermeldung) — "
                         "dann helfe ich gezielt.",
                         "What exactly doesn't work? Describe what you do and what happens (or the error message) — "
                         "then I can help precisely.")
    return text
