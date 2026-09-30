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