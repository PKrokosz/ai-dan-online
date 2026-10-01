"""Uruchamia bramki na serwerze i zwraca krotki, jednoznaczny wynik.

Po co to, skad `python3.11 -m unittest`:
- bot.py konfiguruje `logging` przy imporcie, wiec wyjscie zalewa
  setki linii i podsumowanie unittest wpada poza limit odpowiedzi shella.
  Wtedy widac `Ran 0 tests` albo `HTTP 200` i czlowiek wotpie "zielono",
  a bramka nie zrobila nic (tak bylo: `-s /home/srv120794/ai-dan`
  zamiast katalogu `tests/` dalo "Ran 0 tests" i exit 0).
- `test_negatywny.py` PODMIANA pliki aplikacji. Na serwerze, gdzie dziala
  bot, uruchomienie go przez `discover` to strata aplikacji. Ten skrypt
  odmawia go domyslnie.

Wynik: jedna linia `WYNIK <n> OK|PADL <bledy> <pominiete>`, exit 0/1.
"""
import logging
import sys
import unittest
from pathlib import Path

KATALOG_APKI = Path("/home/srv120794/ai-dan")
KATALOG_TESTOW = KATALOG_APKI / "tests"

# Moduly destrukcyjne: podmieniaja pliki aplikacji i odtwarzaja je w `finally`.
# Na serwerze z dzialajacym botem nie wolno im zglosic sukcesu ani awarii —
# skrypt konczy sie bledem, zamiast je uruchamiac.
ZABRONIONE = {"test_negatywny.py"}


def main(argv: list[str]) -> int:
    wzorce = argv[1:]
    if not wzorce:
        wzorce = sorted(
            p.name for p in KATALOG_TESTOW.glob("test_*.py")
            if p.name not in ZABRONIONE
        )
    if not wzorce:
        print("WYNIK 0 PADL 1 0 — brak testow do uruchomienia")
        return 1

    zle = [w for w in wzorce if w in ZABRONIONE]
    if zle:
        print(f"ZABRONIONE: {', '.join(zle)} — moduly podmieniaja pliki "
              f"aplikacji; na serwerze z dzialajacym botem nie sa bezpieczne")
        return 2

    # Wyciszenie szumu z logowania: podsumowanie ma byc widoczne, nie
    # setki linii httpx i discord.
    logging.disable(logging.CRITICAL)

    suita = unittest.TestSuite()
    for wzorzec in wzorce:
        suita.addTests(unittest.defaultTestLoader.discover(
            str(KATALOG_TESTOW), pattern=wzorzec, top_level_dir=str(KATALOG_TESTOW)))

    wynik = unittest.TextTestRunner(verbosity=0, stream=sys.stdout).run(suita)

    for test, slad in (wynik.failures + wynik.errors):
        print(f"\n--- BLAD: {test} ---")
        print(slad.strip().splitlines()[-1] if slad.strip() else "?")

    # `testsRun == 0` to nie sukces. Taki wynik mialo `-s` wskazujace
    # katalog aplikacji zamiast `tests/`: bramka milczala, a wygladala
    # na zielona.
    if wynik.testsRun == 0:
        print("WYNIK 0 PADL 1 0 — zaden test nie zostal znaleziony "
              "(wzorzec nie pasuje do plikow w katalogu testow)")
        return 1

    status = "OK" if wynik.wasSuccessful() else "PADL"
    print(f"WYNIK {wynik.testsRun} {status} "
          f"{len(wynik.failures) + len(wynik.errors)} "
          f"{len(wynik.skipped)}")
    return 0 if wynik.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
