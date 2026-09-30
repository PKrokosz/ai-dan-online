# Historia: od wersji v1 do dziś

## Punkt wyjścia

W repo strony (`pkrokosz.pl`, katalog `public/ai-dan/backend/`) leżał plik
`bot.py` — 255 linii, dwie komendy slash, `.env` z pustymi wartościami. Wyglądał
na działający bot. **Nie dawał się uruchomić** — żadna z dwóch komend nie
odpowiadała.

## Co było zepsute

Cztery błędy, wykryte przez odczyt sygnatur w zainstalowanej bibliotece, nie
przez zgadywanie:

1. `client.chat(...)` — `chat` jest **namespace**, a nie funkcją. `TypeError`
   przy każdym pytaniu. Poprawnie: `await client.chat.ask(...)`.
2. `message=` i `timeout=` — nie istnieją w sygnaturze `ask`.
3. Odpowiedź ma `answer` i `references[ChatReference]`; v1 szukał `cited_sources`
   i `src.title`, których w typach nie ma.
4. `from_storage()` to context manager. v1 trzymał klienta w globalu i nigdy nie
   wszedł w `async with`, więc klient nigdy nie był zbudowany.

Jedno v1 miał dobrze: `from_storage(keepalive=…)` jest poprawne, a `.answer`
istnieje. Do tego `.env` było wypełnione pustymi wartościami, a `main()` tylko
ostrzegał i leciał dalej z pustym notatnikiem.

## Dlaczego nie było testów

v1 nie miał żadnego. Napisane zostały **po tym**, jak okazało się, że plik nie
działa — nie wcześniej. To najważniejsza rzecz w tej historii: bramka jakości
pisana po fakcie łapie regresję, ale nie chroni przed tym samym błędem drugi raz.

Dlatego każdy z czterech błędów ma test, a `tests/test_negatywny.py` cofa
poprawki do wersji v1 i **wymaga, by testy się wywróciły**. Bez tego zielony
wynik mógłby oznaczać brak pokrycia, a nie poprawność.

## Lekcje z pisania bramki

Trzy błędy w samej bramce, wszystkie wykryte dopiero po tym, jak złamanie
przeszło:

- **Asercja bez `return`.** Pierwsza wersja nie łapała wycieku nazwy klasy
  wyjątku do czatu. Asercja napisana jako `if ok(...)` bez `return` zawsze jest
  fałszywa, więc blok był pomijany po cichu.
- **Atrapa udająca coś, czego API nie udaje.** Atrapa „złego chat" miała
  `__call__`, przez co test dowodowy udowadniał nic — prawdziwy `ChatAPI` nie jest
  wywoływalny.
- **`write_text` zmieniał zakończenia linii.** Test negatywny przywracał `bot.py`
  z CRLF zamiast LF, więc plik wracał „inny”. Operacje przeniesione na bajty plus
  asercja identyczności pliku.

## Kolejny błąd: fałszywy fakt w notatniku

Po napisaniu źródła `AKTUALIZACJA` bot zaczął odpowiadać, że impreza odbyła się
„30 września”, a miejscem był „Wodzisław Śląski”. Oba zdania były nieprawdziwe.
Źródła mówią:

- termin: **19–23 sierpnia 2026**,
- miejsce: **terenu Rancho Western w Czyżowicach** (Podręcznik Gracza 4.2).

Wodzisław Śląski to najbliższe miasto dla strony pogodowej, która jest jednym ze
źródeł. Źródło zostało przepisane.

Drugi błąd tej samej klasy: napisane „NIE MA infografik, nagrań audio ani wideo".
Tymczasem notatnik ma **5 artefaktów Studio** — 1 audio, 1 wideo, 3 infografiki,
dokładnie o nazwach, które bot wcześniej „wymyślił”. Rozstrzygnęło to
`client.artifacts.list_*`, którego nie sprawdziłem, ograniczając się do
`client.sources.list()`.

Wniosek: **brak danych w jednym API nie jest brakiem danych.** Trzeba sprawdzić
obie ścieżki, zanim napisze się „nie ma tego".

## Poprawka u źródła

Instrukcje zachowania (`system-prompt`, zakaz zapraszania na minione wydarzenie)
przeniesione zostały do źródła `system-prompt`, a nie tylko do `AKTUALIZACJA`.
Powód: NotebookLM nadaje priorytet treści o takim tytule, a to jedyne miejsce,
gdzie da się wymusić zachowanie bez zmiany kodu.

Efekt zmierzony, nie oszacowany — cztery pytania kontrolne, zero trafień w
zakazane frazy, w tym pytanie, które wcześniej dawało „nie zwalniamy tempa z
przygotowaniami do Larp Gothic 2026”.

## Osobny repo

