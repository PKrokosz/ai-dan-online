# Wdrożenie na serwer (srv120794)

Bot działa na hostingu CloudLinux i **nie potrzebuje Twojego komputera**. Jedyny
moment, w którym komputer jest potrzebny, to odnowienie sesji Google (raz na
kilka tygodni).

## Układ na serwerze

```
/home/srv120794/ai-dan/                 0700  aplikacja
├── bot.py                              0600
├── run.py                              0600  wczytuje .env, uruchamia bota
├── daemon.py                           0600  start/stop/status/log
├── .env                                0600  token + NOTEBOOKLM_HOME
├── bot.pid                             0600
├── bot.log                             0600
└── nlm-home/                           0700  sesja NotebookLM
    └── storage_state.json              0600  ciasteczka Google
```

`storage_state.json` to **pełna sesja Twojego konta Google** na kilka tygodni.
Każdy z dostępem shella na tym koncie może ją odczytać i zalogować się jako Ty.
To świadomy kompromis: bez pliku sesji nie ma automatyzacji.

## Sterowanie

```bash
python3.11 /home/srv120794/ai-dan/daemon.py start    # odpala w tle
python3.11 /home/srv120794/ai-dan/daemon.py stop
python3.11 /home/srv120794/ai-dan/daemon.py status
python3.11 /home/srv120794/ai-dan/daemon.py log      # ostatnie 40 linii
```

Daemonizer sam w sobie (podwójny fork + setsid + pidfile), bo whitelist serwera
dopuszcza wykonywanie tylko `python3.11` — nie da się odpalić `screen` ani
`nohup`. `daemon.py start` nie uruchomi drugiej instancji, jeśli pidfile wskazuje
żywy proces.

## Sesja: tryb plikowy, nie env

```
NOTEBOOKLM_HOME=/home/srv120794/ai-dan/nlm-home
```

W `.env` jest **celowo tryb plikowy**. Alternatywa (`NOTEBOOKLM_AUTH_JSON`) jest
wygodniejsza — inline JSON, bez pliku — ale **wyłącza samonaprawę sesji**:
`_resolve_recovery_path()` zwraca `None`, więc `_recover_psidts_inline()` odmawia
z komunikatem „no writeable backing store”. Bez pliku biblioteka nie odzyska
wygasłego `__Secure-1PSIDTS`, a wtedy potrzebny jest człowiek.

W praktyce na serwerze widać, że tryb plikowy żyje:

```
ai-dan online jako Ai-Dan#8817 (ID: 1484613933737312278)
POST https://accounts.google.com/RotateCookies  200 OK   ← keepalive sam podbija sesję
NotebookLM: klient gotowy (keepalive=600.0s)
```

`KEEPALIVE_INTERVAL=600` oznacza odświeżenie co 10 minut.

## Wgrywanie plików — pułapka FTP

**Nie zmieniaj katalogu przez `cwd`.** Na tym FTP `/home/srv120794` wskazuje
fizycznie `/home/srv120794/home/srv120794` — DirectAdmin duplikuje katalog
domowy. Pliki wgrane po `cwd` lądują tam, gdzie ich nie widać z shella, a `STOR`
zglasza sukces.

Poprawnie: `STOR` w korzeniu FTP (= `/home/srv120794`), potem przeniesienie
skryptem, który ustawia też uprawnienia:

```bash
python3.11 /home/srv120794/ai-dan/setup_app.py     # przenosi i ustawia 0600
```

### Dlaczego to nie jest tylko „niewidoczny plik”

Pierwsza wersja uploadera robiła właśnie `cwd` i przez to podczas wdrożenia
zostały **dwie kopie pliku sesji**:

```
0o644  19838 B  /home/srv120794/home/srv120794/storage_state.json
```

`0644` znaczy **czytelny dla każdego, kto ma shell na tym koncie** — a to pełna
sesja Google, wystarczająca do zalogowania się na Twoje konto. FTP nadaje `0644`
i nie ma `chmod` w whitelistcie shella, więc uprawnienia trzeba ustawiać skryptem.

Wniosek: przy wgrywaniu czegokolwiek wrażliwego **sprawdź uprawnienia po
wgraniu**, nie tylko to, że `STOR` zwrócił sukces. Skrypt
`tools/setup_app.py` w repo robi to właśnie.

## Stan wdrożenia

Zweryfikowane 2026-09-30: bot odpowiada na `/test` i `/ai-dan` na serwerze
(potwierdzenie właściciela, nie tylko logi).

```
11:44:29  ai-dan online jako Ai-Dan#8817 (ID: 1484613933737312278)
11:44:29  NotebookLM: klient gotowy (keepalive=600.0s, chat_timeout=300.0s)
11:54:30  POST https://accounts.google.com/RotateCookies  200
12:04:30  POST https://accounts.google.com/RotateCookies  200
```

Ostatnie dwie linie to ten sam mechanizm co 10 minut — sesja sama się podbija
bez udziału człowieka.

## Odnowienie sesji (jedyna czynność ręczna)

```bash
# 1. na komputerze
notebooklm login --browser chrome
notebooklm auth check --test --json         # musi być "status": "ok"

# 2. wgrać plik sesji (bez cwd!)
#    źródło: %USERPROFILE%\.notebooklm\profiles\default\storage_state.json

# 3. na serwerze: uprawnienia i restart
python3.11 /home/srv120794/setup_app.py
python3.11 /home/srv120794/ai-dan/daemon.py stop
python3.11 /home/srv120794/ai-dan/daemon.py start
```

Uwaga na ścieżkę profilu: CLI używa
`~/.notebooklm/profiles/default/storage_state.json`, a nie
`~/.notebooklm/storage_state.json`. Sprawdzenie złej ścieżki daje fałszywe
„brak pliku".

## Powiadomienie o wygasłej sesji

Gdy pytanie nie wraca z powodu sesji, bot wysyła **jedno** powiadomienie na
Discordzie: najpierw DM do `OWNER_USER_ID`, a gdy DM jest zamknięty — do osoby,
która zadała pytanie. Drugie powiadomienie w tym samym incydencie już nie
wychodzi; po pierwszym udanym pytaniu brotka się resetuje.

Koszt: jeden bool w procesie. Żadnej pętli, żadnego odpytywania w tle.

Treść zawiera konkretną instrukcję naprawy, bo incydent nie może się skończyć
komunikatem bez wyjścia dla człowieka.

## Czego świadomie nie ma

- **Watchdoga.** Nic nie pilnuje procesu po restarcie serwera. Po awarii
  hostingu bot nie wstanie sam — trzeba `daemon.py start`. Pętla nadzorcy
  w Pythonie byłaaby kilkunastoma linijkami, jeśli zajdzie potrzeba.
- **Synchronizacji komend do gildi.** Bot synchronizuje globalnie, więc komendy
  mogą nie pojawić się w serwerze przez długi czas mimo logu „Zsynchronizowano”.

## Czego nie da się zautomatyzować

Logowania do Google. Biblioteka nie umie zalogować się bez przeglądarki, a import
ciasteczek z zainstalowanego Chrome (`--browser-cookies`) wymaga `rookiepy`,
który buduje się z Rustu — `cargo` blokuje polityka kontroli aplikacji Windows
(`OSError: [WinError 4551]`), a na Pythonie 3.14 nie ma gotowego koła.

Dlatego powiadomienie zamiast automatycznej naprawy: naprawa wymaga człowieka,
ale nie wymaga zauważenia problemu.