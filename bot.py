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
            await bot.get_user(id_zglaszajacego).send(tekst)
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


async def _zbuduj_klienta_nb() -> None:
    global _nb_cm, _klient_nb
    _nb_cm = NotebookLMClient.from_storage(
        keepalive=KEEPALIVE, chat_timeout=CHAT_TIMEOUT
    )
    _klient_nb = await _nb_cm.__aenter__()
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
    log.info("ai-dan online jako %s (ID: %s)", client.user, client.user.id)
    try:
        await _zbuduj_klienta_nb()
    except Exception as exc:
        # Bot startuje, ale nie odpowie — lepiej powiedziec to wprost
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


@tree.command(name="test", description="Sprawdź, czy bot działa")
async def test(interaction: discord.Interaction) -> None:
    stan = "gotowy do odpowiedzi" if _klient_nb is not None else "BEZ połączenia z notebookiem"
    await interaction.response.send_message(f"🤖 ai-dan działa. NotebookLM: {stan}.")


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
        await interaction.followup.send(
            "🔌 Bot nie ma połączenia z notebookem. Administrator musi zrestartować usługę.",
            ephemeral=True,
        )
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
        odpowiedz, cytowania, nowy_cid = await zapytaj_notebook(
            _klient_nb, NOTEBOOK_ID, tresc, cid
        )
    except Exception as exc:
        log.warning("Zapytanie nieudane (user=%s): %s", uid, exc)
        await interaction.followup.send(komunikat_bledu(exc), ephemeral=True)
        if czy_blad_sesji(exc):
            await powiadom_o_wygaslej_sesji(interaction.client, bledy, uid)
        return

    global _powiadomiono_o_sesji
    _powiadomiono_o_sesji = False

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
