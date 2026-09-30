#!/usr/bin/env python3
"""Zasilenie i odczyt licznika limitow.

Dwa tryby:

  --zasil        wczytuje istniejace artefakty z notatnika jako sukcesy
                  (koszt: zero generowania, korzysta z Artifact.created_at)
  --odczyt       wypisuje raport licznika
  --probe TYP    probuje jedno generowanie i zapisuje wynik (sukces lub limit)

Zasilenie jest bezplatne, ale nie wystarczy do wyznaczenia okna: potrzeba
pary limit+sukces. Zasilenie daje polowę danych za darmo.
"""
import argparse
import asyncio
import os
import sys
from datetime import timezone
from pathlib import Path

KATALOG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KATALOG))

os.environ.setdefault("NOTEBOOKLM_HOME", "/home/srv120794/ai-dan/nlm-home")
os.environ.pop("NOTEBOOKLM_AUTH_JSON", None)

from limits import LicznikLimitow  # noqa: E402

NOTEBOOK_ID = os.environ.get("NOTEBOOK_ID", "65f678e6-086c-43bf-a14a-2471286c35c0")
LICZNIK_PLIK = Path(os.environ.get("LIMITS_FILE", str(KATALOG / "limits.json")))

# nazwa metody list_* -> typ w liczniku
LISTY = {
    "list_audio": "audio",
    "list_video": "video",
    "list_cinematic_videos": "cinematic_video",
    "list_reports": "report",
    "list_quizzes": "quiz",
    "list_flashcards": "flashcards",
    "list_infographics": "infographic",
    "list_slide_decks": "slide_deck",
    "list_data_tables": "data_table",
    "list_mind_maps": "mind_map",
}


def epoch(znacznik) -> float | None:
    """created_at bywa datetime albo None; czas trzymamy w UTC."""
    if znacznik is None:
        return None
    if hasattr(znacznik, "timestamp"):
        if znacznik.tzinfo is None:
            znacznik = znacznik.replace(tzinfo=timezone.utc)
        return znacznik.timestamp()
    return None


async def zasil(licznik: LicznikLimitow) -> int:
    from notebooklm import NotebookLMClient

    klient = await NotebookLMClient.from_storage(keepalive=None).__aenter__()
    dodane = 0
    try:
        # dedupe po (typ, sekunda) we WSZYSTKICH typach — inaczej ponowne
        # zasilenie dubluje zdarzenia, a licznik nie ma prawa klamac
        znane = {(typ, round(w.czas)) for typ in {t for t in LISTY.values()}
                 for w in licznik.zdarzenia(typ)}
        for metoda, typ in LISTY.items():
            fn = getattr(klient.artifacts, metoda, None)
            if fn is None:
                continue
            try:
                lista = await fn(NOTEBOOK_ID)
            except Exception as exc:  # noqa: BLE001
                print(f"  {typ}: nie odczytano ({type(exc).__name__})")
                continue
            for a in lista or []:
                ts = epoch(getattr(a, "created_at", None))
                if ts is None:
                    print(f"  {typ}: {getattr(a, 'title', '?')[:40]} — brak created_at, pominiety")
                    continue
                if (typ, round(ts)) in znane:
                    continue
                licznik.rejestruj_sukces(typ, czas=ts)
                dodane += 1
                tytul = (getattr(a, "title", None) or "")[:50]
                print(f"  {typ}: {tytul} @ {licznik.zdarzenia(typ)[-1].iso()}")
    finally:
        await klient.__aexit__(None, None, None)
    print(f"\nzasilono zdarzeniami: {dodane}")
    return 0


async def probe(licznik: LicznikLimitow, typ: str) -> int:
    """Jedna proba generowania. Zapisuje sukces albo limit — bez zgadywania."""
    from notebooklm import NotebookLMClient

    klient = await NotebookLMClient.from_storage(keepalive=None, chat_timeout=120.0).__aenter__()
    try:
        mapa = {
            "audio": klient.artifacts.generate_audio,
            "video": klient.artifacts.generate_video,
            "cinematic_video": klient.artifacts.generate_cinematic_video,
            "infographic": klient.artifacts.generate_infographic,
            "slide_deck": klient.artifacts.generate_slide_deck,
            "report": klient.artifacts.generate_report,
            "quiz": klient.artifacts.generate_quiz,
            "flashcards": klient.artifacts.generate_flashcards,
            "data_table": klient.artifacts.generate_data_table,
            "mind_map": klient.artifacts.generate_mind_map,
        }
        if typ == "audio":
            # jezyk musi byc podany recznie — domyslnie biblioteka daje 'en'
            status = await klient.artifacts.generate_audio(NOTEBOOK_ID, language="pl")
        elif typ in mapa:
            status = await mapa[typ](NOTEBOOK_ID)
        else:
            print(f"nieznany typ: {typ}")
            return 2

        print(f"task_id={status.task_id} status={status.status} "
              f"error_code={status.error_code} error={status.error}")

        if status.status == "completed":
            await klient.artifacts.wait_for_completion(
                NOTEBOOK_ID, status.task_id, timeout=1500.0, initial_interval=3.0
            )
            licznik.rejestruj_sukces(typ)
            print("zapisano: sukces")
            return 0
        if licznik.rejestruj_odmowe(typ, status):
            print("zapisano: LIMIT KWOTY")
            return 3
        licznik.rejestruj_odmowe(typ, status)
        print("zapisano: awaria (nie limit)")
        return 1
    finally:
        await klient.__aexit__(None, None, None)


