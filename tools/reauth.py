"""Reautoryzacja sesji NotebookLM — bez hasla, z weryfikacja przed podmiana.

Dlaczego to w ogole dziala: profil Chrome trzyma trwale zalogowana sesje, a
`cookies_z_profilu.py` umie ja odczytac. 30.09 `auth check --test` zwrocil
`ok` dla pliku wyciagnietego z profilu, w ktorym nikt nie wpisywal hasla.

Kolejnosc jest tu istotna i nie jest dowolna:

    1. czy obecna sesja zyje?      -> jesli tak, koniec (nie ruszamy)
    2. wyciagnij do pliku TYMCZASOWEGO
    3. zweryfikuj IZOLOWANIE       -> w osobnym katalogu, produkcja nietknieta
    4. dopiero po `ok` -> podmiana

Kolejnosc 2-3-4 chroni przed najgorszym scenariuszem tego skryptu: nadpisaniem
dobrej sesji zepsuta. Weryfikacja na produkcyjnym pliku bylaby bez sensu —
to ona ma byc bezpieczna.

Serwer NIE potrafi tego zrobic sam: nie ma przegladarki ani profilu Google.
Dostaje tylko plik, wiec automatyzacja konczy sie na tym skrypcie + wgraniu.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cookies_z_profilu as ciasteczka_mod

# plik przeznaczony do wgrania na serwer — celowo POZA katalogiem profilu,
# zeby `notebooklm login` go nie nadpisywal swoim stanem
def plik_dla_serwera() -> Path:
    return Path(os.environ["USERPROFILE"]) / ".notebooklm" / "sesja_na_serwer.json"


def znacznik_ostatniej_proby() -> Path:
    return Path(os.environ["USERPROFILE"]) / ".notebooklm" / "ostatnia_reauth.json"


@contextmanager
def katalog_roboczy(ktory: Path) -> Iterator[None]:
    """Czasowo wskazuje NOTEBOOKLM_HOME na `ktory`.

    Biblioteka czyta lokalizacje z env, wiec zmiana w procesie jedynym
    zamiast w calym systemie — inaczej testowalibysmy produkcje.
    """
    poprzedni = os.environ.get("NOTEBOOKLM_HOME")
    os.environ["NOTEBOOKLM_HOME"] = str(ktory)
    try:
        yield
    finally:
        if poprzedni is None:
            os.environ.pop("NOTEBOOKLM_HOME", None)
        else:
            os.environ["NOTEBOOKLM_HOME"] = poprzedni


def sesja_zyje(plik: Path) -> tuple[bool, str]:
    """Czy sesja zapisana w `plik` jest uzyteczna.

    Weryfikacja ZAWSZE leci w swoim katalogu tymczasowym. Dwa powody: biblioteka
    szuka pliku pod nazwa `storage_state.json`, a nasz plik celu nazywa sie
    inaczej; i — wazniejsze — plik zrodlowy nie moze byc celem testu, bo
    "czy on dziala" jest wlasnie tym, co sprawdzamy.

    Samo `from_storage()` jest bramka: biblioteka pobiera token w trakcie
    tworzenia klienta i rzuca `Authentication expired` jesli nie da rady.
    Nie trzeba dodatkowego zapytania do API.
    """
    if not plik.exists():
        return False, f"brak pliku {plik}"
    try:
        from notebooklm import NotebookLMClient
    except Exception as exc:  # noqa: BLE001
        return False, f"brak biblioteki notebooklm: {exc}"

    katalog = Path(tempfile.mkdtemp(prefix="nlm-check-"))
    try:
        shutil.copy2(plik, katalog / "storage_state.json")
        with katalog_roboczy(katalog):
            asyncio.run(NotebookLMClient.from_storage(keepalive=None))
    except Exception as exc:  # noqa: BLE001 — wyjatek == martwa sesja
        return False, f"{type(exc).__name__}: {str(exc)[:150]}"
    finally:
        shutil.rmtree(katalog, ignore_errors=True)
    return True, "token pobrany, sesja zyje"


def zapisz_znacznik(wynik: str, szczegoly: dict[str, object]) -> None:
    import json
    znacznik_roboczy = znacznik_ostatniej_proby()
    znacznik_roboczy.parent.mkdir(parents=True, exist_ok=True)
    znacznik_roboczy.write_text(json.dumps(
        {"wynik": wynik, "czas": time.strftime("%Y-%m-%d %H:%M:%S"), **szczegoly},
        indent=2, ensure_ascii=False), encoding="utf-8")


def wykonaj(wymuszaj: bool = False, tylko_sprawdz: bool = False) -> int:
    """Wlasciwa logika, bez argparse — dzieki temu testowalna.

    Kolejnosc 2-3-4 (wyciagnij -> zweryfikuj w izolacji -> podmien) jest
    zabezpieczeniem przed jednym scenariuszem: nadpisaniem dobrej sesji zepsuta.
    """
    cel = plik_dla_serwera()
    print(f"profil Chrome : {ciasteczka_mod.sciezka_chrome()}")
    print(f"cel (serwer)  : {cel}")

    # 1. czy obecna sesja jeszcze dziala?
    if not wymuszaj:
        zyje, komunikat = sesja_zyje(cel)
        print(f"\n1. obecna sesja: {'ZYJE' if zyje else 'martwa'} — {komunikat}")
        if zyje:
            zapisz_znacznik("sesja_zyje", {"cel": str(cel)})
            print("\nnie ma co robic — sesja nadaje sie do wgrania.")
            return 0
    else:
        print("\n1. pomijam sprawdzenie (--wymuszaj)")

    if tylko_sprawdz:
        zyje, _ = sesja_zyje(cel)
        return 0 if zyje else 1

    # 2. wyciagnij do pliku tymczasowego
    katalog_tymczasowy = Path(tempfile.mkdtemp(prefix="nlm-reauth-"))
    try:
        print("\n2. wyciagam ciasteczka z profilu...")
        roboczy = katalog_tymczasowy / "storage_state.json"
        raport = ciasteczka_mod.wyciagnij(roboczy)
        if "blad" in raport:
            print(f"   NIE UDALO SIE: {raport['blad']}")
            zapisz_znacznik("blad_wyciagania", {"blad": raport["blad"]})
            return 2
        if raport["brakujace"]:
            print(f"   brakujace ciasteczka: {raport['brakujace']}")
            zapisz_znacznik("brak_ciasteczek",
                            {"brakujace": raport["brakujace"]})
            return 3
        print(f"   wybrane: {raport['wyrane']} ciasteczek google")

        # 3. weryfikacja IZOLOWANA — produkcja nietknieta
        print("\n3. weryfikuje nowa sesje w izolacji...")
        zyje, komunikat = sesja_zyje(roboczy)
        print(f"   {'OK' if zyje else 'ODRZUCONA'} — {komunikat}")
        if not zyje:
            zapisz_znacznik("odrzucona", {"powod": komunikat})
            print("\nNowa sesja nie dziala — starej nie ruszam. Koniec.")
            return 4

        # 4. podmiana dopiero teraz
        cel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(roboczy, cel)
        print(f"\n4. podmieniono: {cel} ({cel.stat().st_size} B)")
        zapisz_znacznik("ok", {"cel": str(cel), "wyrane": raport["wyrane"]})
        print(f"\nGotowe. Wgraj na serwer:\n"
              f"  {cel}\n  ->  /home/srv120794/ai-dan/nlm-home/storage_state.json")
        return 0
    finally:
        shutil.rmtree(katalog_tymczasowy, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reautoryzacja sesji NotebookLM z profilu Chrome (bez hasla)")
    parser.add_argument("--wymuszaj", action="store_true",
                        help="odwiez nawet jesci obecna sesja wciaz zyje")
    parser.add_argument("--tylko-sprawdz", action="store_true",
                        help="niczego nie podmieniaj, tylko zgloz stan")
    args = parser.parse_args()
    return wykonaj(wymuszaj=args.wymuszaj, tylko_sprawdz=args.tylko_sprawdz)


if __name__ == "__main__":
    sys.exit(main())
