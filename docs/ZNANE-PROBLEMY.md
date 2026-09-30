# Znane problemy

Ograniczenia, z którymi bot żyje. Nic tu nie jest naprawione — dlatego
dokument istnieje.

## 1. `auth check` nie jest tym samym co `login`

`notebooklm login` potrafi zakończyć się komunikatem `Login not detected within 5
minutes` przy **aktywnej** sesji. Powód: CLI czeka na `notebooklm.google.com`,
a NotebookLM przekierowuje na `notebook.google.com`. Wiarygodnym testem jest:

```bash
notebooklm auth check --test --json     # status: ok + token_fetch: true
```

Nie ma to wpływu na bota — `from_storage()` korzysta z pliku sesji, nie z `login`.

## 2. Słowo „dzisiaj” wciąż się pojawia

Źródło `system-prompt` zakazuje pytań typu „co dzisiaj bierzemy na warsztat”.
Bot **nadal** zdarza się zakończyć odpowiedź zdaniem „co masz ochotę dzisiaj
wziąć na warsztat?".

Treściowo zdanie dotyczy rozmowy, nie wydarzenia — i w ostatnim canary nie
trafiło w zakazane frazy. Ale to ta sama konstrukcja, która irytowała na
początku, więc nie jest wyeliminowana, tylko złagodzona.

Sposób ostateczny: zakaz używania słowa „dzisiaj” w ogóle, z zamianą na
„co chcesz omówić”. Wymaga przepisania `system-prompt` (delete + add_text),
więc nie zrobione bez świadomej decyzji.

## 3. Brak resetu rozmowy

Bot przekazuje `conversation_id` dla słów `dalej` / `cd` / `więcej`, żeby
kontynuacja nie powtarzała kontekstu. Nie ma odwrotności: raz nadany kontekst
zostaje w konwersacji i wpływa na kolejne pytania tego samego wątku.

Prosty wariant naprawy: nowy wątek per pytanie albo komenda `/ai-dan nowa`
zrywająca kontekst. Wymaga zmiany w `bot.py` i testu kontraktu.

## 4. Synchronizacja komend jest globalna

`tree.sync()` bez argumentu synchronizuje **globalnie**. Globalne komendy Discord
rozsyła według własnego harmonogramu — mogą nie pojawić się w serwerze przez długi
czas mimo potwierdzenia w logach (`Zsynchronizowano 2 komend`).

W `.env.example` jest `DISCORD_GUILD_ID` jako miejsce na synchronizację do gildi.
W praktyce jednoserwerowy bot powinien synchronizować do gildi: pojawia się
natychmiast. Nie zrobione, bo to odstaje od zachowania wersji odtworzonej.

## 5. Artefakty Studio nie generują cytowań

Odpowiedź oparta na artefakcie wygląda jak odpowiedź bez źródeł. Użytkownik nie
odróżni „nie mam źródeł" od „nie chcę cytować". Nie da się tego zmienić z API.

## 6. Sesja Google wygasa

Profil trzyma ciasteczka z ważnością kilku tygodni. Po wygaśnięciu bot dostaje
`AuthError`, a naprawa wymaga logowania w przeglądarce — interakcji, któriej
nie da się zautomatyzować na maszynie z zablokowanym `cargo`
(`docs/NOTEBOOKLM.md`).

## 7. Dwa boty o tej samej nazwie

W ekosystemie są dwa boty „Ai-Dan”:

| | ID | Po co |
|---|---|---|
| Ten repo | `1484613933737312278` | odpowiada z notatnika NotebookLM |
| Ról w Larp Gothic | `1440668594378772682` | nadaje role na Discordzie |

Pomylenie ich przy weryfikacji daje wyniki z niewłaściwego źródła.

## 8. Ostrzeżenia o braku PyNaCl i davey

Bot nie używa głosu, ale `discord.py` ostrzega przy starcie. Opcjonalne
zależności są opisane w `requirements.txt` — instalacja ich wycisza log.