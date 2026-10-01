"""Generowanie artefaktow w kolejce — logika bez Discorda.

Osobny modul, bo wszystko w `bot.py` miesza sie z wysylaniem i synchronizacja
komend, a tutaj chodzi o cos w pelni testowalnego: ktore parametry ida do
biblioteki, jak rozpoznac limit, kiedy wynik jest gotowy do wyslania.

Nazwy parametrow w `notebooklm-py` sa NIEOCZEWISTE i to jest pulapka:
`generate_audio` przyjmuje `audio_format` i `audio_length`, nie `format`
i `length`. Sprawdzone w zrodle wersji 0.7.2 zainstalowanej na serwerze:

    generate_audio(notebook_id, source_ids=None, language="en",
                   instructions=None, audio_format=None, audio_length=None)

    class AudioFormat(int, Enum):  DEEP_DIVE=1  BRIEF=2  CRITIQUE=3  DEBATE=4
    class AudioLength(int, Enum):  SHORT=1      DEFAULT=2 LONG=3

`None` oznacza "nie podawaj", a wtedy NotebookLM wybiera wlasny default —
wlasnie dlatego pierwsze proby wyszly po 30-50 MB zamiast ~4 MB.

`source_ids` pozwala wybrac, z ktorych zrodel ma powstac material; `instructions`
to dodatkowa wskazowka tresciowa.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Nazwy widziane przez uzytkownika -> (opis, wartosc enuma)
# Krotki opisy, bo Discord pokazuje je w liscie wyboru.
DLUGOSC: dict[str, tuple[str, Any]] = {
    "krotka": ("krotka (~2 min, ~4 MB, miesci sie w zalaczniku)", "SHORT"),
    "domyslna": ("domyslna (~10 min, zwykle przekracza limit)", "DEFAULT"),
    "dluga": ("dluga (~20 min, przekracza limit)", "LONG"),
}
FORMAT: dict[str, tuple[str, Any]] = {
    "brief": ("brief — jeden glos, krotko, zero dyskusji", "BRIEF"),
    "deep_dive": ("deep dive — dwa glosy, dyskusja w temacie", "DEEP_DIVE"),
    "krytyka": ("krytyka — jeden glos, ocenia material", "CRITIQUE"),
    "debata": ("debata — dwa glosy, sporne stanowiska", "DEBATE"),
}
DOMYSLNA_DLUGOSC = "krotka"
DOMYSLNY_FORMAT = "brief"
JEZYK = "pl"          # domyslnie biblioteka daje "en" — ktorego nie chcemy
TIMEOUT_GENEROWANIA_S = 1800.0   # 30 min; audio potrafi byc bardzo wolne


def _enumy() -> tuple[Any, Any]:
    """Nazwy klas z biblioteki, z importem w srodku.

    Modul ma dac sie wczytac bez biblioteki (testy kontraktu, `py_compile`),
    wiec `notebooklm` nie jest importowane na poziomie pliku.
    """
    from notebooklm import AudioFormat, AudioLength
    return AudioFormat, AudioLength


def opcje_dlugosci() -> list[tuple[str, str]]:
    return [(klucz, opis) for klucz, (opis, _) in DLUGOSC.items()]


def opcje_formatow() -> list[tuple[str, str]]:
    return [(klucz, opis) for klucz, (opis, _) in FORMAT.items()]


def opis_wyboru(dlugosc: str, fmt: str) -> str:
    return (f"{DLUGOSC.get(dlugosc, ('', ''))[0]}, "
            f"{FORMAT.get(fmt, ('', ''))[0]}")


async def wygeneruj_audio(
    klient: Any,
    notebook_id: str,
    *,
    dlugosc: str = DOMYSLNA_DLUGOSC,
    format_audio: str = DOMYSLNY_FORMAT,  # noqa: A002 — nazwa spojna z UI
    source_ids: list[str] | None = None,
    instructions: str | None = None,
    timeout: float = TIMEOUT_GENEROWANIA_S,
    zgloszenie: Any = None,
) -> dict[str, Any]:
    """Generuje audio i czeka na wynik. Zwraca raport, nie rzuca.

    Zawsze zwraca slownik z kluczem `blad` albo `status` — wyjatki z biblioteki
    sa lapane i opisane, bo wywolujacy to zadanie w tle, gdzie wyjatek
    zginalby cicho i zadanie zostalo by w stanie `w_toku` na wiecznosc.
    """
    AudioFormat, AudioLength = _enumy()
    nazwa_dlugosci = DLUGOSC.get(dlugosc, DLUGOSC[DOMYSLNA_DLUGOSC])[1]
    nazwa_formatu = FORMAT.get(format_audio, FORMAT[DOMYSLNY_FORMAT])[1]

    async def melduj(tekst: str) -> None:
        if zgloszenie is not None:
            try:
                await zgloszenie(tekst)
            except Exception:  # noqa: BLE001 — zgloszenie nie moze ubic wykonania
                pass

    t0 = asyncio.get_running_loop().time()
    try:
        await melduj("Wysyłam zlecenie do NotebookLM…")
        status = await klient.artifacts.generate_audio(
            notebook_id,
            language=JEZYK,
            audio_length=getattr(AudioLength, nazwa_dlugosci),
            audio_format=getattr(AudioFormat, nazwa_formatu),
            source_ids=source_ids,
            instructions=instructions,
        )
    except Exception as exc:  # noqa: BLE001
        return {"blad": f"{type(exc).__name__}: {exc}", "wyjatkiem": exc,
                "sekundy": asyncio.get_running_loop().time() - t0}

    # Limit dzienny przychodzi zwykle juz w zleceniu. Jesli status wymaga
    # czekania, dopiero wtedy czekamy — inaczej wstrzymalibysmy sie na
    # zadanie, ktorego Google juz odrzucil.
    from limits import czy_wymaga_oczekiwania

    if not czy_wymaga_oczekiwania(status):
        return {"status": status, "sekundy": asyncio.get_running_loop().time() - t0}

    await melduj("NotebookLM generuje. To potrafi trwać kilka minut…")
    try:
        status = await klient.artifacts.wait_for_completion(
            notebook_id, status.task_id, timeout=timeout, initial_interval=5.0
        )
    except Exception as exc:  # noqa: BLE001 — timeout to nie limit
        return {"blad": f"{type(exc).__name__}: {exc}", "wyjatkiem": exc,
                "sekundy": asyncio.get_running_loop().time() - t0}

    return {"status": status, "sekundy": asyncio.get_running_loop().time() - t0}
