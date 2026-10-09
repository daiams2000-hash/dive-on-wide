import re


def slug(titel):
    t = titel.lower()
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        t = t.replace(a, b)
    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')
