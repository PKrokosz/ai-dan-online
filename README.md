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
python tests/test_bot.py         # 43 asercje kontraktu, bez sieci i tokenu
python tests/test_limits.py      # 40 asercje licznika limitów
python tests/test_negatywny.py   # 26 wprowadzonych regresji musi paść
```

Druga bramka jest najważniejsza: **cofa `bot.py` do wersji zepsutej i wymaga,
żeby testy się wywróciły**, po czym sprawdza hash, że plik wrócił bajt w bajt.
Zielony wynik bez niej mógłby oznaczać brak pokrycia, a nie poprawność.

Ta bramka złapała realne błędy — między innymi ucięty handler `/ai-dan`, przez
który bot **nie wysyłał odpowiedzi w ogóle**, przy zielonych testach i `200 OK`
w logu. Historia: [`docs/HISTORIA.md`](docs/HISTORIA.md).

Zasada wyprowadzona z tych doświadczeń: **złamanie, które nie robi żadnego testu
czerwonym, jest złamaniem, które udaje ochronę.** Trzy razy zdarzyło się, że
test sprawdzał wyciągniętą jednostkę, a nie okablowanie — i złamanie przechodziło.

## Licznik limitów

```bash
python tools/limits_probe.py --sprawdz   # czy sesja żyje + stan licznika
python tools/limits_probe.py --zasil     # wczytaj istniejące artefakty (koszt 0)
python tools/limits_probe.py --odczyt    # sam raport
```

Limitów kwoty **nie da się odczytać z API** — da się tylko liczyć zdarzenia.
Licznik wyznacza **granice**, nie dokładne liczby, i mówi wprost, gdy czegoś
jeszcze nie wie. Szczegóły: [`docs/LICZNIK.md`](docs/LICZNIK.md).

## Dokumentacja

| Plik | Co w nim |
|---|---|
| [`docs/ARCHITEKTURA.md`](docs/ARCHITEKTURA.md) | Przepływ, kontrakt z NotebookLM, granice testowalności |
| [`docs/WDROZENIE-SERWER.md`](docs/WDROZENIE-SERWER.md) | Wdrożenie na srv120794, sterowanie, odnowienie sesji, pułapka FTP |
| [`docs/OPERACJE.md`](docs/OPERACJE.md) | Start/stop, logi, co robić gdy bot nie odpowiada, licznik |
| [`docs/LICZNIK.md`](docs/LICZNIK.md) | Licznik limitów: co mierzy, czego nie mierzy, po co granice |
| [`docs/NOTEBOOKLM.md`](docs/NOTEBOOKLM.md) | Logowanie, potwierdzone pułapki biblioteki i środowiska |
| [`docs/ZNANE-PROBLEMY.md`](docs/ZNANE-PROBLEMY.md) | Otwarte ograniczenia, z którymi bot żyje |
| [`docs/HISTORIA.md`](docs/HISTORIA.md) | Co było nie tak w wersji v1 i 30.09, i jak to wyszło na jaw |

## Na serwerze

Bot działa na `srv120794` i nie wymaga Twojego komputera:

```bash
python3.11 /home/srv120794/ai-dan/daemon.py start|stop|status|log
```

Komputer jest potrzebny **tylko** przy odnowieniu sesji Google — i to częściej,
niż zakładaliśmy: zmierzone **2,5 godziny** od wdrożenia, przy ciasteczkach
ważnych 365 dni. Bot zgłasza awarię sam na Discordzie (jedno powiadomienie,
nie spam).

Procedura: [`docs/WDROZENIE-SERWER.md`](docs/WDROZENIE-SERWER.md) → „Odnowienie sesji",
wraz z listą ścieżek, które **nie działają** i nie warto próbować.

## Stan na 30.09.2026

Działa — potwierdzone prawdziwą odpowiedzią z notatnika, nie samym logiem
(to rozróżnienie okazało się istotne: przez pół godziny log pokazywał
`200 OK`, a bot **nie wysyłał nic**).

| | |
|---|---|
| Sesja | żyje, odnowiona przez Playwrighta |
| Testy | 43 + 40 zielone, 26/26 złamań |
| Licznik | 5 sukcesów (audio 1, wideo 1, infografiki 3), 0 limitów |
| Okno resetu limitów | **nieznane** — brak pary limit → sukces |

Ostatnie dwie pozycje to brak danych, nie wynik. Licznik mówi o tym wprost
zamiast podawać liczbę zgadniętą.

## Bezpieczeństwo

- `.env` i `storage_state.json` są zignorowane; żaden sekret nie wchodzi do repo.
- Token nie trafia do `argv` — `run.py` czyta go w procesie.
- Bot nie ma interfejsu webowego i nie potrzebuje webroota.