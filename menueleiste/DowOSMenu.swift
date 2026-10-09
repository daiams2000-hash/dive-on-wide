// Dive on Wide — die Ecke oben rechts.
//
// WAS DAS IST UND WAS NICHT
// -------------------------
// Eine Hülle um die vorhandene HTTP-Schnittstelle, kein zweites Produkt. Es
// gibt hier keine Geschäftslogik, keinen eigenen Zustand und keinen Speicher:
// Alles, was angezeigt wird, kommt von /api/health und /api/mesh, und alles,
// was der Nutzer anklickt, ist ein Aufruf derselben Schnittstelle, die auch
// die Weboberfläche benutzt. Wenn das Menü etwas könnte, was die Oberfläche
// nicht kann, wäre das ein Fehler.
//
// WARUM SWIFT UND NICHT PYTHON
// ----------------------------
// Ein Menüleisten-Symbol braucht NSStatusItem, also AppKit. Aus Python ginge
// das nur mit PyObjC — und Dive on Wide hat null Abhängigkeiten, das ist keine
// Zierde, sondern die Zusage. Swift liegt bei den Xcode-Befehlszeilen-
// werkzeugen bei, die auf macOS ohnehin schon für python3 gebraucht werden.
// Fehlt swiftc, sagt das Bauskript das und nichts geht kaputt: Das Menü ist
// Zubehör, Dive on Wide läuft ohne es vollständig.
//
// KEIN EIGENER RECHTEBEDARF
// -------------------------
// Es wird ausschließlich mit 127.0.0.1 gesprochen. Kein Netzzugriff nach
// außen, kein Dateizugriff, keine Bedienungshilfen. Das Menü kann nichts,
// was ein Browser auf derselben Maschine nicht auch könnte.

import AppKit
import Foundation

let standardPort = 3000

func portLesen() -> Int {
    // Die .env liegt neben der App im Dive-on-Wide-Ordner. Nicht gefunden? Dann
    // 3000, wie der Server selbst. Zwei Quellen für dieselbe Zahl wären eine
    // Gelegenheit, dass sie auseinanderlaufen.
    if let arg = ProcessInfo.processInfo.environment["DOWOS_PORT"],
       let p = Int(arg) { return p }
    // Nach OBEN suchen, bis die .env neben server.py auftaucht. Zwei feste
    // Ebenen reichten NICHT: Das Programm steckt in .app/Contents/MacOS/, die
    // .env liegt vier Ebenen darueber im Dive-on-Wide-Ordner. Vorher landete die App
    // deshalb still auf Port 3000 und zeigte den Zustand eines Servers, der
    // gar nicht der eigene war — ein Symbol, das das Falsche behauptet, ist
    // schlimmer als gar keines.
    var ordner = URL(fileURLWithPath: CommandLine.arguments[0])
        .resolvingSymlinksInPath().deletingLastPathComponent()
    for _ in 0..<7 {
        let env = ordner.appendingPathComponent(".env")
        if let text = try? String(contentsOf: env, encoding: .utf8) {
            for zeile in text.split(separator: "\n") {
                let t = zeile.trimmingCharacters(in: .whitespaces)
                if t.hasPrefix("PORT="),
                   let p = Int(t.dropFirst(5).trimmingCharacters(in: .whitespaces)) {
                    return p
                }
            }
            // .env gefunden, aber ohne PORT: Der Server nimmt dann auch 3000.
            return standardPort
        }
        let hoeher = ordner.deletingLastPathComponent()
        if hoeher.path == ordner.path { break }
        ordner = hoeher
    }
    return standardPort
}

let port = portLesen()
let basis = "http://127.0.0.1:\(port)"

// ---------------------------------------------------------------------------

final class Menue: NSObject, NSApplicationDelegate {

    let punkt = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menue = NSMenu()
    var takt: Timer?

    // Was zuletzt vom Server kam. Nur zum Anzeigen — nie als Wahrheit
    // gespeichert, beim nächsten Takt wird ohnehin neu gefragt.
    var laeuft = false
    var modell = ""
    var meshAn = false
    var meshGeraete = 0
    var meshGB = 0.0

