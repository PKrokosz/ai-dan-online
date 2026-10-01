"""Dzienny przebieg: odwiez sesje, a potem wgraj ja na serwer.

Dwa kroki, bo bez drugiego pierwszy nic dla bota nie znaczy:
1. `reauth.py`        — wyciaga swieza sesje z profilu Chrome, weryfikuje
                         w izolacji i podmienia plik lokalny
2. `wgraj_sesje.py`   — wysyla plik na serwer (FTP), robi `chmod 600` i
                         restartuje bota, TYLKO gdy plik sie zmienil

Osobne procesy, nie jeden skrypt z logika, bo kody wyjscia mowia wprost co
zawiodlo: reauth zwraca 0 gdy zdrowa sesja nie wymaga odwiezenia, wiec krok 2
i tak leci, ale nic nie wysyla i nie restartuje.

`reauth.py` ma wlasny log (`~/.notebooklm/reauth.log`), ten skrypt dokladnie go
wypisuje, wiec zadanie harmonogramu nie musi nic przekierowywac — `2>` w
`schtasks /TR` konczyl sie bledem, bo `&` i `>` trafiaja do polecenia.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = Path(os.environ.get("USERPROFILE", str(Path.home()))) / \
    ".notebooklm" / "reauth.log"

# Wyjscie dzieci jest w UTF-8, a konsola Windows uzywa cp1250. Bez tego
# `print()` pada na UnicodeEncodeError przy pierwszym polskim znaku spoza
# cp1250 — czyli zadanie harmonogramu konczyloby sie wyjatkiem zamiast kodem.
# `errors="replace"` zamienia tylko to, czego konsola nie umie, w miejsce
# niesmiertelnych znakow; sam plik logu dostaje pelne UTF-8.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def uruchom(nazwa: str) -> int:
    """Uruchom podproces i zapisz jego wyjscie do tego samego logu.

    `reauth.py` ma wlasny log, ale `wgraj_sesje.py` pisze na stdout, a zadanie
    harmonogramu nie ma konsoli — wiec najwazniejszy krok (wgranie na serwer)
    zostawal bez sladu. Przechwycenie tutaj daje jeden czytelny dziennik.
    """
    print(f"\n--- {nazwa} ---", flush=True)
    with open(LOG, "a", encoding="utf-8") as dziennik:
        dziennik.write(f"\n>>> {nazwa}\n")
        dziennik.flush()
        r = subprocess.run(
            [sys.executable, str(HERE / nazwa)],
            cwd=str(HERE.parent),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        for linia in r.stdout.decode("utf-8", "replace").splitlines():
            print(linia, flush=True)
            dziennik.write(linia + "\n")
        dziennik.flush()
    return r.returncode


def main() -> int:
    print("=== dzienny przebieg reautoryzacji ===", flush=True)
    kod_reauth = uruchom("reauth.py")
    kod_wgraj = uruchom("wgraj_sesje.py")

    print("\n=== podsumowanie ===")
    print(f"reauth:        {kod_reauth}")
    print(f"wgraj_sesje:   {kod_wgraj}")
    if kod_reauth == 0 and kod_wgraj == 0:
        print("OK — sesja na serwerze aktualna")
        return 0
    print("UWAGA — sprawdz powyzsze kody; 429 znaczy limit API, nie awarie sesji")
    return kod_reauth or kod_wgraj


if __name__ == "__main__":
    sys.exit(main())
