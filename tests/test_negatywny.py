"""Test negatywny bramki ai-dan: cofam poprawke do wersji v1 i sprawdzam,
czy bramka pada. Bez tego zielony wynik moglby znaczyc brak pokrycia, a nie
poprawnosc — dokladnie tak jak w v1, gdzie testow w ogole nie bylo.

WAZNE: operacje na BAJTACH (`read_bytes`/`write_bytes`), nie `read_text`/
`write_text`. Pierwsza wersja uzywala tekstu i przez to `write_text`
przetlumaczyla LF na CRLF — po przywroceniu plik mial inna zawartosc niz
przed testem (sha256 inny, 313 CRLF zamiast LF). Narzedzie nie moze modyfikowac
pliku, ktory testuje.

Przywraca plik w `finally` i sprawdza hash, wiec nie zostawia repo w
zepsutym stanie nawet gdy sam wyloci.
"""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

# bot.py lezy o katalog wyzej; ten test celowo go podmienia, wiec sciezki
# musza byc absolutne wzgledem repo — inaczej odpalony z innego katalogu
# "zlamalby" nieswoj plik albo w ogole nie znalazlby bot.py.
ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot.py"
LIMITS = ROOT / "limits.py"
KOPIA = ROOT / "bot.py.nagatyw"
KOPIA_LIMITS = ROOT / "limits.py.nagatyw"
TEST_BOT = ROOT / "tests" / "test_bot.py"
TEST_LIMITS = ROOT / "tests" / "test_limits.py"
PY = sys.executable


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _posprzataj_cache() -> None:
    """Kasuje `__pycache__` przed kazdym przebiegiem.

    Bez tego bramka potrafi klamac w obie strony. Zlamanie typu `>= 2` na
    `>= 1` ma tyle samo bajtow, a my zapisujemy plik w miejscu — wiec
    `write_bytes` nie uniewaznia skompilowanego bytecode'u, Python uzyje
    starej wersji i zlamanie bedzie niewidoczne. Albo odwrotnie: test
    wywola blad w linii, ktorej w pliku juz nie ma.

    Zaobserwowane w praktyce: zlamanie `len(...) >= 2` na `>= 1` zgloszalo
    `IndexError` z linijki, ktora w pliku byla poprawna.
    """
    for katalog in (ROOT / "__pycache__", ROOT / "tests" / "__pycache__"):
        shutil.rmtree(katalog, ignore_errors=True)