async def sprawdz_sesje(licznik: LicznikLimitow) -> int:
    """Próba budowy klienta. Zapisuje wygasanie sesji, jesli nastapilo.

    Osobne od `--zasil` i `--probe`, bo wygasanie sesji pada **wczesniej**,
    niz powstaje jakiekolwiek zadanie: wywala `from_storage()`. Narzędzie
    do liczenia limitow nie zobaczy tego przez `generate_*`.
    """
    from notebooklm import NotebookLMClient

    try:
        klient = await NotebookLMClient.from_storage(keepalive=None).__aenter__()
    except Exception as exc:  # noqa: BLE001
        if licznik.rejestruj_wygasniecie_sesji(exc):
            print("SESJA WYGASLA — zapisano w liczniku")
            print(f"  {type(exc).__name__}: {str(exc)[:200]}")
            return 3
        print(f"BLAD BUDOWY (to nie wygasniecie sesji): {type(exc).__name__}: {str(exc)[:200]}")
        return 1

    try:
        print("sesja zyje — klient zbudowany")
        r = licznik.raport_sesji()
        print(f"  wygasania odnotowane: {r['wygasniecia']}")
        return 0
    finally:
        await klient.__aexit__(None, None, None)


def odczyt(licznik: LicznikLimitow) -> int:
    print(f"licznik: {LICZNIK_PLIK}")
    print(f"{'typ':<18}{'ok':>5}{'limity':>8}{'awarie':>9}{'ok od lim':>11}"
          f"{'okno<=h':>10}  {'znane':<7}")
    print("-" * 76)
    for r in licznik.wszystkie_raporty():
        print(f"{r['typ']:<18}{r['sukcesy']:>5}{r['limity']:>8}{r['awarie_inne']:>9}"
              f"{r['sukcesy_od_ostatniego_limitu']:>11}"
              f"{str(r['okno_resetu_godziny_max'] or '-'):>10}  {'tak' if r['znasz_juz_okno'] else 'nie':<7}")
    znane = [r for r in licznik.wszystkie_raporty() if r["znasz_juz_okno"]]
    sesja = licznik.raport_sesji()
    print()
    print(f"sesja Google: wygasniecia={sesja['wygasniecia']}"
          f" ostatnie={sesja['ostatnie'] or '-'}"
          f" zycie={sesja['zycie_godziny'] if sesja['zycie_godziny'] is not None else '-'}")
    if znane:
        print("Okna resetu (gorne granice, nie zgadywki):")
        for r in znane:
            print(f"  {r['typ']}: <= {r['okno_resetu_godziny_max']} h")
    else:
        print("Zadne okno nie jest jeszcze wyznaczone — brak pary limit+sukces.")
        print("Potrzeba co najmniej: jednej odmowy i jednego sukcesu PO niej.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zasil", action="store_true")
    parser.add_argument("--odczyt", action="store_true")
    parser.add_argument("--sprawdz", action="store_true", help="czy sesja Google zyje")
    parser.add_argument("--probe", metavar="TYP")
    args = parser.parse_args()

    licznik = LicznikLimitow(LICZNIK_PLIK)
    if not any([args.zasil, args.odczyt, args.sprawdz, args.probe]):
        return odczyt(licznik)
    if args.sprawdz:
        kod = asyncio.run(sprawdz_sesje(licznik))
        odczyt(licznik)
        return kod
    if args.zasil:
        kod = asyncio.run(zasil(licznik))
        if kod:
            return kod
        return odczyt(licznik)
    if args.probe:
        return asyncio.run(probe(licznik, args.probe))
    return odczyt(licznik)


if __name__ == "__main__":
    sys.exit(main())