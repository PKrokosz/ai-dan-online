# Wdrożenie na serwer (srv120794)

Bot działa na hostingu CloudLinux i **nie potrzebuje Twojego komputera**. Jedyny
moment, w którym komputer jest potrzebny, to odnowienie sesji Google.

**Zmierzono 2026-09-30: sesja wygasła po 2,5 godziny**, przy ciasteczkach ważnych
kolejne 365 dni. Nie „raz na kilka tygodni", tylko **w ciągu godzin**. Zakładanie
okresu to zgadywanie — dlatego bot zgłasza awarię sam, a procedura jest tu.

## Układ na serwerze

```
/home/srv120794/ai-dan/                 0700  aplikacja
├── bot.py                              0600
├── limits.py                           0600  licznik limitów kwoty
├── run.py                              0600  wczytuje .env, uruchamia bota
├── daemon.py                           0600  start/stop/status/log
├── .env                                0600  token + NOTEBOOKLM_HOME
├── limits.json                         0600  zdarzenia licznika (runtime)
├── bot.pid                             0600
├── bot.log                             0600
├── tests/                                    testy kontraktu
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

Zweryfikowane 2026-09-30 o 14:56 potwierdzeniem rozmówcy: **bot odpowiada** na
`/ai-dan` realną odpowiedzią z notatnika. To jedyne kryterium — log sam w sobie
nie dowodzi, że coś doszło do użytkownika.

```
14:22:08  NotebookLM: klient gotowy (keepalive=600.0s, chat_timeout=300.0s)
14:56     odpowiedz na Discordzie (potwierdzenie rozmówcy)
```

Stan techniczny: 43 + 40 testów, 26/26 złamań, sesja odnowiona, licznik
zasilony 5 sukcesami z istniejących artefaktów.

### Utrwalona wersja

Stan roboczy jest w repo (`origin/main`) i **identyczny z tym, co działa na
serwerze**. Weryfikacja zgodności: `install_files.py` porównuje pliki i raportuje
`juz w aplikacji`, gdy nic się nie różni.

Kolejność przy wdrażaniu nowej wersji — kolejność jest obowiązkowa:

```bash
python tests/test_bot.py && python tests/test_limits.py && python tests/test_negatywny.py
node check_encoding.mjs
# ... wgranie plików ...
python3.11 /home/srv120794/install_files.py     # raportuje stan katalogu
python3.11 /home/srv120794/ai-dan/tests/test_bot.py    # testy na serwerze
python3.11 /home/srv120794/ai-dan/daemon.py stop
python3.11 /home/srv120794/ai-dan/daemon.py start
python3.11 /home/srv120794/ai-dan/daemon.py log       # szukaj: "klient gotowy"
```

Testy uruchomione **na serwerze** mają znaczenie: lokalne mogą przechodzić przy
innym Pythonie, innej wersji biblioteki i innych uprawnieniach do plików.

Zweryfikowane 2026-09-30 o 14:56 potwierdzeniem rozmówcy: **bot odpowiada** na
`/ai-dan` realną odpowiedzią z notatnika. To jedyne kryterium — log sam w sobie
nie dowodzi, że coś doszło do użytkownika (dokładnie tak było przez pół godziny,
gdy handler nie wysyłał nic mimo „200 OK" w logu).

```
14:22:08  NotebookLM: klient gotowy (keepalive=600.0s, chat_timeout=300.0s)
14:56     odpowiedz na Discordzie (potwierdzenie rozmówcy)
```

Stan techniczny w tym momencie: 43 + 40 testów, 26/26 złamań, sesja odnowiona,
licznik zasilony 5 sukcesami z istniejących artefaktów.

## Powiadomienie o wygasłej sesji

Gdy pytanie nie wraca z powodu sesji, bot wysyła **jedno** powiadomienie na
Discordzie: najpierw DM do `OWNER_USER_ID`, a gdy DM jest zamknięty — do osoby,
która zadała pytanie. Drugie w tym samym incydencie już nie wychodzi; po
pierwszym udanym pytaniu brotka się resetuje.

Koszt: jeden bool w procesie. Żadnej pętli, żadnego odpytywania w tle.

Treść zawiera konkretną instrukcję naprawy, bo incydent nie może się skończyć
komunikatem bez wyjścia dla człowieka.

**Ważne — to powiadomienie zadziałało 30.09 dopiero po naprawie.** Pierwsza
wersja używała `get_user()`, który czyta wyłącznie cache Discorda i przy pustym
cache (bot jest slash-only, bez `message_content`) zwracał `None` **zawsze**.
Mechanizm milczał przy pierwszej awarii, dla której istniał. Patrz
`docs/HISTORIA.md`.

## Automatyczne wykrywanie — świadomie wyłączone

Bot **nie sprawdza sam**, czy sesja żyje. Zgłasza awarię dopiero po pytaniu.
Powód: nie chciałeś dodatkowej pętli na serwerze.

Koszt byłby znikomy — jeden bool i jedno uwierzytelnione zapytanie co 20 minut.
Biblioteka daje do tego `notebooklm auth refresh` (rotacja ciasteczek **i zapis
na dysk**, „one-shot keepalive"; dokumentacja podaje 15–20 minut dla użytku
bezobsługowego). Pilnuje też świeżości słoika na dysku — czego sam klient
w pamięci nie robi przy awarii procesu.

Warto wrócić do tego, gdy kolejna sesja padnie wcześniej, niż zdążysz zauważyć.

## Odnowienie sesji (jedyna czynność ręczna)

### Co zadziałało 30.09.2026

```bash
# 1. na komputerze — logowanie (okno otwiera się na pulpicie)
cd C:\Users\admin\Desktop\ai-dan-online
python -m notebooklm login --browser chrome

