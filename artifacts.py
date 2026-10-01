"""Artefakty NotebookLM: odczyt, pomiar rozmiaru, bez generowania.

Osobny moduł, bo `/artefakty` i `/pobierz` NIE zjadają limitu dziennego —
to operacje na juz istniejacym pliku. Generowanie jest w kolejce.py.

Limity Discorda (potwierdzone 30.09):
  10 MiB na złącznik — dotyczy takze Create Message i Edit Message
  discord.py przytnie za duży plik cicho, wiec rozmiar mierzymy PRZED wysłaniem
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

# pamiec podreczna listy artefaktow: klucz -> {"wpisy": [...], "wiek": monotonic}
# lista zmienia sie tylko po wygenerowaniu czegos, wiec krotki TTL wystarczy
_PAMIAT: dict[str, dict[str, Any]] = {}

# Nazwy typow takie, jakich uzytkownik widzi w komendach.
LISTY: dict[str, str] = {
    "audio": "list_audio",
    "video": "list_video",
    "infografika": "list_infographics",
    "slajdy": "list_slide_decks",
    "raport": "list_reports",
    "quiz": "list_quizzes",
    "fiszki": "list_flashcards",
    "tabela": "list_data_tables",
    "mapa": "list_mind_maps",
}
# Nazwy biblioteki -> nazwy uzytkownika (odwrotnie do LISTY)
TYPO_BIBLIOTEKI = {v.replace("list_", "", 1): k for k, v in LISTY.items()}

# Rozszerzenie pliku wynikowego. Bez tego zalacznik dostawal nazwe `.mp3`
# niezaleznie od zawartosci, wiec infografika (PNG) szla jako `xxxx.mp3`.
# Nazwa pliku steruje tym, jak Discord pokazuje odtwarzanie i MIME przy
# podgladzie — `ready to listen` dziala tylko dla wlasciwego typu.
ROZSZERZENIA: dict[str, str] = {
    "audio": "mp3",
    "video": "mp4",
    "infografika": "png",
    "slajdy": "pdf",
    "raport": "md",
    "quiz": "json",
    "fiszki": "json",
    "tabela": "csv",
    "mapa": "json",
}
DOMYSLNE_ROZSZERZENIE = "bin"

# Limity Discorda dla podpowiedzi w autocomplete: 25 wyborow, 100 znakow
# etykiety. Tytuly bywaja dluzsze, wiec skracamy na granicy slowa.
LIMIT_PODPOWIEDZI = 25
LIMIT_ETYKIETY = 100


def etykieta_autocomplete(w: dict[str, Any]) -> str:
    """Nazwa do podpowiedzi: tytul + typ, przyciety do limitu Discorda.

    Przycinamy na ostatnim spacji przed granica, zeby nie urwac slowa w
    polowie — `Płacili za bycie więźniem w Gothi` czyta sie gorzej niz
    `Płacili za byteen więźniem…`.
    """
    tytul = str(w.get("tytul", "?")).strip() or "bez tytulu"
    typ = str(w.get("typ", "?"))
    pelny = f"{tytul} ({typ})"
    if len(pelny) <= LIMIT_ETYKIETY:
        return pelny
    # zostaw miejsce na " (typ)" — inaczej typ zostalby uciety
    budzet = LIMIT_ETYKIETY - len(typ) - 3
    skrocony = tytul[:max(10, budzet)]
    if " " in skrocony:
        skrocony = skrocony[:skrocony.rfind(" ")]
    return f"{skrocony}… ({typ})"


def podpowiedzi(wpisy: list[dict[str, Any]], max: int = LIMIT_PODPOWIEDZI) -> list[dict[str, str]]:
    """Zamienia wpisy na wyborz Discorda. Bez pomiaru rozmiaru.

    Bez `max` Discord obcina po cichu do 25, a uzytkownik widzi wtedy liste,
    ktora wyglada jakby byla kompletna — a nie jest.
    """
    wybor: list[dict[str, str]] = []
    for w in wpisy:
        if "blad" in w or not w.get("url"):
            continue
        if len(wybor) >= max:
            break
        wybor.append({"name": etykieta_autocomplete(w), "value": str(w["id"])})
    return wybor

STAN = {0: "?", 1: "OCZEKUJE", 2: "W TOKU", 3: "GOTOWY",
        4: "NIEUDANE", 5: "USUNIĘTY"}

LIMIT_ZALACZNIKA_B = 10 * 1024 * 1024      # 10 MiB
LIMIT_PLIKU_B = 500 * 1024 * 1024           # sensowny sufit na sciezce tymczasowej


def identyfikator(a: Any) -> str:
    return str(getattr(a, "artifact_id", None) or getattr(a, "id", "") or "")


def rozszerzenie(typ: str) -> str:
    """Rozszerzenie pliku dla typu widzianego przez uzytkownika.

    Nieznany typ daje `bin` zamiast `mp3` — lepiej neutralna nazwa niz
    mylace rozszerzenie, pod ktorym nie ma takiej zawartosci.
    """
    return ROZSZERZENIA.get(str(typ).strip().lower(), DOMYSLNE_ROZSZERZENIE)


def url_pliku(a: Any) -> str | None:
    """URL jest w kazdym z wariantow artefaktu — sprawdzamy po kolei."""
    for pole in ("url", "media_url", "audio_url", "video_url", "download_url"):
        v = getattr(a, pole, None)
        if v:
            return str(v)
    return None


def jest_gotowy(a: Any) -> bool:
    return str(getattr(a, "status", "")) == "3"


async def zmierz(http: Any, url: str) -> int:
    """Rozmiar w bajtach. `content-length` bywa nieobecne — wtedy liczymy."""
    naglowek = await http.head(url)
    dlugosc = naglowek.headers.get("content-length")
    if dlugosc and dlugosc.isdigit():
        return int(dlugosc)
    n = 0
    async with http.stream("GET", url) as s:
        async for kawalek in s.aiter_bytes():
            n += len(kawalek)
    return n


async def _jeden_typ(klient: Any, http: Any, nazwa: str, metoda: str,
                     mierz: bool) -> list[dict[str, Any]]:
    """Listing jednego typu. Wyjatkiem nie przerywamy reszty typow."""
    fn = getattr(klient.artifacts, metoda, None)
    if fn is None:
        return []
    try:
        lista = await fn(os.environ["NOTEBOOK_ID"])
    except Exception as exc:  # noqa: BLE001 — jeden typ nie zatrzymuje reszty
        return [{"typ": nazwa, "blad": f"{type(exc).__name__}: {str(exc)[:80]}"}]
    wyniki: list[dict[str, Any]] = []
    for a in lista:
        if not jest_gotowy(a):
            continue
        url = url_pliku(a)
        wpis: dict[str, Any] = {
            "typ": nazwa,
            "id": identyfikator(a),
            "tytul": str(getattr(a, "title", "?"))[:70],
            "utworzono": str(getattr(a, "created_at", "")),
        }
        if url:
            wpis["url"] = url
            if mierz:
                try:
                    wpis["bajty"] = await zmierz(http, url)
                    wpis["mb"] = round(wpis["bajty"] / 1048576, 2)
                    wpis["miesci"] = wpis["bajty"] <= LIMIT_ZALACZNIKA_B
                except Exception as exc:  # noqa: BLE001
                    wpis["blad_rozmiaru"] = f"{type(exc).__name__}: {str(exc)[:70]}"
        wyniki.append(wpis)
    return wyniki


async def zbierz(klient: Any, typy: list[str] | None = None,
                 mierz: bool = True, ttl: float = 0.0) -> list[dict[str, Any]]:
    """Zwraca gotowe artefakty. Nic nie generuje.

    `ttl > 0` wlacza pamiec podreczna. Zmierzone 01.10: pelne pobranie trwa
    4,2-4,4 s, a limit autocomplete Discorda to 3 s — czyli lista wyboru nigdy
    sie nie zdazyła. Dwa powody, oba naprawione:

    1. typy sa pobierane WSPOLNIE (`asyncio.gather`), a nie po kolei — 9
       sekwencyjnych RPC-i zamienia sie w jeden czas round-tripu
    2. `ttl` trzyma wynik w pamieci, bo lista zmienia sie tylko wtedy, gdy
       ktos cos wygeneruje, czyli rzadko

    `mierz=False` pomija rozmiary. To nie optymalizacja, tylko wymóg:
    autocomplete odpala sie przy kazdym nacisnietym klawiszu i nie musi
    pobierac kazdego pliku z serwera Google.
    """
    import asyncio
    import httpx
    from notebooklm._auth import cookies as auth_cookies

    do = typy or list(LISTY)
    klucz = ("mierz" if mierz else "szybko") + ":" + ",".join(do)
    if ttl > 0:
        wpam = _PAMIAT.get(klucz)
        if wpam is not None and (time.monotonic() - wpam["wiek"]) < ttl:
            return list(wpam["wpisy"])

    jar = auth_cookies.build_httpx_cookies_from_storage(
        Path(os.environ["NOTEBOOKLM_HOME"]) / "storage_state.json")
    async with httpx.AsyncClient(cookies=jar, timeout=180.0,
                                 follow_redirects=True) as http:
        partie = [_jeden_typ(klient, http, nazwa, LISTY[nazwa], mierz)
                  for nazwa in do if nazwa in LISTY]
        wyniki = [w for partia in await asyncio.gather(*partie) for w in partia]

    if ttl > 0:
        _PAMIAT[klucz] = {"wpisy": wyniki, "wiek": time.monotonic()}
    return list(wyniki)


def wyczysc_cache() -> None:
    """Po wygenerowaniu artefaktu — inaczej nowy nie pojawilby sie w liscie
    az do wyparcia wpisu z pamieci."""
    _PAMIAT.clear()


def opis(w: dict[str, Any]) -> str:
    """Jedna linia na artefakt — do komendy /artefakty."""
    if "blad" in w:
        return f"**{w['typ']}** — nie udało się odczytać: {w['blad']}"
    rozmiar = f"{w['mb']:.2f} MB" if "mb" in w else "rozmiar nieznany"
    if w.get("miesci") is True:
        rozmiar += " ✅"
    elif w.get("miesci") is False:
        rozmiar += " ⚠️ przekracza limit Discorda"
    return f"`{w['id'][:8]}` **{w['tytul']}**\n{rozmiar} · {w['utworzono']}"
