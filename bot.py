#!/usr/bin/env python3
"""ai-dan — bot Discord, który odpowiada z notatnika NotebookLM.

Wersja 2. Przepisana pod REALNY kontrakt notebooklm-py 0.7.2, po tym jak
bot.py v1 okazał się nieuruchamialny. W v1 były cztery błędy, każdy z osobna
uniemożliwiał działanie:

  1. `await client.chat(notebook_id=..., message=..., timeout=...)` —
     `client.chat` to namespace `ChatAPI`, NIE funkcja. W rzeczywistości:
     `await client.chat.ask(notebook_id, question, conversation_id=None)`.
  2. `message=` i `timeout=` nie istnieją w sygnaturze; timeout żyje na
     kliencie jako `chat_timeout`.
  3. Odpowiedź ma `answer` i `references[ChatReference]`; v1 szukał
     `cited_sources` i `src.title`, których w typach nie ma.
  4. `NotebookLMClient.from_storage()` to context manager — v1 trzymał go
     w zmiennej globalnej i nigdy nie wchodził w `async with`, więc klient
     nigdy nie był zbudowany ani zamknięty.

Kontrakt pilnuje `test_bot.py` (bramka bez sieci i bez logowania).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import unicodedata

import generowanie  # uzywany w dekoratorach @app_commands.choices, czyli przy imporcie

import discord
from discord import app_commands, Intents
from notebooklm import (
    AuthError,
    ChatError,
    NetworkError,
    NotebookLMClient,
    NotebookNotFoundError,
    RateLimitError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------

NOTEBOOK_ID = os.getenv("NOTEBOOK_ID", "").strip()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
# Kto dostaje powiadomienie o wygasłej sesji. Pusto = osoba, która zada pytanie.
OWNER_USER_ID = os.getenv("OWNER_USER_ID", "").strip()
KEEPALIVE = float(os.getenv("KEEPALIVE_INTERVAL", "600"))
CHAT_TIMEOUT = float(os.getenv("CHAT_TIMEOUT", "300"))
# Termin całkowity zapytania. Musi być mniejszy niż okno followupu Discorda
# (15 min) i większy niż realny czas odpowiedzi NotebookLM.
BUDZET_ZAPYTANIA_S = float(os.getenv("BUDZET_ZAPYTANIA_S", "240"))

# Proaktywna kontrola sesji. Bez niej powiadomience wychodzilo WYLACZNIE z
# handlera pytania — a nikt nie pytal, wiec martwa sesja siedziala godzinami
# bez sladu. Zaobserwowane 30.09: bot "online", sesja wygasla o 22:03, a
# wiadomo o tym bylo dopiero przy kolejnym pytaniu.
# 0 = wylaczone (przydatne w testach, gdzie nie ma petli).
KONTROLA_SESJI_S = float(os.getenv("KONTROLA_SESJI_S", "900"))
# Rozbieg przed pierwszym pulsem — klient musi dojrzec. Osobna zmienna, bo
# inaczej test czekalby 30 s na zadanie, ktore ma zglaszac blad w milisekundach.
KONTROLA_SESJI_START_S = float(os.getenv("KONTROLA_SESJI_START_S", "30"))
# Kanal alarmowy. OWNER_USER_ID bywa pusty, a powiadomienie do zglaszajacego
# wymaga, zeby ktos najpierw zapytal — razem to znaczy, ze przy pustym wlascicielu
# nikt nie dostaje nic. Kanal dziala bez zadnego pytania.
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "").strip()

LIMIT_DISCORD = 2000
MAX_PODRZEDKOW = 50          # na uzytkownika; chroni pamiec procesu
MAX_ZRODL_CYTOWANIA = 400    # dlugosc cytatu w cytowaniu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ai-dan")

# ---------------------------------------------------------------------------
# Czysta logika — testowalna bez sieci
# ---------------------------------------------------------------------------


async def zapytaj_notebook(
    klient: Any, notebook_id: str, pytanie: str, conversation_id: str | None = None
) -> tuple[str, list[dict], str | None]:
    """Zwraca (odpowiedz, cytowania, conversation_id).

    Wywoluje `chat.ask` — jedyne poprawne wywolanie w 0.7.2. Rozmowa jest
    jednorazowa per pytanie, wiec `conversation_id` przekazujemy, gdy chcemy
    kontynuacji.
    """
    wynik = await klient.chat.ask(
        notebook_id, pytanie, conversation_id=conversation_id
    )
    odpowiedz = getattr(wynik, "answer", None) or ""
    cytowania = []
    for ref in getattr(wynik, "references", None) or []:
        cytowania.append(
            {
                "numer": getattr(ref, "citation_number", None),
                "tekst": (getattr(ref, "cited_text", None) or "").strip(),
                "source_id": getattr(ref, "source_id", None),
            }
        )
    return odpowiedz, cytowania, getattr(wynik, "conversation_id", None)


def _bez_diakrytykow(tekst: str) -> str:
    """Porownanie tekstow bez wrazliwosci na diakrytyki i wielkosc liter.

    NotebookLM potrafi zwrocic tytul z diakrytykami ("Podrecznik Gracza")
    i cytowany fragment bez nich, przez co proste `tytul in tekst` przepuszczalo
    ten sam napis dwa razy.
    """
    znormalizowany = unicodedata.normalize("NFKD", tekst or "")
    bez_znakow = "".join(c for c in znormalizowany if not unicodedata.combining(c))
    return " ".join(bez_znakow.lower().split())


def zbuduj_widok(odpowiedz: str, cytowania: list[dict], tytuly: dict[str, str] | None = None) -> str:
    """Tekst odpowiedzi + cytowania, przyciety do limitu Discorda."""
    tytuly = tytuly or {}
    czesci = [odpowiedz.strip()] if odpowiedz.strip() else []
    widoczne = [c for c in cytowania if (c.get("tekst") or "").strip()][:5]
    if widoczne:
        czesci.append("")
        czesci.append("Źródła:")
        for c in widoczne:
            numer = c.get("numer")
            prefiks = f"[{numer}] " if numer else ""
            tytul = tytuly.get(c.get("source_id") or "", "") or ""
            tekst = (c.get("tekst") or "").replace("\n", " ").strip()
            if len(tekst) > MAX_ZRODL_CYTOWANIA:
                tekst = tekst[: MAX_ZRODL_CYTOWANIA - 1] + "…"
            # Przy zrodlach tekstowych cytowany tekst to bywa naglowek sekcji,
            # a czasem caly tytul zrodla — wtedy doklejenie tytulu dawalo
            # "TYTUL - TYTUL". Nie powtarzamy.
            powtorzony = bool(tytul) and _bez_diakrytykow(tytul) in _bez_diakrytykow(tekst)
            nazwa = "" if powtorzony else (f" — {tytul}" if tytul else "")
            czesci.append(f"{prefiks}{tekst}{nazwa}")
    tekst = "\n".join(czesci).strip()
    if len(tekst) > LIMIT_DISCORD:
        tekst = tekst[: LIMIT_DISCORD - 20] + "\n…(ucięto)"
    return tekst


def tytuly_zrodel(zrodla: list[Any]) -> dict[str, str]:
    """source_id -> tytuł, żeby cytowanie miało nazwę zamiast samego ID."""
    out: dict[str, str] = {}
    for s in zrodla or []:
        sid = getattr(s, "id", None)
        tytul = getattr(s, "title", None) or getattr(s, "name", None)
        if sid and tytul:
            out[str(sid)] = str(tytul)
    return out


def komunikat_bledu(exc: BaseException) -> str:
    """Komunikat dla uzytkownika. Klasy zgodne z notebooklm-py 0.7.2."""
    if isinstance(exc, AuthError):
        return (
            "🔑 Sesja do notebooka wygasła. Bot musi się ponownie zalogować "
            "(`notebooklm login`) — powiadom administratora serwera."
        )
    if isinstance(exc, RateLimitError):
        return "⏳ Za dużo zapytań w krótkim czasie. Spróbuj ponownie za chwilę."
    if isinstance(exc, NotebookNotFoundError):
        return (
            "🔎 Nie widzę notatnika o ID z `NOTEBOOK_ID`. Sprawdź konfigurację "
            "albo uprawnienia konta Google."
        )
    if isinstance(exc, ValidationError):
        return f"✏️ Pytanie odrzucone przez notebook: {exc}"
    if isinstance(exc, NetworkError):
        return f"🌐 Brak połączenia z usługą NotebookLM: {exc}"
    if isinstance(exc, ChatError):
        return f"💬 Notebook nie umiał odpowiedzieć: {exc}"
    return f"❌ Błąd: {exc}"


def podziel_pytanie(pytanie: str) -> tuple[str, bool]:
    """Ostatnie słowo 'dalej'/'kontynuuj' = pytanie pod follow-up."""
    q = (pytanie or "").strip()
    if q.lower() in {"dalej", "kontynuuj", "cd", "więcej"}:
        return "", True
    return q, False


# --- Jednorazowe powiadomienie o wygasłej sesji --------------------------
#
# Sygnały są przepisane z biblioteki (`_auth/refresh.py`, `_AUTH_ERROR_SIGNALS`),
# żeby wykrywać dokładnie te sytuacje, dla których ona sama odpala hook
# odświeżania. Własna lista mogłaby się z biblioteką rozjechać.
SYGNAŁY_WYGASŁEJ_SESJI = (
    "authentication expired",
    "redirected to",
    "run 'notebooklm login'",
)

# Jeden bool na cały proces: powiadomienie leci RAZ na incydent, a nie przy
# każdym pytaniu. Zadna pętla, żadne zadanie w tle — koszt to jeden bit.
_powiadomiono_o_sesji = False

# Ostatni kanal, w ktorym ktos uzywal bota. Dzieki temu powiadomienie o
# wygaslej sesji ma gdzie trafic BEZ konfiguracji `ALERT_CHANNEL_ID` — a to
# wlasnie konfiguracja byla warunkiem, ktorego nikt nie ustawil, przez co puls
# wykryl wygasanie i zostala cisza. Kanal zapisywany przy kazdej odpowiedzi.
_ostatni_kanal: int | None = None


def zapamietaj_kanal(interaction: Any) -> None:
    global _ostatni_kanal
    kanal = getattr(interaction, "channel", None)
    ident = getattr(kanal, "id", None)
    if isinstance(ident, int):
        _ostatni_kanal = ident


def czy_blad_sesji(exc: BaseException) -> bool:
    """True, gdy błąd oznacza wygasłą sesję Google (nie np. limit zapytań)."""
    if isinstance(exc, AuthError):
        return True
    tekst = str(exc).lower()
    return any(s in tekst for s in SYGNAŁY_WYGASŁEJ_SESJI)


async def powiadom_o_wygaslej_sesji(
    bot: Any, bledy: list[int], id_zglaszajacego: int
) -> bool:
    """Wysyła pojedyncze powiadomienie o wygasłej sesji. Zwraca True, jeśli wyszło.

    Kolejność kanałów: najpierw właściciel (`OWNER_USER_ID`), potem osoba, która
    zadała pytanie. DM może być zamknięty, a wtedy bez drugiego kanału
    powiadomienie zniknęłoby bez śladu.
    """
    global _powiadomiono_o_sesji
    if _powiadomiono_o_sesji:
        return False
    _powiadomiono_o_sesji = True

    tekst = (
        "🔑 **Sesja NotebookLM wygasła** — bot nie odpowiada na pytania.\n\n"
        "Nie ma tego na serwerze: brak tam przeglądarki i profilu Google, więc "
        "serwer potrafi tylko wykryć. Naprawa idzie z komputera:\n\n"
        "1. `python tools/reauth.py` — wyciąga świeżą sesję z profilu Chrome "
        "**bez hasla** i sprawdza ją, zanim podmieni\n"
        "2. Wgrać `~/.notebooklm/sesja_na_serwer.json` na:\n"
        "   `/home/srv120794/ai-dan/nlm-home/storage_state.json`\n"
        "3. `python3.11 daemon.py stop && … start`\n\n"
        "Powiadomienie leci raz na incydent. Puls kontroli sprawdza sesję co "
        f"{KONTROLA_SESJI_S:.0f} s, więc nie trzeba zgadywać, czy bot żyje."
    )

    wysłane = False

    # Kanal alarmowy PRZED właścicielem: działa bez żadnego pytania i bez
    # znajomości id użytkownika. Właściciel bywa pusty, a wtedy poprzednio
    # powiadomienie mogło wyjść wyłącznie do osoby, która akurat zadała pytanie.
    #
    # Kolejność kanałów: `ALERT_CHANNEL_ID` z konfiguracji, a jak pusty — ostatni
    # kanał, w którym ktoś używał bota. Bez tego drugiego alarm zależałby od
    # kogoś, kto wpisze numer w `.env`, a nikt tego nie zrobił i puls miał
    # wykryć wygasanie bez miejsca, w które można je wysłać.
    for id_kanalu in ([int(ALERT_CHANNEL_ID)] if ALERT_CHANNEL_ID else []) + (
            [_ostatni_kanal] if _ostatni_kanal else []):
        try:
            kanal = await bot.fetch_channel(id_kanalu)
            await kanal.send(tekst)
            log.warning("Powiadomiono kanal %s o wygaslej sesji", id_kanalu)
            wysłane = True
        except Exception as exc:  # noqa: BLE001 — kanal moze byc niedostepny
            log.warning("Nie udalo sie powiadomic kanalu %s: %s", id_kanalu, exc)

    for id_docelowy in [int(OWNER_USER_ID)] if OWNER_USER_ID else []:
        if id_docelowy == id_zglaszajacego:
            continue
        try:
            uzytkownik = await bot.fetch_user(id_docelowy)
            await uzytkownik.send(tekst)
            log.warning("Powiadomiono wlasciciela %s o wygaslej sesji", id_docelowy)
            wysłane = True
        except Exception as exc:  # noqa: BLE001 — DM moze byc zamkniety
            log.warning("Nie udalo sie powiadomic wlasciciela %s: %s", id_docelowy, exc)

    if not wysłane and id_zglaszajacego:
        try:
            # `fetch_user`, nie `get_user`: `get_user` czyta wylacznie cache
            # i zwraca None dla kazdego, kto nie jest w pamieci. Bot jest
            # slash-only i nie ma message_content, wiec cache jest pusty —
            # na `get_user` powiadomienie nigdy nie wyszlo.
            uzytkownik = await bot.fetch_user(id_zglaszajacego)
            if uzytkownik is None:
                raise RuntimeError("fetch_user zwrocil None")
            await uzytkownik.send(tekst)
            log.warning("Powiadomiono uzytkownika %s o wygaslej sesji", id_zglaszajacego)
            wysłane = True
        except Exception as exc:  # noqa: BLE001
            log.error("Nie udalo sie powiadomic uzytkownika %s: %s", id_zglaszajacego, exc)

    return wysłane


# ---------------------------------------------------------------------------
# Klient Discord
# ---------------------------------------------------------------------------

intents = Intents.default()
intents.message_content = False  # bot jest slash-only; nie potrzebujemy tresci wiadomosci
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# NotebookLM: klient wchodzi w `async with` w petli zdarzen Discorda
# (tworzenie go poza petla gwarantuje wyciek zadania keepalive).
_nb_cm: Any = None
_klient_nb: Any = None

# user_id -> conversation_id (pytanie pod follow-up)
rozmowy: dict[int, str] = {}


_blad_polaczenia: BaseException | None = None


async def _zbuduj_klienta_nb() -> None:
    global _nb_cm, _klient_nb, _blad_polaczenia
    _nb_cm = NotebookLMClient.from_storage(
        keepalive=KEEPALIVE, chat_timeout=CHAT_TIMEOUT
    )
    _klient_nb = await _nb_cm.__aenter__()
    _blad_polaczenia = None
    log.info("NotebookLM: klient gotowy (keepalive=%ss, chat_timeout=%ss)", KEEPALIVE, CHAT_TIMEOUT)


async def _zamknij_klienta_nb() -> None:
    global _nb_cm, _klient_nb
    if _nb_cm is not None:
        try:
            await _nb_cm.__aexit__(None, None, None)
            log.info("NotebookLM: klient zamkniety")
        except Exception as exc:  # zamkniecie nie moze ubic klienta
            log.warning("NotebookLM: blad przy zamykaniu: %s", exc)
    _nb_cm = _klient_nb = None


@client.event
async def on_ready() -> None:
    global _blad_polaczenia, _zadanie_kontroli
    log.info("ai-dan online jako %s (ID=%s)", client.user, client.user.id)
    try:
        await _zbuduj_klienta_nb()
    except Exception as exc:
        # Bot startuje, ale nie odpowie - lepiej powiedziec to wprost
        _blad_polaczenia = exc
        log.error("NotebookLM: nie moge sie polaczyc (%s). /ai-dan bedzie zwracac blad.", exc)
    if KONTROLA_SESJI_S > 0 and _zadanie_kontroli is None:
        # `is None` chroni przed druga instancja: `on_ready` fires po kazdym
        # reconnect, a bez tego zostalaby petla na petle
        _zadanie_kontroli = asyncio.create_task(zadanie_kontroli_sesji())
        log.info("Uruchomiono zadanie kontroli sesji")
    if _zadanie_audio is None:
        _zadanie_audio = asyncio.create_task(zadanie_kolejki_audio())
        log.info("Uruchomiono zadanie kolejki audio")
    # `w_toku` po restarcie to zadanie, ktorego procesu juz nie ma. Bez
    # sprzatania kolejka nigdy nie ruszylaby — nastepne() zwraca tylko
    # `czekajace`, a `w_toku` zostaloby na wieki.
    import kolejka
    przerwane = kolejka.sprzataj_przerwane()
    if przerwane:
        log.info("Kolejka: oznaczono %d zadan jako przerwane po restarcie", przerwane)
    try:
        zsynchronizowane = await tree.sync()
        log.info("Zsynchronizowano %d komend: %s", len(zsynchronizowane),
                 ", ".join(c.name for c in zsynchronizowane))
    except Exception as exc:
        log.error("Nie udalo sie zsynchronizowac komend: %s", exc)


@client.event
async def on_close() -> None:
    global _zadanie_kontroli, _zadanie_audio
    for nazwa in ("_zadanie_kontroli", "_zadanie_audio"):
        zadanie = globals().get(nazwa)
        if zadanie is not None:
            zadanie.cancel()
            try:
                await zadanie
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            globals()[nazwa] = None
    await _zamknij_klienta_nb()
    log.info("ai-dan zamkniety")


# ---------------------------------------------------------------------------
# Komendy
# ---------------------------------------------------------------------------


async def zapytaj_z_budzetem(
    klient: Any,
    notebook_id: str,
    tresc: str,
    cid: str | None,
    *,
    budzet: float | None = None,
) -> tuple[Any, Any, Any]:
    """Zapytanie z CAŁKOWITYM terminem zwrotu.

    `chat_timeout` biblioteki to limit pojedynczego odczytu HTTP, nie czasu
    trwania zapytania. Strumień odpowiedzi, który trzyma połączenie otwarte
    i wysyła kolejne bajty, nigdy go nie przekroczy — a wtedy `ask()` nie
    wraca nigdy. Na Discordzie zostawało wtedy "myśli..." na zawsze, bo wiszący
    `await` nie jest wyjątkiem i żaden `except` go nie złapał.
    """
    return await asyncio.wait_for(
        zapytaj_notebook(klient, notebook_id, tresc, cid),
        timeout=BUDZET_ZAPYTANIA_S if budzet is None else budzet,
    )


async def sprawdz_sesje(klient: Any, notebook_id: str) -> list[Any]:
    """Jedno realne wywolanie wymagajace uwierzytelnienia.

    Samo `klient is not None` nie znaczy nic: klient zbudowany wczoraj
    moze miec sesje uniewazniona przez Google, a obiekt dalej istnieje.
    Tak wlasnie `/test` zglaszil "gotowy do odpowiedzi" na martwej sesji.
    `sources.list` to najtaniejsi RPC wymagajacy tokenu, wiec nadaje sie na
    puls. Wyjatki nie lapemy — caller musi je zobaczyc.
    """
    return await klient.sources.list(notebook_id)


_zadanie_kontroli: Any = None
_zadanie_audio: Any = None

# Interwal odpytywania kolejki. Generowanie trwa minuty wiec pytamy
# rzadko — kolejka jest plikiem, wiec sprawdzenie jest tanie.
KOLEJKA_CO_S = float(os.getenv("KOLEJKA_INTERVAL", "20"))
# Ile sekund na wygenerowanie jednego audio. Dolna granica nie jest losowa:
# NotebookLM potrafi nie odpowiedziec w ogole, a kolejka ma byc jednoznaczna.
KOLEJKA_LIMIT_JEDNOCZESNIE = 1


async def zadanie_kolejki_audio(dysk: Any = None) -> None:
    """Worker kolejki: bierze jedno zadanie, generuje, wysyla do kanalu.

    W procesie bota, nie osobno — z dwoch powodow. Po pierwsze, kazde
    wygenerowanie MUSI trafic do `limits.json`, a licznik jest licznikiem
    procesowym w tym samym katalogu. Po drugie, `refresh.py` pokazal, ze dwa
    procesy piszace do tego samego pliku to wyścig.

    Kolejka jest jednoznaczna: jedno zadanie w toku. Dwie generacje naraz to
    dwa limity dzienne, a tego nikt nie zamawial.
    """
    import kolejka

    dysk = dysk if dysk is not None else client
    log.info("Kolejka audio: start (co %.0f s)", KOLEJKA_CO_S)
    while True:
        try:
            zadanie = kolejka.nastepne()
            if zadanie is not None:
                await _obsluz_zadanie_audio(dysk, zadanie)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — worker nie moze umrzec
            log.error("Kolejka audio: blad w petli — %s", exc, exc_info=True)
        await asyncio.sleep(KOLEJKA_CO_S)


async def _obsluz_zadanie_audio(dysk: Any, zadanie: dict[str, Any]) -> None:
    import kolejka

    import generowanie

    zadanie_id = zadanie["id"]
    kanal_id = zadanie.get("channel_id")
    parametry = zadanie.get("parametry") or {}
    kolejka.oznacz(zadanie_id, "w_toku")
    log.info("Kolejka: biorę %s (%s) dla kanalu %s", zadanie_id,
             parametry.get("format", generowanie.DOMYSLNY_FORMAT), kanal_id)

    async def zglos(tekst: str) -> None:
        kanal = await dysk.fetch_channel(int(kanal_id))
        await kanal.send(tekst)

    raport = await generowanie.wygeneruj_audio(
        _klient_nb, NOTEBOOK_ID,
        dlugosc=parametry.get("dlugosc", generowanie.DOMYSLNA_DLUGOSC),
        format_audio=parametry.get("format", generowanie.DOMYSLNY_FORMAT),
        zgloszenie=zglos,
    )

    if "blad" in raport:
        kolejka.oznacz(zadanie_id, "nieudane", {"blad": raport["blad"]})
        log.warning("Kolejka: %s nieudane — %s", zadanie_id, raport["blad"])
        await _powiadom_kanal(dysk, kanal_id,
                              f"❌ **Audio nie wyszło** — {raport['blad'][:200]}")
        return

    # Licznik zapisywany PO WYNIKU, nie przy zleceniu: `zapisz_wynik` sam
    # rozroznia sukces, limit i awarie, a limit przychodzi zwykle juz
    # w odpowiedzi na zlecenie.
    status = raport["status"]
    if _licznik is not None:
        to_limit, opis = _licznik.zapisz_wynik("audio", status)
        log.info("Kolejka: %s zapisany jako %s%s", zadanie_id, opis,
                 " (LIMIT)" if to_limit else "")
        if to_limit:
            kolejka.oznacz(zadanie_id, "limit", {"opis": opis})
            await _powiadom_kanal(
                dysk, kanal_id,
                f"🚫 **NotebookLM odmówił** — {opis}. Zwykle oznacza to limit "
                f"dzienny; następna generacja będzie możliwa po jego resetcie.")
            return

    strona = str(getattr(status, "status", ""))
    if strona != "completed":
        kolejka.oznacz(zadanie_id, "nieudane", {"status": strona})
        await _powiadom_kanal(dysk, kanal_id,
                              f"❌ **Audio nie powstało** (status: {strona or '?'}).")
        return

    kolejka.oznacz(zadanie_id, "gotowe", {"task": str(getattr(status, "task_id", ""))})
    artifacts.wyczysc_cache()  # nowy artefakt musi byc widoczny od razu
    await _wyślij_wynik_audio(dysk, kanal_id, zadanie, raport)


async def _powiadom_kanal(dysk: Any, kanal_id: Any, tekst: str) -> None:
    try:
        kanal = await dysk.fetch_channel(int(kanal_id))
        await kanal.send(tekst)
    except Exception as exc:  # noqa: BLE001
        log.warning("Nie udalo sie powiadomic kanalu %s: %s", kanal_id, exc)


async def _wyślij_jeden_plik(dysk: Any, kanal: Any, w: dict[str, Any],
                             tytul: str) -> bool:
    """Sciaga plik i wysyla. Zwraca False, gdy przekracza limit."""
    import artifacts as art

    if w.get("miesci") is False:
        await kanal.send(
            f"⚠️ **{w['tytul']}** — {w['mb']:.2f} MB przekracza limit Discorda "
            f"(10 MiB). Nie wysyłam, bo plik zostałby przycięty bez ostrzeżenia. "
            f"Użyj `/pobierz`, żeby zobaczyć szczegóły, albo zamów `krotka`.")
        return False
    await kanal.send(content=f"**{w['tytul']}**\n{w['mb']:.2f} MB",
                     file=discord.File(
                         await _zapisz_plik_lokalnie(w), filename=f"{w['id'][:8]}.{art.rozszerzenie(w.get('typ', ''))}"
                     ))
    return True


async def _zapisz_plik_lokalnie(w: dict[str, Any]) -> str:
    import tempfile

    katalog = Path(tempfile.gettempdir()) / "ai-dan-art"
    katalog.mkdir(parents=True, exist_ok=True)
    sciezka = katalog / f"{w['id'][:8]}.tmp"
    sciezka.write_bytes(await pobierz_bajty(_klient_nb, w["url"]))
    return str(sciezka)


async def _wyślij_wynik_audio(dysk: Any, kanal_id: Any, zadanie: dict[str, Any],
                              raport: dict[str, Any]) -> None:
    """Znajduje gotowy plik i wysyla go do kanalu, ktory o zadanie prosil."""
    import artifacts as art

    znalezione = await art.zbierz(_klient_nb, typy=["audio"])
    kandydaci = [w for w in znalezione
                 if "blad" not in w and w.get("url")
                 and str(w.get("utworzono", "")).startswith(
                     time.strftime("%Y-%m-%d"))]
    kandydaci.sort(key=lambda w: w.get("utworzono", ""), reverse=True)
    if not kandydaci:
        await _powiadom_kanal(dysk, kanal_id,
                              "⚠️ Audio zgłoszone jako gotowe, ale nie znalazłem "
                              "go na liście. Spróbuj `/artefakty`.")
        return

    w = kandydaci[0]
    sekundy = raport.get("sekundy", 0.0)
    kanal = await dysk.fetch_channel(int(kanal_id))
    await kanal.send(f"🎧 Audio gotowe w {sekundy / 60:.1f} min — wysyłam plik…")
    await _wyślij_jeden_plik(dysk, kanal, w, zadanie.get("tytul", "Audio"))
    for rozmiar in Path(tempfile.gettempdir()).glob("ai-dan-art/*.tmp"):
        rozmiar.unlink(missing_ok=True)


@tree.command(name="audio", description="Wygeneruj podcast z notatnika (w kolejce)")
@app_commands.describe(
    dlugosc="Długość — krótka mieści się w załączniku Discorda",
    format_audio="Format rozmowy",
    temat="O czym ma być (opcjonalnie, np. 'zasady kolonii karnnej')",
)
@app_commands.choices(
    dlugosc=[app_commands.Choice(name=n, value=k) for k, n in generowanie.opcje_dlugosci()],
    format_audio=[app_commands.Choice(name=n, value=k) for k, n in generowanie.opcje_formatow()],
)
async def audio(
    interaction: discord.Interaction,
    dlugosc: str = generowanie.DOMYSLNA_DLUGOSC,
    format_audio: str = generowanie.DOMYSLNY_FORMAT,
    temat: str | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    if _klient_nb is None:
        await interaction.followup.send(_opis_przerwy()[0], ephemeral=True)
        return

    import kolejka

    import generowanie as gen

    if dlugosc not in gen.DLUGOSC:
        await interaction.followup.send(
            f"Nieznana długość `{dlugosc}`. Wybierz z listy.", ephemeral=True)
        return
    if format_audio not in gen.FORMAT:
        await interaction.followup.send(
            f"Nieznany format `{format_audio}`. Wybierz z listy.", ephemeral=True)
        return

    parametry = {"dlugosc": dlugosc, "format": format_audio}
    if temat:
        parametry["temat"] = temat[:200]
    try:
        zadanie = kolejka.dodaj("audio", channel_id=interaction.channel_id,
                                author_id=interaction.user.id,
                                parametry=parametry)
    except ValueError as exc:
        await interaction.followup.send(f"Kolejka pełna — {exc}"[:300], ephemeral=True)
        return

    za_soba = len(kolejka.aktywne())
    await interaction.followup.send(
        f"🎙️ Przyjęte do kolejki (#{zadanie['id']}).\n"
        f"Wybrano: **{gen.opis_wyboru(dlugosc, format_audio)}**\n"
        f"Aktualnie w kolejce: **{za_soba}** (jedno generowanie naraz).\n"
        f"Gotowe dostaniesz w tym kanale. Generowanie zjada limit dzienny — "
        f"nie generujemy, dopóki nie poprosisz.")
    log.info("Kolejka: dodano %s (%s/%s) przez %s", zadanie["id"], dlugosc,
             format_audio, interaction.user.id)


async def zadanie_kontroli_sesji(dysk: Any = None) -> None:
    """Puls sesji w procesie bota — wykrywa wygasanie bez czekania na pytanie.

    Swiadomie BEZ osobnego procesu. `refresh.py` byl drugim pisarzem
    `storage_state.json` obok bota, a dwa procesy zapisujace ten sam plik to
    wyścig — podejrzenie, ze to wlasnie zabijało sesje po ~1,93 h. Ten puls
    tylko CZYTA (`sources.list`, najtańszy RPC wymagajacy tokenu) i niczego
    nie zapisuje, wiec nie ma czego odbic.

    `ask()` nie wchodzi w gre — zjada limit dzienny. Wykrywanie musi byc
    najtańszym RPC, jaki wymaga uwierzytelnienia.

    `dysk` jest jawna zależnoscia zamiast globalnego `client` — dzieki temu
    test podmienia obiekt, a nie moduł.
    """
    dysk = dysk if dysk is not None else client
    log.info("Kontrola sesji: start co %ss", KONTROLA_SESJI_S)
    await asyncio.sleep(KONTROLA_SESJI_START_S)  # klient musi dojrzec
    while True:
        try:
            if _blad_polaczenia is not None:
                raise _blad_polaczenia
            if _klient_nb is not None:
                await sprawdz_sesje(_klient_nb, NOTEBOOK_ID)
                log.debug("Kontrola sesji: sesja zyje")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if czy_blad_sesji(exc):
                log.error("Kontrola sesji: SESJA WYGASLA — %s", exc)
                await powiadom_o_wygaslej_sesji(dysk, [], 0)
            else:
                log.warning("Kontrola sesji: blad, ale nie sesja — %s", exc)
        await asyncio.sleep(KONTROLA_SESJI_S)


def _opis_przerwy() -> tuple[str, bool]:
    """Komunikat o braku klienta + czy powodem jest wygasla sesja.

    Rozroznienie jest istotne operacyjnie: restart uslugi NIE naprawia
    wygaslej sesji, a naprawia kazdy inny blad startu. Meldowanie
    "zrestartuj usluge" przy martwej sesji kieruje w zla strone.
    """
    if _blad_polaczenia is not None and czy_blad_sesji(_blad_polaczenia):
        return (
            "🔑 **Sesja Google do NotebookLM wygasła** — dlatego nie ma klienta.\n"
            "Bot nie odpowie, dopóki sesja nie zostanie odnowiona "
            "(`notebooklm login`, potem restart usługi).",
            True,
        )
    return (
        "🔌 Bot nie ma klienta NotebookLM — sprawdź log i zrestartuj usługę.",
        False,
    )


@tree.command(name="artefakty", description="Gotowe materiały z notatnika (nie generuje)")
async def artefakty(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    if _klient_nb is None:
        await interaction.followup.send(_opis_przerwy()[0], ephemeral=True)
        return

    import artifacts
    try:
        znalezione = await artifacts.zbierz(_klient_nb, ttl=300.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("Artefakty nieodczytane: %s", exc)
        await interaction.followup.send(f"Nie udało się odczytać artefaktów: {exc}"[:400],
                                       ephemeral=True)
        await zareaguj_na_wygasla_sesje(interaction.client, exc, interaction.user.id)
        return

    gotowe = [w for w in znalezione if "blad" not in w]
    if not gotowe:
        await interaction.followup.send(
            "W notatniku nie ma jeszcze gotowych materiałów. "
            "Generowanie dojdzie wraz z komendami `/audio` i `/infografika`.",
            ephemeral=True)
        return

    linie = [f"**Gotowe materiały** ({len(gotowe)}) — wysyłanie nie zjada limitu", ""]
    for w in gotowe:
        linie.append(artifacts.opis(w))
        linie.append("")
    await interaction.followup.send("\n".join(linie)[:1900])
    log.info("Wypisano %d artefaktow dla %s", len(gotowe), interaction.user.id)


def artifacts_autocomplete(
    func: Any,
) -> Any:
    """Podpowiedzi dla parametru `id` w `/pobierz`.

    Osobna funkcja, bo `app_commands.autocomplete` musi byc zadeklarowane na
    KOMENDZIE, a nie na callbacku — i `choices` z `autocomplete` wykluczaja sie
    nawzajem, wiec nie da sie zrobic listy „na sztywno".

    `mierz=False` jest tu kluczowe: autocomplete odpala sie przy kazdym
    nacisnietym klawiszu, a pelny pomiar rozmiarow sciagalby kazdy plik
    z Google. Uzytkownik wpisujac slowo czekalby sekundami.

    Blad zwraca pusta liste, nie wywala: `except ValueError` (blad Discorda)
    w `.autocomplete()` jest jedynym miejscem, gdzie wyjatek moze byc w
    normalnym trybie pracy komendy.
    """
    async def callback(interaction: discord.Interaction, current: str) -> list:
        import artifacts

        if _klient_nb is None:
            return []
        try:
            # `ttl` jest tu nie optymalizacja, tylko wymog: pelne pobranie
            # mierzono 01.10 na 4,2-4,4 s, a limit Discorda to 3 s
            wpisy = await artifacts.zbierz(_klient_nb, mierz=False, ttl=90.0)
            wybor = artifacts.podpowiedzi(wpisy)
        except Exception as exc:  # noqa: BLE001
            log.warning("Podpowiedzi nieudane: %s", exc)
            return []
        prefiks = (current or "").strip().lower()
        if prefiks:
            wybor = [w for w in wybor
                     if prefiks in w["name"].lower() or prefiks in w["value"].lower()]
        return [app_commands.Choice(name=w["name"][:100], value=w["value"]) for w in wybor]

    return app_commands.autocomplete(id=callback)(func)


@tree.command(name="pobierz", description="Wyślij gotowy materiał na Discorda")
@app_commands.describe(id="Materiał do wysłania — wybierz z listy albo wklep id")
@artifacts_autocomplete
async def pobierz(interaction: discord.Interaction, id: str) -> None:  # noqa: A002
    await interaction.response.defer(thinking=True)
    if _klient_nb is None:
        await interaction.followup.send(_opis_przerwy()[0], ephemeral=True)
        return

    import artifacts
    ident = str(id).strip().lower()
    try:
        znalezione = await artifacts.zbierz(_klient_nb)
    except Exception as exc:  # noqa: BLE001
        log.warning("Artefakty nieodczytane: %s", exc)
        await interaction.followup.send(f"Nie udało się odczytać artefaktów: {exc}"[:400],
                                       ephemeral=True)
        await zareaguj_na_wygasla_sesje(interaction.client, exc, interaction.user.id)
        return

    trafiony = next((w for w in znalezione
                     if w.get("id", "").lower().startswith(ident)), None)
    if trafiony is None:
        await interaction.followup.send(
            f"Nie znalazłem artefaktu o id `{ident[:12]}`. "
            f"Wpisz `/artefakty`, żeby zobaczyć listę.", ephemeral=True)
        return
    if "url" not in trafiony:
        await interaction.followup.send(
            "Nie mam adresu pliku dla tego artefaktu.", ephemeral=True)
        return
    if trafiony.get("miesci") is False:
        await interaction.followup.send(
            f"**{trafiony['tytul']}** — {trafiony['mb']:.2f} MB, a limit Discorda to 10 MiB.\n"
            f"Nie wysyłam: plik zostałby przycięty albo odrzucony. "
            f"Podział na części dojdzie wraz z komendami generowania.",
            ephemeral=True)
        return

    try:
        await pobierz_i_wyslij(_klient_nb, trafiony, interaction)
    except Exception as exc:  # noqa: BLE001
        log.warning("Wyslanie artefaktu nieudane: %s", exc)
        await interaction.followup.send(f"Nie udało się wysłać pliku: {exc}"[:300],
                                       ephemeral=True)
        return
    log.info("Wyslano artefakt %s (%s MB) dla %s", trafiony["id"][:8],
             trafiony.get("mb"), interaction.user.id)


async def pobierz_i_wyslij(klient: Any, wpis: dict[str, Any],
                           interaction: discord.Interaction) -> None:
    """Sciaga plik na serwer i wysyla go jako zalacznik.

    Rozmiar sprawdzony w `artifacts.zbierz` — do tego miejsca trafiaja tylko
    pliki mieszcze sie w 10 MiB. Plik tymczasowy znika niezaleznie od
    wyniku, wiec nic nie zostaje na serwerze.
    """
    import hashlib

    import artifacts

    katalog = Path(tempfile.gettempdir()) / "ai-dan-art"
    katalog.mkdir(parents=True, exist_ok=True)
    nazwa = f"{wpis['id'][:8]}.{artifacts.rozszerzenie(wpis.get('typ', ''))}"
    sciezka = katalog / nazwa
    try:
        dane = await pobierz_bajty(klient, wpis["url"])
        sciezka.write_bytes(dane)
        await interaction.followup.send(
            content=f"**{wpis['tytul']}**\n{wpis['mb']:.2f} MB · {wpis['utworzono']}",
            file=discord.File(str(sciezka), filename=nazwa))
        log.info("Artefakt %s wyslany (%s B, md5=%s)", wpis["id"][:8], len(dane),
                 hashlib.md5(dane).hexdigest()[:8])
    finally:
        try:
            sciezka.unlink(missing_ok=True)
        except OSError:
            pass


async def pobierz_bajty(klient: Any, url: str) -> bytes:
    """Pobiera plik przez sesje Google — URL jest prywatny, nie zadziala bez niej."""
    import httpx
    from notebooklm._auth import cookies as auth_cookies

    jar = auth_cookies.build_httpx_cookies_from_storage(
        Path(os.environ["NOTEBOOKLM_HOME"]) / "storage_state.json")
    async with httpx.AsyncClient(cookies=jar, timeout=300.0,
                                 follow_redirects=True) as http:
        odpowiedz = await http.get(url)
        odpowiedz.raise_for_status()
        return odpowiedz.content


@tree.command(name="test", description="Sprawdź, czy bot działa")
async def test(interaction: discord.Interaction) -> None:
    uid = interaction.user.id
    if _klient_nb is None:
        await interaction.response.send_message(_opis_przerwy()[0])
        if _blad_polaczenia is not None:
            await zareaguj_na_wygasla_sesje(interaction.client, _blad_polaczenia, uid)
        return

    await interaction.response.defer(thinking=True)
    try:
        zrodla = await sprawdz_sesje(_klient_nb, NOTEBOOK_ID)
    except Exception as exc:  # noqa: BLE001 — chcemy wypisac powod
        log.warning("Test sesji nieudany: %s", exc)
        await interaction.followup.send(
            f"⚠️ Klient istnieje, ale **sesja do notebooka nie działa**.\n"
            f"Powód: {str(exc)[:200]}\n"
            f"Bot nie odpowie na pytania, dopóki sesja nie zostanie odnowiona."
        )
        await zareaguj_na_wygasla_sesje(interaction.client, exc, uid)
        return

    await interaction.followup.send(
        f"🤖 ai-dan działa. NotebookLM: gotowy do odpowiedzi "
        f"(sprawdzone {len(zrodla)} źródeł)."
    )


@tree.command(name="ai-dan", description="Zadaj pytanie pomocnikowi Mistrza Gry")
@app_commands.describe(
    pytanie="Twoje pytanie do asystenta. Wpisz 'dalej', aby kontynuować rozmowę.",
)
async def ai_dan(interaction: discord.Interaction, pytanie: str) -> None:
    await interaction.response.defer(thinking=True)
    uid = interaction.user.id

    if not NOTEBOOK_ID:
        await interaction.followup.send(
            "⚙️ Bot nie ma ustawionego `NOTEBOOK_ID` — bramka zgłoszona administratorowi.",
            ephemeral=True,
        )
        return
    if _klient_nb is None:
        await interaction.followup.send(_opis_przerwy()[0], ephemeral=True)
        if _blad_polaczenia is not None:
            await zareaguj_na_wygasla_sesje(interaction.client, _blad_polaczenia, uid)
        return

    tresc, followup = podziel_pytanie(pytanie)
    if not tresc and not followup:
        await interaction.followup.send("✏️ Pytanie jest puste.", ephemeral=True)
        return
    if not tresc and followup and uid not in rozmowy:
        await interaction.followup.send(
            "🔁 Nie mam jeszcze otwartej rozmowy z Tobą. Napisz pełne pytanie.",
            ephemeral=True,
        )
        return

    cid = rozmowy.get(uid) if followup else None
    try:
        odpowiedz, cytowania, nowy_cid = await zapytaj_z_budzetem(
            _klient_nb, NOTEBOOK_ID, tresc, cid
        )
    except asyncio.TimeoutError:
        # To nie jest limit kwoty i nie jest wygaśnięciem sesji — NotebookLM
        # przyjął zapytanie i nie dokończył. Licznik tego nie miesza.
        log.warning(
            "Zapytanie przekroczylo budzet %ss (user=%s) — strumien nie zostal domkniety",
            BUDZET_ZAPYTANIA_S, uid,
        )
        await interaction.followup.send(
            f"⏳ NotebookLM przyjął pytanie, ale nie dokończył w {BUDZET_ZAPYTANIA_S // 60} "
            f"minut. Odpowiedź mogła być wygenerowana po stronie Notion — sprawdź "
            f"notebook, a potem zapytaj ponownie.",
            ephemeral=True,
        )
        return
    except Exception as exc:
        log.warning("Zapytanie nieudane (user=%s): %s", uid, exc)
        await interaction.followup.send(komunikat_bledu(exc), ephemeral=True)
        await zareaguj_na_wygasla_sesje(interaction.client, exc, uid)
        return

    await _zakoncz_interakcje(interaction, uid, odpowiedz, cytowania, nowy_cid)


_powiadomiono_o_sesji = False
_licznik: Any = None


def _pobierz_licznika() -> Any:
    """Licznik limitow z katalogu aplikacji. Brak pliku nie moze wywrocic bota."""
    global _licznik
    if _licznik is None:
        try:
            from limits import LicznikLimitow
        except Exception as exc:  # noqa: BLE001 — telemetry nie jest krytyczna
            log.debug("Licznik limitow niedostepny: %s", exc)
            return None
        sciezka = os.getenv(
            "LIMITS_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "limits.json"),
        )
        try:
            _licznik = LicznikLimitow(sciezka)
        except Exception as exc:  # noqa: BLE001
            log.warning("Nie udalo sie otworzyc licznika limitow: %s", exc)
            return None
    return _licznik


async def zareaguj_na_wygasla_sesje(bot: Any, exc: BaseException, uid: int) -> bool:
    """Jedna reakcja na wygasla sesje: zapis w liczniku + powiadomienie.

    Wspoldzielona przez `/test` i `/ai-dan`, bo incydent jest ten sam.
    Zwraca True tylko gdy blad faktycznie dotyczy sesji — limit kwoty
    obsluguje osobno sciezka generowania.
    """
    if not czy_blad_sesji(exc):
        return False
    licznik = _pobierz_licznika()
    if licznik is not None:
        try:
            licznik.rejestruj_wygasniecie_sesji(exc)
        except Exception as blad_zapisu:  # noqa: BLE001
            log.warning("Zapis w liczniku limitow nieudany: %s", blad_zapisu)
    await powiadom_o_wygaslej_sesji(bot, [], uid)
    return True


async def _zakoncz_interakcje(
    interaction: discord.Interaction,
    uid: int,
    odpowiedz: str,
    cytowania: list[Any],
    nowy_cid: str | None,
) -> None:
    """Zapisuje follow-up i wysyla odpowiedz do uzytkownika.

    Osobna funkcja, nie ogon handlera: ta czesc zostala swiadomie
    odcieta przez zla edycje, przez co `/ai-dan` konczyl sie bez wyslania
    czegokolwiek — na Discordzie zostawalo "mysli..." na zawsze, bez
    wyjatku i bez logu, bo nie bylo czego zlapac. Wyciagniecie tego
    ogona do funkcji daje test, ktory w ogole da sie napisac.
    """
    if nowy_cid:
        if len(rozmowy) >= MAX_PODRZEDKOW and uid not in rozmowy:
            rozmowy.pop(next(iter(rozmowy)))
        rozmowy[uid] = nowy_cid

    zapamietaj_kanal(interaction)

    if not odpowiedz.strip():
        await interaction.followup.send(
            "📭 Notebook nie zawiera odpowiedzi na to pytanie. Spróbuj inaczej sformułować.",
            ephemeral=True,
        )
        return

    tytuly: dict[str, str] = {}
    if cytowania:
        try:
            tytuly = tytuly_zrodel(await _klient_nb.sources.list(NOTEBOOK_ID))
        except Exception as exc:
            log.debug("Nie pobrano tytulow zrodel: %s", exc)

    await interaction.followup.send("🤖 **ai-dan**\n" + zbuduj_widok(odpowiedz, cytowania, tytuly))
    log.info("Odpowiedz dla %s (%s znakow, %d cytowan)", uid, len(odpowiedz), len(cytowania))


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def main() -> int:
    braki = [n for n, v in (("NOTEBOOK_ID", NOTEBOOK_ID), ("DISCORD_TOKEN", DISCORD_TOKEN)) if not v]
    if braki:
        # v1 tylko ostrzegal i leciał dalej z pustym notebookiem — lepiej nie startować.
        log.error("Brak zmiennych środowiskowych: %s", ", ".join(braki))
        return 1
    log.info("Start ai-dan (notebook=%s)", NOTEBOOK_ID)
    try:
        client.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.info("Przerwano")
    return 0


if __name__ == "__main__":
    sys.exit(main())
