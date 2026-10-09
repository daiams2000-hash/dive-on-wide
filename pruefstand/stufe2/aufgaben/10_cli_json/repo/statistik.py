import argparse
import sys


def auswerten(zahlen):
    return {"anzahl": len(zahlen), "summe": sum(zahlen), "mittel": sum(zahlen) / len(zahlen)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("zahlen", nargs="+", type=float)
    args = p.parse_args(argv)
    e = auswerten(args.zahlen)
    print(f"Anzahl: {e['anzahl']}")
    print(f"Summe: {e['summe']}")
    print(f"Mittel: {e['mittel']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
