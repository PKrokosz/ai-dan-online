"""Jednorazowe nadrobienie zapisu, ktory zniknal przez blad w workerze.

01.10: pierwsze zlecenie `/audio` wygenerowalo plik, ale worker wywalil sie
na `NameError` PRZED zapisem w `limits.json`, a zadanie dostalo stan
`gotowe` mimo niewyslanego pliku. Dwa skutki w danych:

- licznik pokazywal `audio 2 ok`, chociaz zrodlo zgłosilo trzeci sukces —
  przy niedoszacowanym liczniku mozna przeroczyc dzienny limit;
- zadanie mialo stan `gotowe` bez informacji, ze nic nie dotarlo.

To narzedzie NIE generuje ponownie (to tlumiłoby limit), tylko opisuje to,
co naprawde sie stalo. Domyslnie `--suchy`: wypisuje co zrobi, nie zmienia
niczego.

Stan `gotowe` zostaje swiadomie: artefakt na serwerze Google istnieje i
uzytkownik pobierze go przez `/pobierz`, wiec regenerowanie tylko marnowaloby
limit. Stanowi `dostarczone: false` wraz z powodem — czyli zadanie mowi
prawde o tym, czego NIE zrobilo.

Uzycie:
  python3.11 tools/nadrob_zapis.py --typ audio --zadanie z1790838111967-ccbb21
  python3.11 tools/nadrob_zapis.py --typ audio --zadanie z... --zapisz
"""
import argparse
import json
import sys
from pathlib import Path

KATALOG_APKI = Path("/home/srv120794/ai-dan")
DOMY = KATALOG_APKI
sys.path.insert(0, str(DOMY))

POWOD = ("worker wywalil sie na NameError (artifacts niezaimportowane) "
         "przed wyslaniem — plik wygenerowany, ale nie dostarczony")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--typ", default="audio")
    parser.add_argument("--zadanie", required=True, help="id zadania z kolejki")
    parser.add_argument("--zapisz", action="store_true",
                        help="faktycznie popraw dane (domyslnie suchy przebieg)")
    args = parser.parse_args(argv[1:])

    import kolejka
    from limits import LicznikLimitow

    # `wczytaj()` zwraca caly stan, czyli slownik z kluczem "zadania".
    # Szukanie zadania wprost na jego korzeniu daloby "nie ma zadania"
    # dla zadania, ktore istnieje.
    stan_kolejki = kolejka.wczytaj()
    zadanie = stan_kolejki.get("zadania", {}).get(args.zadanie)
    if zadanie is None:
        print(f"nie ma zadania {args.zadanie} w kolejce "
              f"(znane: {', '.join(sorted(stan_kolejki.get('zadania', {}))) or 'brak'})")
        return 1

    licznik = LicznikLimitow(KATALOG_APKI / "limits.json")
    przed = licznik.raport(args.typ)
    print(f"przed:  sukcesy={przed['sukcesy']} limity={przed['limity']} "
          f"od_ostatniego_limitu={przed['sukcesy_od_ostatniego_limitu']}")
    print(f"zadanie: stan={zadanie.get('stan')} wynik={zadanie.get('wynik')}")

    if not args.zapisz:
        print("\n--suchy: nic nie zmieniono (dodaj --zapisz)")
        return 0

    if zadanie.get("wynik", {}).get("doliczone"):
        print("zadanie ma juz znacznik `doliczone` — nic nie robie")
        return 0

    licznik.rejestruj_sukces(args.typ)
    kolejka.oznacz(args.zadanie, zadanie.get("stan", "gotowe"), {
        **(zadanie.get("wynik") or {}),
        "dostarczone": False,
        "powod": POWOD,
        "doliczone": True,
    })
    po = licznik.raport(args.typ)
    print(f"po:     sukcesy={po['sukcesy']} limity={po['limity']} "
          f"od_ostatniego_limitu={po['sukcesy_od_ostatniego_limitu']}")

    # Kontrola: plik musi pozostac zwalniawalny, inaczej drugi przebieg
    # dodalby kolejny sukces do licznika.
    with open(KATALOG_APKI / "zadania.json", encoding="utf-8") as f:
        po_stanie = json.load(f)["zadania"][args.zadanie]
    if not (po_stanie.get("wynik") or {}).get("doliczone"):
        print("BLAD: zapis nie zostal oznaczony — powtorzylbym zapis w liczniku")
        return 1
    print("OK — zapis doliczony, zadanie oznaczone jako niedostarczone")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