    func applicationDidFinishLaunching(_: Notification) {
        punkt.button?.title = "◌"
        punkt.button?.toolTip = "Dive on Wide"
        punkt.menu = menue
        neuZeichnen()
        abfragen()
        takt = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.abfragen()
        }
    }

    // -- Fragen -------------------------------------------------------------

    func holen(_ pfad: String, _ fertig: @escaping ([String: Any]?) -> Void) {
        guard let url = URL(string: basis + pfad) else { return fertig(nil) }
        var anfrage = URLRequest(url: url)
        anfrage.timeoutInterval = 4
        URLSession.shared.dataTask(with: anfrage) { daten, _, _ in
            guard let d = daten,
                  let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any]
            else { return fertig(nil) }
            fertig(j)
        }.resume()
    }

    func senden(_ pfad: String, _ koerper: [String: Any], _ fertig: @escaping () -> Void) {
        guard let url = URL(string: basis + pfad) else { return fertig() }
        var anfrage = URLRequest(url: url)
        anfrage.httpMethod = "POST"
        anfrage.timeoutInterval = 30
        anfrage.setValue("application/json", forHTTPHeaderField: "Content-Type")
        anfrage.httpBody = try? JSONSerialization.data(withJSONObject: koerper)
        URLSession.shared.dataTask(with: anfrage) { _, _, _ in fertig() }.resume()
    }

    func abfragen() {
        holen("/api/health") { j in
            let da = j != nil
            let m = (j?["model"] as? String) ?? (j?["default_model"] as? String) ?? ""
            DispatchQueue.main.async {
                self.laeuft = da
                self.modell = m
                self.neuZeichnen()
            }
        }
        holen("/api/mesh") { j in
            DispatchQueue.main.async {
                self.meshAn = (j?["laeuft"] as? Bool) ?? false
                self.meshGeraete = (j?["geraete_gesamt"] as? Int) ?? 0
                self.meshGB = (j?["compute_gesamt_gb"] as? Double) ?? 0
                self.neuZeichnen()
            }
        }
    }

    // -- Zeichnen -----------------------------------------------------------

    func zeile(_ text: String, _ tat: Selector?, _ taste: String = "") -> NSMenuItem {
        let e = NSMenuItem(title: text, action: tat, keyEquivalent: taste)
        e.target = self
        return e
    }

    func hinweis(_ text: String) -> NSMenuItem {
        let e = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        e.isEnabled = false
        return e
    }

    func neuZeichnen() {
        // Das Symbol sagt auf einen Blick, woran man ist. Erster Entwurf
        // unterschied „Netz an" von „Netz aus" durch ◉ gegen ● — auf dem
        // Bildschirm nachgesehen waren die zwei Zeichen bei Menueleisten-
        // Groesse nicht auseinanderzuhalten. Eine Zustandsanzeige, deren
        // Zustaende gleich aussehen, ist keine. Also die Geraetezahl daneben:
        // unmissverstaendlich UND die Angabe, die man tatsaechlich wissen will.
        if !laeuft {
            punkt.button?.title = "◌"
            punkt.button?.toolTip = "Dive on Wide antwortet nicht (Port \(port))"
        } else if meshAn {
            punkt.button?.title = "● \(meshGeraete)"
            punkt.button?.toolTip = String(format: "Dive on Wide · Netz mit %d Gerät(en), %.1f GB",
                                           meshGeraete, meshGB)
        } else {
            punkt.button?.title = "●"
            punkt.button?.toolTip = "Dive on Wide läuft · Netz aus"
        }

        menue.removeAllItems()
        if laeuft {
            menue.addItem(hinweis("● Dive on Wide läuft auf Port \(port)"))
            if !modell.isEmpty { menue.addItem(hinweis("   Modell: \(modell)")) }
        } else {
            menue.addItem(hinweis("○ Kein Dive on Wide auf Port \(port)"))
            menue.addItem(hinweis("   Erst „Dive on Wide starten“ ausführen"))
        }
        menue.addItem(.separator())
        menue.addItem(zeile("Dive on Wide öffnen", #selector(oeffnen), "o"))
        menue.addItem(.separator())

        if laeuft {
            if meshAn {
                let gb = String(format: "%.1f", meshGB)
                menue.addItem(hinweis("🕸 Netz: \(meshGeraete) Gerät(e), \(gb) GB"))
                menue.addItem(zeile("Netz verlassen", #selector(meshStopp)))
            } else {
                menue.addItem(hinweis("🕸 Netz: aus"))
                // Bewusst NUR die Klause von hier aus. Die Weite ist das offene
                // Netz — wer da hineingeht, soll die Erklärung dazu gelesen
                // haben, und die steht in der Oberfläche, nicht in einem Menü
                // mit vier Wörtern Platz.
                menue.addItem(zeile("Diving Net starten (eigenes Netz)", #selector(meshStart)))
            }
            menue.addItem(.separator())
        }
        menue.addItem(zeile("Menü beenden", #selector(schliessen), "q"))
    }

    // -- Tun ----------------------------------------------------------------

    @objc func oeffnen() {
        if let u = URL(string: basis) { NSWorkspace.shared.open(u) }
    }

    @objc func meshStart() {
        senden("/api/mesh/start", ["betriebsart": "klause"]) {
            DispatchQueue.main.async { self.abfragen() }
        }
    }

    @objc func meshStopp() {
        // Ohne Rückfrage ginge hier ein laufendes Netz verloren — samt allem,
        // was nur im Arbeitsspeicher lebt: Kontakte, Chats, Fäden. Genau das
        // ist die Zusage des Systems, also darf es nicht ein Rutscher sein.
        let frage = NSAlert()
        frage.messageText = "Netz verlassen?"
        frage.informativeText = "Kontakte, Chats und Fäden leben nur im "
            + "Arbeitsspeicher und sind danach weg. Das ist die Zusage des "
            + "Systems, keine fehlende Bequemlichkeit."
        frage.addButton(withTitle: "Verlassen")
        frage.addButton(withTitle: "Abbrechen")
        NSApp.activate(ignoringOtherApps: true)
        guard frage.runModal() == .alertFirstButtonReturn else { return }
        senden("/api/mesh/stop", [:]) {
            DispatchQueue.main.async { self.abfragen() }
        }
    }

    @objc func schliessen() {
        // Beendet NUR das Menü. Dive on Wide selbst läuft weiter — alles andere
        // wäre eine Überraschung, die ein Symbol in der Ecke nicht auslösen darf.
        NSApp.terminate(nil)
    }
}

// --- Selbstpruefung ---------------------------------------------------------
// `DowOSMenu --pruefen` fragt einmal ab, schreibt hin, was im Menue staende,
// und endet. Damit laesst sich der Inhalt pruefen, ohne das Menue anzuklicken —
// und wer wissen will, warum das Symbol etwas anderes zeigt als erwartet,
// bekommt hier die Rohdaten statt einer Vermutung.
if CommandLine.arguments.contains("--pruefen") {
    let sem = DispatchSemaphore(value: 0)
    let m = Menue()
    var offen = 2
    let fertig = { if offen == 0 { sem.signal() } }
    m.holen("/api/health") { j in
        m.laeuft = j != nil
        m.modell = (j?["model"] as? String) ?? (j?["default_model"] as? String) ?? ""
        offen -= 1; fertig()
    }
    m.holen("/api/mesh") { j in
        m.meshAn = (j?["laeuft"] as? Bool) ?? false
        m.meshGeraete = (j?["geraete_gesamt"] as? Int) ?? 0
        m.meshGB = (j?["compute_gesamt_gb"] as? Double) ?? 0
        offen -= 1; fertig()
    }
    _ = sem.wait(timeout: .now() + 10)
    print("PORT: \(port)")
    print("SYMBOL: \(m.laeuft ? (m.meshAn ? "● \(m.meshGeraete)" : "●") : "◌")")
    print("LAEUFT: \(m.laeuft)")
    print("MESH: \(m.meshAn) geraete=\(m.meshGeraete) gb=\(m.meshGB)")
    print("MENUE:")
    if m.laeuft {
        print("  ● Dive on Wide läuft auf Port \(port)")
        if !m.modell.isEmpty { print("     Modell: \(m.modell)") }
    } else {
        print("  ○ Kein Dive on Wide auf Port \(port)")
    }
    print("  Dive on Wide öffnen")
    if m.laeuft {
        print(m.meshAn
              ? "  🕸 Netz: \(m.meshGeraete) Gerät(e) — Netz verlassen"
              : "  🕸 Netz: aus — Diving Net starten (eigenes Netz)")
    }
    print("  Menü beenden")
    exit(0)
}

let app = NSApplication.shared
let menue = Menue()
app.delegate = menue
app.setActivationPolicy(.accessory)   // kein Dock-Symbol, kein App-Umschalter
app.run()
