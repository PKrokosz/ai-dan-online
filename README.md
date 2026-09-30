# ai-dan — slash-bot Discord odpowiadający z notatnika NotebookLM

Bot `Ai-Dan` odpowiada w serwerze **Larp Gothic** na pytania graczy, czerpiąc
odpowiedzi z notatnika [notatnik-discord 2](https://notebook.google.com/notebook/65f678e6-086c-43bf-a14a-2471286c35c0)
przez [`notebooklm-py`](https://github.com/teng-lin/notebooklm-py).

| | |
|---|---|
| Bot | `Ai-Dan` (`Ai-Dan#8817`), ID `1484613933737312278` |
| Serwer | Larp Gothic, ID `821384751902490624` |
| Notatnik | `65f678e6-086c-43bf-a14a-2471286c35c0` |
| Komendy | `/test`, `/ai-dan pytanie:…` |

Osobowość bota, limit długości odpowiedzi i stan wydarzenia **nie są w kodzie** —
są źródłami w notatniku (`system-prompt`, `AKTUALIZACJA — stan na 30.09.2026`).
Zmiana zachowania bota nie wymaga restartu ani deployu: wystarczy edycja źródła.

## Wymagania

- Python 3.12+ (sprawdzone na 3.12 i 3.14)
- token bota Discord
- zalogowany profil NotebookLM (patrz [`docs/NOTEBOOKLM.md`](docs/NOTEBOOKLM.md))

## Instalacja

```bash
git clone https://github.com/PKrokosz/ai-dan-online.git
cd ai-dan-online
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

## Uruchomienie

```bash
cp .env.example .env      # Windows: copy .env.example .env
# uzupelnij NOTEBOOK_ID i DISCORD_TOKEN
python run.py
```

Bot zaloguje się i zsynchronizuje komendy. Trwały log:

```bash
python run.py 2>&1 | tee ai-dan.log
```

## Komendy

| Komenda | Działanie |
|---|---|
| `/test` | Sprawdza, czy bot żyje i czy klient NotebookLM jest gotowy |
| `/ai-dan pytanie:…` | Zadaje pytanie notatnikowi i zwraca odpowiedź z cytowaniami |

`/ai-dan` bez tekstu pyta, co robić dalej. Słowa `dalej`, `cd`, `więcej`
kontynuują wątek przez `conversation_id`, więc odpowiedzi nie powtarzają kontekstu.

## Bramki

```bash
python tests/test_bot.py         # 20 asercji kontraktu, bez sieci i tokenu
python tests/test_negatywny.py   # 9 wprowadzonych regresji musi paść
```

Druga bramka jest najważniejsza: cofa `bot.py` do wersji zepsutej w dziewięciu
miejscach i **wymaga**, żeby testy się wywróciły, po czym sprawdza hash, że plik
wrócił bajt w bajt. Zielony wynik bez niej mógłby oznaczać brak pokrycia, a nie
poprawność — dokładnie tak było w wersji v1, która nie miała żadnych testów,
a mimo to leżała w repo i nie dawała się uruchomić.

## Dokumentacja

| Plik | Co w nim |
|---|---|
| [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) | Przepływ, kontrakt z NotebookLM, granice testowalności |
| [`docs/OPERACJE.md`](docs/OPERACJE.md) | Start/stop, logi, deploy, co robić gdy bot nie odpowiada |
| [`docs/NOTEBOOKLM.md`](docs/NOTEBOOKLM.md) | Logowanie, potwierdzone pułapki biblioteki i środowiska |
| [`docs/ZNANE-PROBLEMY.md`](docs/ZNANE-PROBLEMY.md) | Otwarte ograniczenia, z którymi bot żyje |
| [`docs/HISTORIA.md`](docs/HISTORIA.md) | Co było nie tak w wersji v1 i jak to wyszło na jaw |

## Bezpieczeństwo

- `.env` i `storage_state.json` są zignorowane; żaden sekret nie wchodzi do repo.
- Token nie trafia do `argv` — `run.py` czyta go w procesie.
- Bot nie ma interfejsu webowego i nie potrzebuje webroota.