def uruchom_testy(test_plik: Path | None = None) -> tuple[int, str]:
    _posprzataj_cache()
    r = subprocess.run(
        [PY, str(test_plik or TEST_BOT)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=str(ROOT),
    )
    return r.returncode, r.stdout + r.stderr


def verdict(out: str) -> str:
    for linia in out.splitlines():
        if linia.startswith("KONTRAKT:"):
            return linia.split(":", 1)[1].strip()
    return "?"


# ZLAMANIA — kazde musi trafic w konkretny test. Wszystkie stringi na bajtach,
# zeby tresc z PL znakami nie przechodzila przez kodeki.
ZLAMANIA = [
    (
        "v1: client.chat(...) zamiast client.chat.ask(...)",
        b"await klient.chat.ask(\n        notebook_id, pytanie, conversation_id=conversation_id\n    )",
        b"await klient.chat(\n        notebook_id=notebook_id, message=pytanie\n    )",
        "test_uzywa_chat_ask",
    ),
    (
        "v1: message= zamiast question= (klucz obcy dla ask)",
        b"notebook_id, pytanie, conversation_id=conversation_id",
        b"notebook_id, message=pytanie, conversation_id=conversation_id",
        "test_uzywa_chat_ask",
    ),
    (
        "regresja: followup przestaje przekazywac conversation_id",
        b"notebook_id, pytanie, conversation_id=conversation_id",
        b"notebook_id, pytanie",
        "test_followup_przekazuje_conversation_id",
    ),
    (
        "regresja: brak przycinania do limitu Discorda",
        b'tekst = tekst[: LIMIT_DISCORD - 20] + "\\n\xe2\x80\xa6(uci\xc4\x99to)"',
        b"pass",
        "test_przecina_dlugie_odpowiedzi",
    ),
    (
        "regresja: nazwa klasy wyjatku trafia do uzytkownika",
        # kotwica ASCII: sama czesc komunikatu z {exc}. Pelna linia ma emoji,
        # a hardkodowanie jej bajtow wczesniej nie zgadzalo sie z plikiem.
        b'B\xc5\x82\xc4\x85d: {exc}"',
        b'B\xc5\x82\xc4\x85d: {type(exc).__name__}: {exc}"',
        "test_nieznany_bled_pokazuje_sie_dosownie",
    ),
    (
        "regresja: brak konfiguracji nie blokuje startu",
        'log.error("Brak zmiennych środowiskowych: %s", ", ".join(braki))\n        return 1'.encode("utf-8"),
        'log.warning("Brak zmiennych: %s", ", ".join(braki))'.encode("utf-8"),
        "test_brak_konfiguracji_konczy_sie_niezerem",
    ),
    (
        "regresja: odpowiedz z return() zamiast .answer",
        b'odpowiedz = getattr(wynik, "answer", None) or ""',
        b"odpowiedz = str(wynik)",
        "test_uzywa_chat_ask",
    ),
    (
        "regresja: porownanie tytulu wraca do wrazliwego na diakrytyki",
        b"powtorzony = bool(tytul) and _bez_diakrytykow(tytul) in _bez_diakrytykow(tekst)",
        b"powtorzony = bool(tytul) and tytul.strip().lower() in tekst.lower()",
        "test_tytul_czesciowo_w_tresci_nie_powtarzany_bez_znakow_diakrytycznych",
    ),
    (
        "regresja: tytul zrodla doklejany wzdluz cytatu (podwojny napis)",
        b"            powtorzony = bool(tytul) and _bez_diakrytykow(tytul) in _bez_diakrytykow(tekst)",
        b"            powtorzony = False",
        "test_tytul_juz_w_tresci_nie_powtarzany",
    ),
    (
        "regresja: powiadomienie o sesji leci przy kazdym pytaniu (spam)",
        b"    if _powiadomiono_o_sesji:\n        return False",
        b"    if False:\n        return False",
        "test_drugie_powiadomienie_w_tej_samej_incydencie_nie_wychodzi",
    ),
    (
        "regresja: zamkniety DM wlasciciela nie ma kanalu zapasowego",
        b"    if not wys",
        b"    if False:  # kanal zapasowy usuniety\n        if False:",
        "test_dm_wlasciciela_zamkniety_spada_na_zglaszajacego",
    ),
    (
        "regresja: handler konczy sie bez wyslania odpowiedzi",
        b"    await _zakoncz_interakcje(interaction, uid, odpowiedz, cytowania, nowy_cid)",
        b"    global _powiadomiono_o_sesji\n\n_powiadomiono_o_sesji = False\n_licznik: Any = None",
        "test_odpowiedz_dociera_do_uzytkownika",
    ),
    (
        "regresja: zapytanie bez terminu calkowitego wisi w nieskonczonosc",
        b"    return await asyncio.wait_for(\n"
        b"        zapytaj_notebook(klient, notebook_id, tresc, cid),\n"
        b"        timeout=BUDZET_ZAPYTANIA_S if budzet is None else budzet,\n    )",
        b"    return await zapytaj_notebook(klient, notebook_id, tresc, cid)",
        "test_wiszacy_strumien_jest_przerwany",
    ),
    (
        "regresja: powiadomienie o sesji z cache zamiast z API",
        b"            uzytkownik = await bot.fetch_user(id_zglaszajacego)",
        b"            uzytkownik = bot.get_user(id_zglaszajacego)",
        "test_powiadomienie_dziala_gdy_uzytkownik_nie_ma_w_cache",
    ),
    (
        "regresja: wygasla sesja kierowana do restartu uslugi",
        b"    if _blad_polaczenia is not None and czy_blad_sesji(_blad_polaczenia):",
        b"    if _blad_polaczenia is not None and False:",
        "test_wygasla_sesja_mowi_o_loginie_nie_o_restarcie",
    ),
    (
        "regresja: /test zglasza zdrowie bez sprawdzenia sesji",
        b"    return await klient.sources.list(notebook_id)",
        b"    return []",
        "test_martwa_sesja_wywala_wyjatkiem",
    ),
    (
        "regresja: awaria wysylki powiadomienia wywraca bota",
        b"        except Exception as exc:  # noqa: BLE001\n            log.error(\"Nie udalo sie powiadomic uzytkownika",
        b"        except Exception as exc:  # noqa: BLE001\n            raise exc\n        finally:\n            _ = \"Nie udalo sie powiadomic uzytkownika",
        "test_awaria_wysylki_nie_wywraca_bota",
    ),
]

# ZLAMANIA limits.py — licznik limitow. Kazde musi trafic w konkretny test
# test_limits.py. Teza kazdego zlamania: bramka ma zęby, czyli NIE da sie
# przejsc z falszywym limitem, zgadywanym oknem, zgubionym zerowym czasem
# albo wywracajacym sie plikiem.
ZLAMANIA_LIMITS = [
    (
        "regresja: zwykla awaria liczona jako limit",
        b"            if czy_to_limit(status):",
        b"            if True:",
        "test_zwykla_awaria_nie_jest_liczona_jako_limit",
    ),
    (
        "regresja: okno resetu zgadywane zamiast wyznaczone",
        b'"okno_resetu_godziny_max": round(min((g["godziny"] for g in granice), default=0), 2) or None,',
        b'"okno_resetu_godziny_max": 24.0,',
        "test_bez_sukcesu_po_limicie_okno_nieznane",
    ),
    (
        "regresja: falszywe 0 gubi zdarzenie (czas or time.time())",
        b"            Wydarzenie(czas=czas if czas is not None else time.time(), status=\"completed\")",
        b"            Wydarzenie(czas=czas or time.time(), status=\"completed\")",
        "test_zero_jako_czas_nie_gubi_zdarzenia",
    ),
    (
        "regresja: uszkodzony plik wywraca licznik",
        b"        except (json.JSONDecodeError, OSError, UnicodeDecodeError):\n"
        b"            # uszkodzony plik = brak danych, nie awaria\n"
        b"            return",
        b"        except (json.JSONDecodeError, OSError, UnicodeDecodeError):\n"
        b"            raise",
        "test_uszkodzony_plik_nie_wywraca_licznika",
    ),
    (
        "regresja: zadanie w toku zapisywane jako awaria",
        b'    return str(getattr(status, "status", "")) in ("pending", "in_progress")',
        b"    return False",
        "test_stany_w_locie_wymagaja_czekania",
    ),
    (
        "regresja: jedno zdarzenie zapisywane dwa razy",
        b"        to_limit = self.rejestruj_odmowe(typ, status, czas=czas)",
        b"        self.rejestruj_odmowe(typ, status, czas=czas)\n"
        b"        to_limit = self.rejestruj_odmowe(typ, status, czas=czas)",
        "test_awaria_zapisana_dokladnie_raz",
    ),
    (
        "regresja: zycie sesji liczone z odstepu miedzy wykryciami",
        b"            w.czas - self._login_dla(w.czas)\n"
        b"            for w in self.wygasania\n"
        b"            if self._login_dla(w.czas) is not None",
        b"            w.czas - self.wykrycia[-2].czas\n"
        b"            for w in self.wygasania[-1:]\n"
        b"            if len(self.wykrycia) >= 2",
        "test_bez_logowania_nie_ma_zycia",
    ),
    (
        "regresja: powtorne wykrycie liczone jako nowe wygasniecie",
        b"                self.powtorne_wykrycia += 1",
        b"                self.wygasania.append(Wydarzenie(czas=kiedy, status=\"x\"))",
        "test_powtorne_wykrycie_nie_jest_wygasnieciem",
    ),
    (
        "regresja: wygasanie sesji mylone z limitem kwoty",
        b"        if not czy_to_wygasniecie_sesji(wyjatek):\n            return False",
        b"        if False:\n            return False",
        "test_limit_kwoty_to_nie_wygasanie_sesji",
    ),
    ]

print("=== stan przed zlamaniami ===")
przed = BOT.read_bytes()
kod, wyjscie = uruchom_testy()
print("  kod:", kod, "| werdykt:", verdict(wyjscie), "| bot.py:", sha(przed))
stan_dobry = kod == 0
KOPIA.write_bytes(przed)

wyniki = []
for opis, znajdz, podmien, oczekiwany_test in ZLAMANIA:
    if znajdz not in przed:
        wyniki.append({"zlamanie": opis, "wlozono": False, "bramkaPadla": False,
                       "uwaga": "NIE ZNALEZIONO tekstu — zlamanie nie zostalo sprawdzone"})
        continue
    try:
        BOT.write_bytes(przed.replace(znajdz, podmien, 1))
        kod, wyjscie = uruchom_testy()
        wyniki.append({
            "zlamanie": opis, "wlozono": True, "bramkaPadla": kod != 0,
            "wskazanyTestPadl": oczekiwany_test in wyjscie, "oczekiwany": oczekiwany_test,
        })
    finally:
        BOT.write_bytes(przed)

po = BOT.read_bytes()
kod_koniec, wyjscie_koniec = uruchom_testy()
print("=== stan po przywroceniu ===")
print("  kod:", kod_koniec, "| werdykt:", verdict(wyjscie_koniec), "| bot.py:", sha(po))
print("  plik identyczny bajt w bajt:", po == przed)

# --- limits.py: ta sama petla, inny plik i inna bramka ----------------------
print("\n=== limits.py: stan przed zlamaniami ===")
przed_lim = LIMITS.read_bytes()
kod_lim, wyj_lim = uruchom_testy(TEST_LIMITS)
print("  kod:", kod_lim, "| werdykt:", verdict(wyj_lim), "| limits.py:", sha(przed_lim))
stan_dobry_lim = kod_lim == 0
KOPIA_LIMITS.write_bytes(przed_lim)

wyniki_lim = []
for opis, znajdz, podmien, oczekiwany_test in ZLAMANIA_LIMITS:
    if znajdz not in przed_lim:
        wyniki_lim.append({"zlamanie": opis, "wlozono": False, "bramkaPadla": False,
                           "uwaga": "NIE ZNALEZIONO tekstu — zlamanie nie zostalo sprawdzone"})
        continue
    try:
        LIMITS.write_bytes(przed_lim.replace(znajdz, podmien, 1))
        kod, wyjscie = uruchom_testy(TEST_LIMITS)
        wyniki_lim.append({
            "zlamanie": opis, "wlozono": True, "bramkaPadla": kod != 0,
            "wskazanyTestPadl": oczekiwany_test in wyjscie, "oczekiwany": oczekiwany_test,
        })
    finally:
        LIMITS.write_bytes(przed_lim)

po_lim = LIMITS.read_bytes()
kod_lim_koniec, wyj_lim_koniec = uruchom_testy(TEST_LIMITS)
print("=== limits.py: stan po przywroceniu ===")
print("  kod:", kod_lim_koniec, "| werdykt:", verdict(wyj_lim_koniec), "| limits.py:", sha(po_lim))
print("  plik identyczny bajt w bajt:", po_lim == przed_lim)

sprawdzone_lim = [w for w in wyniki_lim if w["wlozono"]]
zlapane_lim = [w for w in sprawdzone_lim if w["bramkaPadla"]]
niesprawdzone_lim = [w for w in wyniki_lim if not w["wlozono"]]
ok_lim = (stan_dobry_lim and len(zlapane_lim) == len(sprawdzone_lim)
          and kod_lim_koniec == 0 and po_lim == przed_lim and not niesprawdzone_lim)

for w in wyniki_lim:
    znacznik = "OK  " if w["bramkaPadla"] else ("BRAK" if w["wlozono"] else "SPROZ")
    print(f"  {znacznik}  {w['zlamanie']}" + (f"  ({w.get('uwaga','')})" if w.get("uwaga") else ""))
KOPIA_LIMITS.unlink(missing_ok=True)

sprawdzone = [w for w in wyniki if w["wlozono"]]
zlapane = [w for w in sprawdzone if w["bramkaPadla"]]
niesprawdzone = [w for w in wyniki if not w["wlozono"]]
ok = (stan_dobry and len(zlapane) == len(sprawdzone) and kod_koniec == 0
      and po == przed and not niesprawdzone and ok_lim)

for w in wyniki:
    znacznik = "OK  " if w["bramkaPadla"] else ("BRAK" if w["wlozono"] else "SPROZ")
    print(f"  {znacznik}  {w['zlamanie']}" + (f"  ({w.get('uwaga','')})" if w.get("uwaga") else ""))

KOPIA.unlink(missing_ok=True)
razem_ok = len(zlapane) + len(zlapane_lim)
razem_sprawdzone = len(sprawdzone) + len(sprawdzone_lim)
print("\nWNIOSEK:", f"bramka ma zęby — łapie {razem_ok}/{razem_sprawdzone} złamań i nie rusza plików"
      if ok else "BRAKI — bramka jest pozorna")
sys.exit(0 if ok else 1)
