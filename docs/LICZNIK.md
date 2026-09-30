# Licznik limitów NotebookLM

## Po co

Limitów kwoty **nie da się odczytać z API**. Biblioteka nie zwraca stanu
licznika, nie ma endpointu „ile mi zostało". Da się tylko obserwować, co się
dzieje: zadanie wystartowało albo Google odmówił.

Stąd licznik zlicza **zdarzenia** i wyznacza z nich **granice** — nie dokładne
liczby. Granica jest uczciwa; zgadywanie liczby nie.

## Co mierzy

| Pole | Znaczenie |
|---|---|
| `ok` | zadania wystartowały (albo istniejące artefakty wczytane z `created_at`) |
| `limity` | odmowy kwoty rozpoznane po `error_code` / treści błędu |
| `awarie` | pozostałe niepowodzenia — **nie są** limitami |
| `ok od lim` | sukcesy od ostatniego limitu → dolna granica limitu |
| `okno<=h` | górna granica okna resetu, z pary limit → pierwszy sukces po nim |
| `znane` | czy para istnieje. `nie` = **nie wiemy**, i tak jest w raporcie |

Stan w `limits.json` (`0600`, runtime, poza repo).

## Czego nie mierzy — i dlaczego to ważne

**Nie podaje liczby, której nie umie zmierzyć.** Bez pary limit → następny
sukces raport pokazuje `znane: nie` zamiast wartości zmyślonej z pojedynczego
zdarzenia.

To nie jest ostrożność stylistyczna. Pierwsza wersja raportowała
`zycie sesji: 0,58 h` dla jednej martwej sesji zgłoszonej dwa razy — różnica
między wykryciami wyglądała jak wiedza o częstotliwości wygasania. Liczby,
które wyglądają jak wiedza, ale nią są, są groźniejsze niż brak liczby.

## Rozróżnienie, o które prosiłem

Limity kwoty i wygasanie sesji mają **różne skale**:

| | reset | zdarzenie |
|---|---|---|
| limit kwoty | godziny | `generate_*` zwraca `failed` + kod błędu |
| sesja Google | tygodnie | `from_storage()` pada **zanim** powstanie zadanie |

W jednym worku wyznaczanie okna byłoby śmieciowe. Sesje mają osobny cykl życia:
`logowania → wygasania`, z liczbą **powtórnych wykryć** osobno — bo po restarcie
daemona bot znów próbuje i znów dostaje ten sam błąd, a to **nie** jest drugie
wygasanie.

## Użycie

```bash
python tools/limits_probe.py --sprawdz          # sesja żyje + stan licznika
python tools/limits_probe.py --zasil            # wczytaj artefakty (koszt 0)
python tools/limits_probe.py --odczyt           # sam raport
python tools/limits_probe.py --probe audio      # JEDNO generowanie
```

`--zasil` jest darmowy: korzysta z `Artifact.created_at` istniejących
artefaktów. Nie generuje niczego, więc nie zjada limitu.

`--probe` **zjada limit**. Jedno uruchomienie = jedno zdarzenie.

## Stan na 30.09.2026

```
audio                 1       0        0          1         -  nie
video                 1       0        0          1         -  nie
infographic           3       0        0          3         -  nie
```

5 sukcesów z istniejących artefaktów, **0 zmierzonych limitów**. Okno resetu
nieznane — brak pary.

Trzy infografiki mają **identyczną sekundę** utworzenia, co sugeruje generowanie
hurtem. Pierwsza wskazówka o charakterze limitów, jaka w ogóle mamy.

## Trzy błędy, które tu naprawiłem

Wszystkie zniekształcałyby wyznaczone granice — czyli to, po co licznik istnieje.

1. **`generate_*` zwraca `pending`, nie `completed`.** Kod czekał tylko na
   `completed`, więc `wait_for_completion` nigdy nie był wołany, a zadanie w
   toku leciało do „awarie". Każdy pomiar kłamałby.
2. **Limit przychodzący w trakcie generowania był zapisywany jako sukces.**
   `wait_for_completion` zwraca końcowy status — kod go ignorował. Wyznaczane
   okno miałoby sens tylko dla zdanego limitu, którego nie było.
3. **`rejestruj_odmowe(...)` było wywołane dwa razy**, a zapisuje zawsze
   (do odmów albo awarii) i tylko zwraca, czy to limit. Jedna awaria dawała
   dwa zdarzenia; licznik awarii rósł dwukrotnie szybciej.

## Powtórne wykrycia i pierwsze wykrycie

Rozróżnienie subtelne, ale zmienia liczby:

- **pierwsze** wykrycie w zapisanej historii jest **wygasaniem**, nawet gdy nie
  znamy początku — sesja mogła zacząć się, zanim w ogóle zapisaliśmy; zapisujemy
  je **bez znanego początku**, więc nie wydłuża życia i nie ginie z historii,
- **kolejne** wykrycie bez nowego logowania to **ta sama martwa sesja** razem.

## Migracja pliku

Stary układ (płaska lista zdarzeń bez informacji, co czym jest) traktowany jest
jako `wykrycia`, a `wygasania` zostaje puste. **Nie wymyślamy historii, której
nie znamy** — dlatego po migracji życie sesji nadal wynosi `None`, tym razem
z prawdziwym powodem zamiast fałszywej liczby.