Bot z sekretami stał w `public/`, czyli w webroocie — `.htaccess` blokował `.env`
na LiteSpeed, ale nie na nginxie, a bot **nie ma żadnego interfejsu webowego** i
webroota nie potrzebuje. Kod przeniesiony do osobnego repo, poza webrootem.

---

# 30 września 2026: pięć zielonych bramek i jeden nieistniejący bot

Najważniejsza część tej historii, bo dotyczy błędów, których **nie wykryła żadna
bramka**.

## Fałszywe zielone

**1. `/test` kłamał.** Odpowiadał „gotowy do odpowiedzi", sprawdzając wyłącznie
`klient is not None`. Klient zbudowany wcześniej zostaje w pamięci dokładnie tak
samo po unieważnieniu sesji przez Google. Wykryte przez zrzut ekranu rozmówcy —
nie przez testy.

**2. Powiadomienie o awarii nie zadziałało ani razu.** `get_user()` czyta wyłącznie
cache Discorda i zwraca `None`. Bot jest slash-only i bez `message_content`, więc
cache jest pusty — `get_user` zwracał `None` **zawsze**. Mechanizm zbudowany
po to, żeby powiedzieć właścicielowi o awarii, milczał przy pierwszej awarii.
Testy tego nie wykryły, bo atrapa Discorda w testach miała `get_user` zwracające
użytkownika. **Atrapa była ładniejsza niż produkcja.**

**3. Keepalive raportował zdrowie, którego nie mierzył.** `RotateCookies` co 10
minut zwracał `200 OK` przez 80 minut — w tym czasie sesja była już bezużyteczna.

**4. Życie sesji policzone z odstępu między wykryciami.** Jedna martwa sesja
zgłoszona o10:57 i 11:32 dała „zycie sesji: 0,58 h" — liczbę wyglądającą jak
wiedza o częstotliwości wygasania. Liczy się teraz z pary **logowanie →
wygaśnięcie**, a bez pary raport oddaje `None` **z powodem**.

## Wiszący await i ucięty handler — ten sam objaw

Rozmówca zgłosił „myśli..." bez obsługi błędu. Dwa różne błędy dawały **dokładnie
ten sam objaw**:

| | |
|---|---|
| `chat_timeout` biblioteki to **per-read**, nie całkowity | strumień trzymający połączenie nigdy go nie przekracza |
| handler `/ai-dan` **uwięziony złym wcięciem** | `global` miał wcięcie, przypisanie po nim nie — koniec funkcji |

W obu przypadkach: brak wyjątku, brak logu, wieczne „myśli...".

Rozstrzygnął to skrypt czekający na wynik i czytający log, nie zgadywanie:
`RotateCookies` leciał dalej (pętla żyje), a termin 240 s się nie oglosił — więc
wisiał await **poza** `wait_for`.

Ucięcie powstało **w mojej edycji pliku** i wcześniej rozcięło już `async def
_zbuduj_klienta_nb`. Ten sam błąd dwa razy.

## Wzorzec: złamanie, które niczego nie chroni

Trzy razy w jednej sesji złamanie zgłosiło `BRAK`, bo test sprawdzał **wyciągniętą
jednostkę** zamiast **okablowania**:

1. powiadomienie — atrapa miała działający `get_user`,
2. termin zapytania — test sprawdzał stałą, nie wywołanie `wait_for`,
3. ogon handlera — testy wołały `_zakoncz_interakcje` wprost, a handler mógł
   nie wywoływać go wcale.

Reguła: **złamanie, które nie robi żadnego testu czerwonym, jest złamaniem,
które udaje ochronę.** Każde z nich wymagało oddzielenia logiki od handlerów,
żeby dało się ją przetestować. Ostatnie dwa złamania naprawiały się same —
gdy test robił to samo co kod produkcyjny.

## Czego nie dało się odtworzyć testem

Sesji Google wygasła w **2,5 godziny** od wdrożenia, przy ciasteczkach ważnych
**365 dni**. `auth refresh` odmówił (`Token fetch failed`). Plik był nietknięty.
Google unieważnił sesję po swojej stronie; najbardziej prawdopodobne, że przez
to samo konto używane z dwóch IP — **niepotwierdzone**.

Odnowienie wymaga wpisania hasła i ewentualnego 2FA przez człowieka. **Nie da się
tego zautomatyzować** — i dlatego celem jest nie niezawodność, lecz wykrywalność:
bot zgłasza sam, po jednym wykryciu, a procedura odnowienia jest udokumentowana.

Ścieżka bez haseł (`--browser-cookies` + `rookiepy`) też jest zamknięta, tym razem
nie z powodu polityki, lecz szyfrowania: ciasteczka Chrome 127+ są w formacie
`v20` (app-bound), a klucz ma wyłącznie sam Chrome. Kopia profilu **nie** działa —
Chrome kasuje w niej bazę. Odczyt z oryginalnego katalogu działa, ale wymaga
zamknięcia przeglądarki.