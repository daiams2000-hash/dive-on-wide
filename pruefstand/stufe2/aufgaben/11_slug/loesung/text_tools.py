import re
import unicodedata


def slug(text):
    t = text.replace("ß", "ss")
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "ae"), ("Ö", "oe"), ("Ü", "ue")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    if len(t) > 60:
        teil = t[:61]
        i = teil.rfind("-")
        t = t[:i] if i > 0 else t[:60]
        t = t.strip("-")
    return t or "n-a"