# 2. UWAGA: CLI może skończyć się "Login not detected within 5 minutes",
#    nawet gdy logowanie się udało. Sprawdź, zamiast wierzyć:
python -m notebooklm --storage <ścieżka> auth check --test    # "Authentication is valid."
```

Dokładnie tak było 30.09: dwie próby `login` zgłosiły brak logowania, a
**profil przeglądarki miał komplet ciasteczek Google** (`SID`, `__Secure-1PSIDTS`
i resztę). Wykrycie w CLI zawiodło, sesja była prawdziwa.

Jeśli `login` nie zdąży, a profil narzędzia ma ciasteczka, da się je odczytać
przez Playwrighta z **oryginalnego** katalogu profilu (`docs/NOTEBOOKLM.md`).

### Ścieżki, które nie działają — nie próbuj ich

| Ścieżka | Dlaczego odpada |
|---|---|
| `notebooklm auth refresh` | przy martwej sesji: `Token fetch failed`. Obraca ciasteczko na wierzchu martwej sesji |
| `--browser-cookies` + `rookiepy` | ciasteczka Chrome 127+ są `v20` (app-bound), klucz ma tylko Chrome |
| kopia profilu Chrome | Chrome **kasuje** bazę w kopii — klucz jest związany z katalogiem |
| odczyt z zainstalowanego Chrome | j.w., `rookiepy` tego nie odczyta |

### Wgranie i restart — kolejność ma znaczenie

```bash
# 3. wgrać plik sesji (bez cwd!)
#    źródło: %USERPROFILE%\.notebooklm\profiles\default\storage_state.json

# 4. NA SERWERZE, w tej kolejności:
python3.11 /home/srv120794/ai-dan/daemon.py stop        # 1. zatrzymaj
python3.11 /home/srv120794/inst_sesji.py               # 2. podmiana sesji
python3.11 /home/srv120794/ai-dan/daemon.py start       # 3. wystartuj
```

**Dlaczego kolejność jest obowiązkowa:** klient NotebookLM zapisuje stary słoik
przy zamykaniu (`__aexit__`). Podmiana sesji przy żywym procesie przestawia plik
i **cicho cofa do stanu sprzed wgrania**. Instalator `inst_sesji.py` dlatego
**odmawia pracy, gdy bot działa** — to nie kaprys, tylko blokada przed cofnięciem.

Instalator dodatkowo odrzuca plik bez wymaganych ciasteczek i kasuje plik
tymczasowy z katalogu domowego.

Uwaga na ścieżkę profilu: CLI używa
`~/.notebooklm/profiles/default/storage_state.json`, a nie
`~/.notebooklm/storage_state.json`. Sprawdzenie złej ścieżki daje fałszywe
„brak pliku".

## Czego świadomie nie ma

- **Watchdoga.** Nic nie pilnuje procesu po restarcie serwera. Po awarii
  hostingu bot nie wstanie sam — trzeba `daemon.py start`. Pętla nadzorcy
  w Pythonie byłaaby kilkunastoma linijkami, jeśli zajdzie potrzeba.
- **Synchronizacji komend do gildi.** Bot synchronizuje globalnie, więc komendy
  mogą nie pojawić się w serwerze przez długi czas mimo logu „Zsynchronizowano”.

## Czego nie da się zautomatyzować

Logowania do Google. Wymaga wpisania hasła i ewentualnego 2FA przez człowieka —
żadna automatyzacja tego nie obejdzie.

Ścieżki bezokienne też odpadły (30.09, zmierzone):

- `auth refresh` — przy martwej sesji zwraca `Token fetch failed`,
- `--browser-cookies` + `rookiepy` — ciasteczka `v20` (app-bound), klucz ma
  tylko Chrome; `rookiepy` ich nie odczyta,
- kopia profilu Chrome — Chrome kasuje w niej bazę ciasteczek.

Dlatego powiadomienie zamiast automatycznej naprawy: naprawa wymaga człowieka,
ale nie wymaga zauważenia problemu.

## Ryzyko, które przyjęliśmy świadomie

Sesja Google wygasła w **2,5 godziny** od wdrożenia, przy ciasteczkach ważnych
365 dni i pliku nietkniętym. Najbardziej prawdopodobne, że winne jest **jedno
konto używane z dwóch IP** — ale to **hipoteza, nie ustalenie**.

Kontrolny eksperyment (dedykowane konto Google tylko dla bota) został rozważony
i **odrzucony**. Świadomie przyjmujemy, że awaria może się powtórzyć. Jeśli
zdecydowana zostanie inaczej, najskuteczniejsza pojedyncza zmiana to właśnie
dedykowane konto — izoluje unieważnienie od Twojego konta i sprowadza je do
jednego IP.