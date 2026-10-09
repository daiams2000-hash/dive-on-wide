from datetime import date


def parse_datum(text):
    t = text.strip()
    try:
        if "-" in t:
            teile = t.split("-")
            if len(teile) != 3:
                raise ValueError(text)
            jahr, monat, tag = (int(x) for x in teile)
        else:
            teile = t.split(".")
            if len(teile) != 3:
                raise ValueError(text)
            tag, monat, jahr = (int(x) for x in teile)
            if len(teile[2]) == 2:
                jahr += 2000
        return date(jahr, monat, tag)
    except (TypeError, IndexError) as e:
        raise ValueError(text) from e
