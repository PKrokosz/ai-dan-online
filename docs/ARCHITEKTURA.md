# Architektura

## Przepływ

```
Użytkownik -> /ai-dan -> discord.py -> bot.zapytaj_notebook()
                                             |
                                             v
                                   NotebookLMClient (from_storage)
                                             |
                                             v
                              POST /_/LabsTailwindUi/data/batchexecute
                                   (chat.ask, rLM1Ne / GenerateFreeFormStreamed)
                                             |
                                             v
                                    AskResult.answer + references[]
                                             |
                                             v
                              bot.zbuduj_widok() -> wiadomość na Discord
```

Bot nie ma bazy danych, nie trzyma stanu między restartami i nie generuje
odpowiedzi sam — cała treść pochodzi z notatnika.

## Pliki

| Plik | Rola |
|---|---|
| `bot.py` | Jedyny moduł produkcyjny: komendy, klient NotebookLM, renderowanie |
| `run.py` | Wczytuje `.env` w procesie i uruchamia `bot.py` |
| `tests/test_bot.py` | 20 asercji kontraktu, bez sieci, logowania i tokenu |
| `tests/test_negatywny.py` | Cofa poprawki do wersji zepsutej i wymaga, by bramka padła |

`bot.py` celowo nie wie, skąd bierze sekretów — czyta `os.getenv`. Dzięki temu
testy importują go bez żadnej konfiguracji.

## Kontrakt z `notebooklm-py` 0.7.2

Zweryfikowany odczytem sygnatur, nie z dokumentacji. Wersje są przypięte
w `requirements.txt`, bo to nazwy wewnętrzne, a nie publiczne API.

```python
client = await NotebookLMClient.from_storage().__aenter__()   # context manager
wynik   = await client.chat.ask(notebook_id, pytanie, conversation_id=cid)
```

`client.chat` jest **namespace**, a nie funkcją. Wywołanie `client.chat(...)`
kończy się `TypeError`.

`AskResult` ma pola: `answer`, `conversation_id`, `turn_number`, `is_follow_up`,
`references`, `raw_response`. Cytowania to lista obiektów `ChatReference`
z `source_id`, `citation_number`, `cited_text`, `start`/`end`, `chunks`.

Wersja v1 wywoływała `client.chat(...)`, przekazywała nieistniejące
`message=` i `timeout=`, czytała nieistniejące `cited_sources` i trzymała klienta
w globalu bez `async with`. Każdy z tych punktów ma test, a bramka negatywna
potwierdza, że test **padnie**, gdy się do nich wróci.

## Granice testowalności

`bot.py` dzieli się na dwie warstwy, żeby testy nie potrzebowały sieci:

- **`zapytaj_notebook(klient, notebook_id, pytanie, conversation_id)`** —
  jedyne miejsce, które dotyka klienta. Testy podstawiają atrapę i sprawdzają
  kontrakt: wywołane `chat.ask`, przekazane `conversation_id`, czytane `.answer`
  i `.references`.
- **`zbuduj_widok(odpowiedz, cytowania, tytuly)`** — czysta funkcja tekstowa.
  Testy sprawdzają przycinanie do 2000 znaków, pomijanie pustych cytowań
  i doklejanie tytułu źródła.

Atrapa klienta w testach **nie** udaje `ChatAPI` przez `__call__` —
prawdziwy obiekt też nie jest wywoływalny, a atrapa, która jest, dowodziłaby
niczego. Stąd test `test_stara_metoda_ladnie_wybucha`: stara metoda musi zgłosić
błąd zamiast przejść po cichu.

## Renderowanie cytowań

```
[numer] <cytowany tekst> — <tytuł źródła>
```

Tytuł doklejany jest tylko wtedy, gdy **nie ma go już w cytowanym tekście**.
Przy źródłach tekstowych cytowany fragment to bywa nagłówek sekcji, czasem
cały tytuł — bez tego sprawdzenia czytelnik widział `TYTUL — TYTUL`. Porównanie
jest odporne na diakrytyki i wielkość liter (`_bez_diakrytykow`), bo NotebookLM
potrafi zwrócić tytuł z diakrytykami, a fragment bez nich.

## Osobowość bota nie jest w kodzie

`system-prompt` i `AKTUALIZACJA` są zwykłymi źródłami w notatniku. NotebookLM nie
ma pola „system prompt” przez API — priorytet daje treść i tytuł źródła, nie
jego pozycja. Dlatego zachowanie bota zmienia się **bez restartu i bez deployu**.

Limity są w kodzie, bo są właściwością transportu, a nie treści:
`LIMIT_DISCORD = 2000` (limit Discorda), `MAX_PODRZEDKOW = 50` (pamięć procesu),
`MAX_ZRODL_CYTOWANIA = 400` (długość cytatu).

## Synchronizacja komend

Bot używa `tree.sync()`, czyli synchronizacji **globalnej**. Globalne komendy
Discord rozsyła według własnego harmonogramu i mogą nie pojawić się w serwerze
przez długi czas. W `.env.example` jest `DISCORD_GUILD_ID` jako miejsce na
synchronizację do gildi (patrz `docs/ZNANE-PROBLEMY.md`).