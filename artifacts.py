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
from pathlib import Path
from typing import Any

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


async def zbierz(klient: Any, typy: list[str] | None = None) -> list[dict[str, Any]]:
    """Zwraca gotowe artefakty z rozmiarem. Nic nie generuje."""
    import httpx
    from notebooklm._auth import cookies as auth_cookies

    do = typy or list(LISTY)
    jar = auth_cookies.build_httpx_cookies_from_storage(
        Path(os.environ["NOTEBOOKLM_HOME"]) / "storage_state.json")

    wyniki: list[dict[str, Any]] = []
    async with httpx.AsyncClient(cookies=jar, timeout=180.0,
                                 follow_redirects=True) as http:
        for nazwa in do:
            metoda = LISTY.get(nazwa)
            if not metoda:
                continue
            fn = getattr(klient.artifacts, metoda, None)
            if fn is None:
                continue
            try:
                lista = await fn(os.environ["NOTEBOOK_ID"])
            except Exception as exc:  # noqa: BLE001 — jeden typ nie zatrzymuje reszty
                wyniki.append({"typ": nazwa, "blad": f"{type(exc).__name__}: {str(exc)[:80]}"})
                continue
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
                    try:
                        wpis["bajty"] = await zmierz(http, url)
                        wpis["mb"] = round(wpis["bajty"] / 1048576, 2)
                        wpis["miesci"] = wpis["bajty"] <= LIMIT_ZALACZNIKA_B
                        wpis["url"] = url
                    except Exception as exc:  # noqa: BLE001
                        wpis["blad_rozmiaru"] = f"{type(exc).__name__}: {str(exc)[:70]}"
                wyniki.append(wpis)
    return wyniki


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
