import argparse
import json
import sys


def auswerten(zahlen):
    return {"anzahl": len(zahlen), "summe": sum(zahlen), "mittel": sum(zahlen) / len(zahlen),
            "min": min(zahlen), "max": max(zahlen)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("zahlen", nargs="+", type=float)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    e = auswerten(args.zahlen)
    if args.json:
        print(json.dumps(e))
        return 0
    print(f"Anzahl: {e['anzahl']}")
    print(f"Summe: {e['summe']}")
    print(f"Mittel: {e['mittel']}")
    print(f"Min: {e['min']}")
    print(f"Max: {e['max']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
