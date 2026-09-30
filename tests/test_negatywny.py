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
KOPIA = ROOT / "bot.py.nagatyw"
TEST_BOT = ROOT / "tests" / "test_bot.py"
PY = sys.executable


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def uruchom_testy() -> tuple[int, str]:
    r = subprocess.run(
        [PY, str(TEST_BOT)], capture_output=True, text=True,
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
        "regresja: awaria wysylki powiadomienia wywraca bota",
        b"        except Exception as exc:  # noqa: BLE001\n            log.error(\"Nie udalo sie powiadomic uzytkownika",
        b"        except Exception as exc:  # noqa: BLE001\n            raise exc\n        finally:\n            _ = \"Nie udalo sie powiadomic uzytkownika",
        "test_awaria_wysylki_nie_wywraca_bota",
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

sprawdzone = [w for w in wyniki if w["wlozono"]]
zlapane = [w for w in sprawdzone if w["bramkaPadla"]]
niesprawdzone = [w for w in wyniki if not w["wlozono"]]
ok = stan_dobry and len(zlapane) == len(sprawdzone) and kod_koniec == 0 and po == przed and not niesprawdzone

for w in wyniki:
    znacznik = "OK  " if w["bramkaPadla"] else ("BRAK" if w["wlozono"] else "SPROZ")
    print(f"  {znacznik}  {w['zlamanie']}" + (f"  ({w.get('uwaga','')})" if w.get("uwaga") else ""))

KOPIA.unlink(missing_ok=True)
print("\nWNIOSEK:", f"bramka ma zęby — łapie {len(zlapane)}/{len(sprawdzone)} złamań i nie rusza pliku"
      if ok else "BRAKI — bramka jest pozorna")
sys.exit(0 if ok else 1)
