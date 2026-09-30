# NotebookLM — logowanie i potwierdzone pułapki

Wszystko w tym pliku zostało sprawdzone na `notebooklm-py` 0.7.2,
Windows, Python 3.14. Bez zgadywania.

## Ścieżka profilu

CLI używa **profilu per konto**, nie katalogu domowego:

```
%USERPROFILE%\.notebooklm\profiles\default\storage_state.json
```

Poziom niżej, `%USERPROFILE%\.notebooklm\storage_state.json`, **nie istnieje**.
Sprawdzanie sesji w złym miejscu daje fałszywe „brak pliku" i prowadzi do
poszukiwań nie tam, gdzie trzeba.

## Logowanie

```bash
notebooklm login --browser chrome
notebooklm auth check --test --json
```

`--browser chrome` używa systemowego Google Chrome i omija pobieranie Chromium
przez Playwrighta (~170 MB). Bez tego flaga CLI pada z komunikatem o braku
pliku wykonywalnego Playwrighta.

**Proces czeka 5 minut** i wykrywa zalogowanie, dopiero gdy strona jest już na
domenie NotebookLM. Samo zalogowanie do Google nie wystarcza — CLI kończy wtedy
pracę komunikatem `Login not detected within 5 minutes`.

### Pułapka: biblioteka czeka na złą domenę

W CLI jest:

```python
DEFAULT_BASE_URL = "https://notebooklm.google.com"   # _env.py:17
```

a `login` robi:

```python
page.goto(f"{get_base_url()}/")                       # playwright_login.py:798
page.wait_for_url(f"{get_base_url()}/**", ...)        # :857
```

NotebookLM w praktyce **odpowiada przekierowaniem 301 na `notebook.google.com`**,
więc `wait_for_url` nigdy się nie spełnia i logowanie zgłasza brak, mimo aktywnej
sesji. Widoczne to także w logach przy normalnym pytaniu:

```
GET https://notebooklm.google.com/  301 Moved Permanently
GET https://notebook.google.com/     200 OK
```

`auth check --test` w tym momencie i tak zwraca `status: ok` — to on, a nie
`login`, jest wiarygodnym testem sesji.

## Odtwarzanie sesji bez okna

Gdy `login` nie działa, a profil przeglądarki ma już ciasteczka, sesję można
zrzucić przez Playwrighta i zapisać `storage_state.json` ręcznie:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        USER_DIR, channel="chrome", headless=True)
    ctx.storage_state()   # -> storage_state.json
```

Wykryte w ten sposób: `FINAL_HOST notebook.google.com` przy poprawnej sesji —
potwierdzenie powyższego rozdziału.

## Dlaczego nie `--browser-cookies`

```bash
notebooklm login --browser-cookies chrome
```

Wymaga `rookiepy`, które buduje się **z Rustem**. Na tej maszynie `cargo`
blokują zasady kontroli aplikacji Windows (`OSError: [WinError 4551]`), więc
instalacja kończy się błędem i import ciasteczek z zainstalowanej przeglądarki
nie jest dostępny. Na Pythonie 3.14 nie ma też gotowego koła.

## Źródła a artefakty — to nie jest to samo

To rozróżnienie jest ważniejsze, niż się wydaje:

| | `client.sources.list()` | `client.artifacts.list_*()` |
|---|---|---|
| Co to | dokumenty w notatniku | wygenerowane opracowania Studio |
| Dostępne przez API | `sources.list` | `artifacts.list_audio/list_video/list_infographic/…` |
| Cytowania w odpowiedzi | tak | **nie** |

Artefakty **nie generują cytowań**. Odpowiedź oparta na artefakcie wygląda jak
odpowiedź bez źródeł, więc „zero cytowań" nie oznacza „wymyślone".

Ten notatnik ma 11 źródeł i 5 artefaktów (1 audio, 1 wideo, 3 infografiki).

## Dodawanie i usuwanie źródeł

```python
zrodlo = await client.sources.add_text(notebook_id, tytul, tresc, wait=True)
await client.sources.wait_until_ready(notebook_id, zrodlo.id, timeout=180)
```

- **Źródła tekstowe nie mają dedupe po stronie serwera.** Ponowienie wywołania
  tworzy drugą kopię. Sprawdzaj tytuł przed dodaniem.
- `add_text(..., wait=True)` czeka na indeksowanie. Bez tego pytania przez
  kilka minut nie uwzględniają nowego źródła.
- **Nie ma edycji treści źródła.** Są tylko `rename` i `refresh`. Zmiana treści
  = `add_text` nowego + `delete` starego. Bezpieczna kolejność: dodaj nowe →
  zweryfikuj treść z serwera → dopiero potem usuń stare. Wtedy błąd w trakcie
  nie zostawia notatka bez treści.
- Artefakty to nie źródła: dodanie ich przez API to `artifacts.generate_*`,
  nie `sources.add_*`.

## Ograniczenia, których API nie zgłasza

- `chat.ask` nie przyjmuje systemowego promptu. Priorytet daje treść i tytuł
  źródła, nie pozycja na liście. Stąd instrukcje trzymamy w źródle `system-prompt`.
- Nie ma sposobu na „reset rozmowy" z zewnątrz poza nieprzekazaniem
  `conversation_id` — kontynuacja jest jedynym mechanizmem, a brak kontekstu
  utrzymuje się między pytaniami w obrębie konwersacji.
- Odpowiedzi potrafią odwoływać się do artefaktów z nazwy, których bot nie
  cytuje. Dla użytkownika wygląda to jak odpowiedź bez źródeł.