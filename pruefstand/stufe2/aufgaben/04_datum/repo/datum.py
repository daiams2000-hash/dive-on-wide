from datetime import date


def parse_datum(text):
    tag, monat, jahr = text.split(".")
    return date(int(jahr), int(monat), int(tag))
