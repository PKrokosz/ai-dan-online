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
from typing import Any

import unicodedata

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
        "Poprawka (jednorazowa, kilka minut):\n"
        "1. Na komputerze: `notebooklm login --browser chrome`\n"
        "2. Wgrać `storage_state.json` na serwer:\n"
        "   `/home/srv120794/ai-dan/nlm-home/storage_state.json`\n"
        "3. `python3.11 /home/srv120794/ai-dan/daemon.py stop && … start`\n\n"
        "Nie da się tego zrobić automatycznie — biblioteka nie umie zalogować się "
        "bez przeglądarki, a `rookiepy` (import ciasteczek) wymaga Rust, którego "
        "blokuje polityka Windows. Powiadomienie leci raz na incydent."
    )

    wysłane = False
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

    if not wysłane:
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
    global _blad_polaczenia
    log.info("ai-dan online jako %s (ID=%s)", client.user, client.user.id)
    try:
        await _zbuduj_klienta_nb()
    except Exception as exc:
        # Bot startuje, ale nie odpowie - lepiej powiedziec to wprost
        _blad_polaczenia = exc
        log.error("NotebookLM: nie moge sie polaczyc (%s). /ai-dan bedzie zwracac blad.", exc)
    try:
        zsynchronizowane = await tree.sync()
        log.info("Zsynchronizowano %d komend: %s", len(zsynchronizowane),
                 ", ".join(c.name for c in zsynchronizowane))
    except Exception as exc:
        log.error("Nie udalo sie zsynchronizowac komend: %s", exc)


@client.event
async def on_close() -> None:
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
