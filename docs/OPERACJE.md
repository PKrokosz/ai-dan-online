# Operacje

## Start

```bash
# Windows
C:\Users\admin\AppData\Local\Temp\ai-dan.env     # sekret poza repo
python run.py --env C:\Users\admin\AppData\Local\Temp\ai-dan.env

# Linux (serwer)
/home/<konto>/ai-dan.env                        # chmod 0600
python3 run.py --env /home/<konto>/ai-dan.env
```

Bot bez sekretów kończy się kodem **1** i wypisuje **nazwy** brakujących zmiennych,
nigdy ich wartości. Nie ma trybu „ostrzeż i jedź” — w wersji v1 był i dlatego
bot startował z pustym `NOTEBOOK_ID`.

Poprawny start wygląda tak:

```
konfiguracja: .../ai-dan.env (3 zmiennych)
DISCORD_TOKEN: 72 znakow (ukryty)
NOTEBOOK_ID:   65f678e6-086c-43bf-a14a-2471286c35c0
Start ai-dan (notebook=65f678e6-086c-43bf-a14a-2471286c35c0)
ai-dan online jako Ai-Dan#8817 (ID: 1484613933737312278)
NotebookLM: klient gotowy (keepalive=600.0s, chat_timeout=300.0s)
Zsynchronizowano 2 komend: test, ai-dan
```

## Stop

Ctrl+C przy uruchomieniu w terminalu. Z daleka — po PID:

```bash
# Windows
taskkill /PID <pid> /F
# Linux
kill <pid>
```

Bot nie zapisuje PID w pliku, więc jedynym wygodnym sposobem jest uruchomienie
przez menedżer procesów albo dopisanie `echo $$!` w skrypcie startowym.

## Restart po zmianie kodu

```bash
python tests/test_bot.py && python tests/test_negatywny.py && python run.py
```

**Zmiana źródeł w notatniku nie wymaga restartu.** Klient czyta je przy każdym
pytaniu. Weryfikacja odpowiedzi:

```bash
python -c "..."
```

albo po prostu zapytaj bota na Discordzie. Czasem odpowiedź uwzględnia nowe
źródło dopiero po kilkudziesięciu sekundach — to czas indeksowania po stronie
NotebookLM, nie cache bota.

## Diagnostyka

| Objaw | Przyczyna | Co zrobić |
|---|---|---|
| `Brak zmiennych środowiskowych` | `.env` nie wskazany lub nieczytelny | `python run.py --env <ścieżka>` |
| `Authentication expired or invalid` | wygasła sesja Google | `notebooklm login --browser chrome`, potem `notebooklm auth check --test --json` |
| `Notebook not found` | złe `NOTEBOOK_ID` | `notebooklm list` i porównaj ID |
| komendy nie widać na serwerze | globalna synchronizacja | `gh`-owy limit Discorda; zsynczuj do gildi (`docs/ZNANE-PROBLEMY.md`) |
| bot online, ale odpowiada „co dzisiaj” | stare zachowanie w kontekście rozmowy | `notebooklm auth check --test`; sprawdź, czy źródło z aktualizacją jest w notatniku |
| `RateLimitError` | za dużo pytań | poczekaj; `RATE_LIMIT` obsługuje komunikat dla użytkownika |

## Sprawdzenie, co jest w notatniku

```bash
notebooklm list                    # notatniki dostepne dla konta
```

Bot widzi wyłącznie zawartość swojego notatnika. Jeśli ktoś twierdzi, że bot
„nie wie", sprawdź najpierw, czy informacja jest w źródle — bot nie ma dostępu do
niczego poza nim.

## Bramki przed commitem

```bash
python tests/test_bot.py        # musi być OK
python tests/test_negatywny.py  # musi być "łapie 9/9" i "identyczny bajt w bajt"
```

Test negatywny **cofa `bot.py` do wersji zepsutej i przywraca go**. Jeśli zostaniesz
przerwany w trakcie, w katalogu zostanie `bot.py.nagatyw` — usuń ręcznie. Nie
przerywaj tego testu.

## Deploy

Bot nie ma interfejsu webowego. Nie potrzebuje webroota, `.htaccess` ani LiteSpeed.
Wystarczy katalog poza `public_html`, token w pliku `.env` o uprawnieniach `0600`
i proces pod menedżerem (systemd, `supervisord`, `screen`).

Rekomendowany plik systemd:

```ini
[Unit]
Description=ai-dan — bot Discord (NotebookLM)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/<konto>/ai-dan-online
ExecStart=/usr/bin/python3 run.py --env /home/<konto>/ai-dan.env
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

## Kopia zapasowa

Bot jest w pełni odtwarzalny z repo — nie ma stanu do zapisania. Do odtworzenia
potrzebne są dwie rzeczy, obie poza repo:

1. token bota,
2. profil logowania NotebookLM.

Profil trzyma ciasteczka Google z ważnością kilku tygodni, więc **zdarza się, że
trzeba się zalogować ponownie**. To najczęstszy powód „bot przestał działać”.