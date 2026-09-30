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

## 6. Sesja Google wygasa — i to częściej, niż wynika z dat w ciasteczkach

**Zmierzone 2026-09-30:** sesja padła w **2,5 godziny** od wdrożenia. Wszystkie
wymagane ciasteczka (`SID`, `__Secure-1PSIDTS`) były ważne **kolejne 365 dni**.

| | |
|---|---|
| `auth refresh` (rotacja) | `Token fetch failed` — nie pomaga |
| Plik `storage_state.json` | nieuszkodzony, 58 ciasteczek, ważne |
| Google | odrzucił sesję po swojej stronie |

Najbardziej prawdopodobna przyczyna: **jedno konto używane z dwóch IP** (komputer
+ hosting). Niepotwierdzone. Kontrolny eksperyment (dedykowane konto tylko dla
bota) był odrzucony świadomie, więc ryzyko powtórzenia zostało przyjęte świadomie.

Naprawa wymaga logowania w przeglądarce — interakcji, którą **nie da się
zautomatyzować**: hasło i ewentualne 2FA musi wprowadzić człowiek
(`docs/NOTEBOOKLM.md`). Bot zgłasza awarię sam, po jednym wykryciu.

Częstotliwości nie znamy. Licznik mierzy ją od par **logowanie → wygaśnięcie**
i dopóki nie ma takiej pary, raportuje `zycie_godziny = None` z powodem, zamiast
liczby zmyślonej z odstępu między wykryciami.

## 6a. Automatyczne wykrywanie wygasania — świadomie nie wdrożone

Bot **nie** sprawdza sam, czy sesja żyje. Zgłasza awarię dopiero po pytaniu
(`/ai-dan`) albo po `/test`. Powód: **nie chciałeś** dodatkowej pętli na serwerze.

Koszt byłby znikomy — jeden bool i jedno uwierzytelnione zapytanie co 20 minut —
a efekt: awaria zgłasza się sama, zamiast czekać na `notelm` gracza. Warto wrócić
do tego, gdy kolejna sesja padnie wcześniej, niż zdążysz zauważyć.

Biblioteka daje do tego `notebooklm auth refresh` (rotacja ciasteczek +
zapis na dysk, „one-shot keepalive") — dokumentacja podaje 15–20 minut dla
użytku bezobsługowego. Dwustronne: pilnuje świeżości słoika na dysku i przy
okazji zapisuje ją, czego sam klient w pamięci nie robi przy awarii procesu.

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

## 8a. Nie wiadomo, czy pytania zawsze zwracają odpowiedź

Rozmówca potwierdził działanie 2026-09-30 o 14:56, ale **jedna** potwierdzona
odpowiedź to nie pomiar. Brakuje danych o tym, jak często NotewortLM zwraca
`200 OK`, a nie zostawia otwartego strumienia (patrz `docs/HISTORIA.md`).

Bot ma na to zabezpieczenie: `BUDZET_ZAPYTANIA_S` (domyślnie 240 s) przerywa
zapytanie i mówi wprost, że odpowiedź nie dotarła. Nie zgadujemy, czy to limit
kwoty — to zdarzenie nie trafia do licznika.

Kolejne pytania sprawdzą, czy zabezpieczenie w ogóle jest potrzebne. Bez tego
powtarzania objawu nie da się odróżnić od zwykłej wolnej odpowiedzi.

## 8b. Limitów kwoty jeszcze nie znamy

Licznik działa, ale **nie zmierzył jeszcze ani jednego limitu**. Stan na
2026-09-30: 5 sukcesów zasilonych z istniejących artefaktów (audio 1, wideo 1,
infografiki 3 — wszystkie z tej samej sekundy), 0 limitów, 0 wygaśnięć sesji
w nowym schemacie.

Raport **mówi wprost, że okna nie zna**, zamiast podawać liczbę zgadnutą
z pojedynczego zdarzenia. Wyznaczenie okna wymaga pary limit → następny sukces.

## 9. Nie ma watchdoga

Bot odpalony przez `daemon.py` działa, dopóki serwer nie zrestartuje się albo
proces nie padnie. **Po restarcie hostingu trzeba `daemon.py start`.**

Pętla nadzorcy w Pythonie (fork → czekaj na wyjście → restart po 10 s)
zastąpiłaby to kilkunastoma linijkami i działałaby bez `cron` i bez `systemd`,
których na CloudLinux nie ma. Świadomie jej nie ma — dodana na żądanie,
gdy będzie potrzebna.

## 10. `OWNER_USER_ID` pusty domyślnie

Powiadomienie o wygasłej sesji idzie do osoby, która zadała pytanie i dostała
błąd. Bez `OWNER_USER_ID` nikt nie dostanie powiadomienia, jeśli nikt akurat nie
zapyta. Wystarczy wpisać ID właściciela w `.env`, żeby dostać DM zawsze.