import csv
import io


def lese_kunden(text):
    zeilen = [z for z in csv.reader(io.StringIO(text.replace("\r\n", "\n"))) if any(f.strip() for f in z)]
    if not zeilen:
        return []
    kopf = zeilen[0]
    return [dict(zip(kopf, z)) for z in zeilen[1:]]
