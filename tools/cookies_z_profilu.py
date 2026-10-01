"""Wyciaga ciasteczka Google z profilu logowania NotebookLM — bez hasla.

Dlaczego to dziala bez logowania: profil `browser_profile` trzyma TRWALE
zalogowana sesje. Po 30.09 potwierdzone dwa razy — `auth check --test` zwracal
`ok` dla pliku wyciagnietego z profilu, w ktorym nikt nie wpisywal hasla.
Wystarczy, ze Chrome jest zalogowane do Google; haslo i 2FA nie sa potrzebne.

Kopia profilu NIE dziala: klucz szyfrowania v20 jest zwiazany z katalogiem
uzytkownika Windows, nie z profilem, wiec Chrome skasowal kope. Ten katalog
jest wlasnoscia narzedzia, nie Twojej przegladarki — mozna go otworzyc
w oryginale, co ten skrypt robi.

NIGDY nie wypisuje wartosci ciasteczek — tylko nazwy i liczby.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

# wymagane przez biblioteke do pobrania tokenu; bez nich `auth check` nie ma
# czego uzyc, a brak ktoregokolwiek konczy sie bledem
WYMAGANE = ["SID", "__Secure-1PSIDTS", "HSID", "SSID", "APISID", "SAPISID"]

DOMENY_OK = (
    ".google.com", ".google.pl", "accounts.google.com", "myaccount.google.com",
    "notebooklm.google.com", "notebook.google.com", "ogs.google.com",
    "gds.google.com", ".googleusercontent.com",
)


def katalog_profilu() -> Path:
    """Katalog profilu (`…/profiles/default`). Nadpisalny przez NOTEBOOKLM_HOME."""
    baza = os.environ.get("NOTEBOOKLM_HOME")
    if baza:
        return Path(baza)
    return Path(os.environ["USERPROFILE"]) / ".notebooklm" / "profiles" / "default"


def sciezka_chrome() -> Path:
    return katalog_profilu() / "browser_profile"


def stan_bazy() -> tuple[int, int]:
    """(wszystkie ciasteczka, te z google). -1/-2 gdy baza nieczytelna."""
    baza = sciezka_chrome() / "Default" / "Network" / "Cookies"
    if not baza.exists():
        return -1, -1
    try:
        pol = sqlite3.connect(f"file:{baza}?mode=ro", uri=True)
        try:
            return (pol.execute("SELECT COUNT(*) FROM cookies").fetchone()[0],
                    pol.execute(
                        "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%google%'"
                    ).fetchone()[0])
        finally:
            pol.close()
    except sqlite3.Error:
        return -2, -2


def pasuje(domena: str) -> bool:
    return any(domena.endswith(d) or domena == d.lstrip(".") for d in DOMENY_OK)


def odczytaj_ciasteczka(headless: bool) -> list[dict[str, Any]]:
    """Otwiera profil w Chrome i zwraca odszyfrowane ciasteczka.

    Tylko Playwright potrafi odszyfrować v20 — czytanie bazy sqlite daje same
    zaszyfrowane ciagi, a z nich nie da się zbudować `storage_state.json`.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        kontekst = p.chromium.launch_persistent_context(
            user_data_dir=str(sciezka_chrome()),
            channel="chrome",
            headless=headless,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        try:
            return kontekst.cookies()
        finally:
            kontekst.close()


def zapisz(cel: Path, ciasteczka: list[dict[str, Any]]) -> dict[str, Any]:
    """Zapisuje `storage_state.json` i zwraca raport (bez wartosci ciasteczek)."""
    wybrane = [
        {k: c[k] for k in ("name", "value", "domain", "path", "expires",
                           "httpOnly", "secure", "sameSite") if k in c}
        for c in ciasteczka if pasuje(c.get("domain", ""))
    ]
    nazwy = {c["name"] for c in wybrane}
    cel.parent.mkdir(parents=True, exist_ok=True)
    cel.write_text(json.dumps({"cookies": wybrane, "origins": []}, indent=2),
                   encoding="utf-8")
    return {
        "sciezka": str(cel),
        "bajty": cel.stat().st_size,
        "wyrane": len(wybrane),
        "brakujace": [w for w in WYMAGANE if w not in nazwy],
    }


def wyciagnij(cel: Path, *, cicho: bool = False) -> dict[str, Any]:
    """Pelny przebieg: odczyt -> zapis. Zwraca raport albo `blad`."""
    if not sciezka_chrome().exists():
        return {"blad": f"brak profilu Chrome: {sciezka_chrome()}"}

    ostatni: Exception | None = None
    for headless in (True, False):
        try:
            if not cicho:
                print(f"  prob Chrome headless={headless}...")
            ciasteczka = odczytaj_ciasteczka(headless)
            if ciasteczka:
                raport = zapisz(cel, ciasteczka)
                raport["headless"] = headless
                return raport
            ostatni = RuntimeError("Chrome zwrocil 0 ciasteczek")
        except Exception as exc:  # noqa: BLE001 — probujemy obu trybow
            ostatni = exc
            if not cicho:
                print(f"  nie udalo sie: {exc}")
    return {"blad": f"{type(ostatni).__name__}: {ostatni}"}


def main() -> int:
    cel = katalog_profilu() / "storage_state.json.wyciagniete"
    print(f"profil: {sciezka_chrome()}")
    print(f"stan bazy: wszystkich={stan_bazy()[0]} google={stan_bazy()[1]}")
    raport = wyciagnij(cel)
    if "blad" in raport:
        print(f"\nNIE UDALO SIE: {raport['blad']}")
        return 2
    print(f"\nwyrano ciasteczek google: {raport['wyrane']}")
    print(f"zapisano: {raport['sciezka']} ({raport['bajty']} B)")
    if raport["brakujace"]:
        print(f"BRAKUJE: {raport['brakujace']}")
        return 3
    print("plik gotowy do weryfikacji `auth check --test`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
