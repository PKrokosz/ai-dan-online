#!/usr/bin/env python3
"""Bramka kontraktu licznika limitow.

Licznik jest telemetria, wiec najwazniejsze jest to, czego **nie** robi:
nie zapisuje zgadywki jako faktu, nie liczy zwyklej awarii jako limitu i nie
wywraca sie na uszkodzonym pliku.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import limits
from limits import LicznikLimitow, czy_to_limit, czy_wymaga_oczekiwania

H = 3600.0


class AtrapaStatus:
    def __init__(self, status="completed", error_code=None, error=None):
        self.status = status
        self.error_code = error_code
        self.error = error


def BLED_SESJI() -> ValueError:
    """Realny komunikat biblioteki — rozpoznawany przez `czy_to_wygasniecie_sesji`."""
    return ValueError(
        "Authentication expired or invalid. Redirected to: https://accounts.google.com/x"
        " Run 'notebooklm login' to re-authenticate."
    )


class TestRozpoznawanieLimitu(unittest.TestCase):
    def test_kod_strukturalny_rozpoznany(self):
        self.assertTrue(czy_to_limit(AtrapaStatus("failed", error_code="USER_DISPLAYABLE_ERROR")))

    def test_tekst_rozpoznany_bez_kodu(self):
        for tekst in ["Rate limit reached", "Quota exceeded for today", "limit exceeded"]:
            with self.subTest(tekst=tekst):
                self.assertTrue(czy_to_limit(AtrapaStatus("failed", error=tekst)))

    def test_removed_to_znaczy_limit(self):
        # Google potrafi po cichu zdjac artefakt po odmowie
        self.assertTrue(czy_to_limit(AtrapaStatus("removed", error_code="USER_DISPLAYABLE_ERROR")))

    def test_zwykla_awaria_to_nie_limit(self):
        for st, kod, tekst in [
            ("failed", "INTERNAL", "NullPointerException"),
            ("failed", None, "notebook not found"),
            ("failed", None, None),
            ("completed", None, None),
            ("in_progress", None, None),
            ("pending", None, None),
        ]:
            with self.subTest(st=st, kod=kod, tekst=tekst):
                self.assertFalse(czy_to_limit(AtrapaStatus(st, kod, tekst)))

    def test_pending_nie_jest_odmowa_nawet_z_quota_w_tekscie(self):
        # Dopasowanie tekstu dziala tylko dla failed/removed — inaczej
        # komunikat z opisem czekajacego zadania bylby liczony jako limit
        self.assertFalse(czy_to_limit(AtrapaStatus("pending", None, "waiting for quota")))


class TestZapisWyniku(unittest.TestCase):
    """Rozstrzyganie limitu od awarii na KONCOWYM statusie.

    W oryginale `if rejestruj_odmowe(...)` bylo wywolane dwa razy, a
    `rejestruj_odmowe` zapisuje zawsze (do odmow albo awarii) i tylko
    zwraca, czy to limit — jedna awaria dawala dwa zdarzenia.
    """

    def setUp(self):
        self.katalog = tempfile.mkdtemp()
        self.licznik = LicznikLimitow(Path(self.katalog) / "limits.json")

    def test_completed_jest_sukcesem(self):
        to_limit, opis = self.licznik.zapisz_wynik("audio", AtrapaStatus("completed"))
        self.assertFalse(to_limit)
        self.assertEqual(opis, "sukces")
        self.assertEqual(self.licznik.raport("audio")["sukcesy"], 1)

    def test_limit_zapisany_jako_odmowa_nie_awaria(self):
        st = AtrapaStatus("failed", error_code="USER_DISPLAYABLE_ERROR", error="quota exceeded")
        to_limit, opis = self.licznik.zapisz_wynik("audio", st)
        self.assertTrue(to_limit)
        self.assertEqual(opis, "LIMIT KWOTY")
        r = self.licznik.raport("audio")
        self.assertEqual((r["limity"], r["awarie_inne"]), (1, 0))

    def test_awaria_zapisana_dokladnie_raz(self):
        st = AtrapaStatus("failed", error_code="INTERNAL", error="kapot")
        to_limit, opis = self.licznik.zapisz_wynik("audio", st)
        self.assertFalse(to_limit)
        self.assertEqual(opis, "awaria (nie limit)")
        self.assertEqual(self.licznik.raport("audio")["awarie_inne"], 1)

    def test_wyjatke_oczekiwania_to_awaria_nie_limit(self):
        to_limit, _ = self.licznik.zapisz_wynik("audio", RuntimeError("timeout"))
        self.assertFalse(to_limit)
        self.assertEqual(self.licznik.raport("audio")["awarie_inne"], 1)

    def test_pending_nie_jest_sukcesem(self):
        # pending to nie wynik — zapisany jako sukcz zglosilby sukces tam,
        # gdzie zadanie dopiero trwa, i wyszedlby absurdalny limit=0
        _, opis = self.licznik.zapisz_wynik("audio", AtrapaStatus("pending"))
        self.assertNotEqual(opis, "sukces")
        self.assertEqual(self.licznik.raport("audio")["sukcesy"], 0)


class TestOczekiwanieWyniku(unittest.TestCase):
    def test_stany_w_locie_wymagaja_czekania(self):
        for stan in ("pending", "in_progress"):
            with self.subTest(stan=stan):
                self.assertTrue(czy_wymaga_oczekiwania(AtrapaStatus(stan)))

    def test_stany_koncowe_nie_wymagaja_czekania(self):
        for stan in ("completed", "failed", "removed", "not_found"):
            with self.subTest(stan=stan):
                self.assertFalse(czy_wymaga_oczekiwania(AtrapaStatus(stan)))


class TestZycieSesji(unittest.TestCase):
    """Zycie sesji liczone WYLACZNIE z par logowanie -> wygasniecie.

    Wczesniejsza wersja liczyla roznice miedzy kolejnymi wykryciami. Dla
    jednej martwej sesji zgloszonej dwa razy raportowala "zycie 0,58 h",
    co wygladalo jak wiedza o czestotliwosci wygasania, a bylo odstepem
    miedzy podejrzeniami.
    """

    def setUp(self):
        self.katalog = tempfile.mkdtemp()
        self.plik = Path(self.katalog) / "limits.json"
        self.licznik = LicznikLimitow(self.plik)

    def test_bez_logowania_nie_ma_zycia(self):
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1268.0)
        r = self.licznik.raport_sesji()
        self.assertIsNone(r["zycie_godziny"], "zycie policzone bez logowania")
        self.assertIn("brak pary", r["zycie_powod"])

    def test_powtorne_wykrycie_nie_jest_wygasnieciem(self):
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1268.0)
        r = self.licznik.raport_sesji()
        self.assertEqual(r["wykrycia"], 2)
        # pierwsze wykrycie to prawdziwe wygasniecie (nieznamy tylko poczatku),
        # drugie to ta sama martwa sesja zgloszona ponownie
        self.assertEqual(r["wygasania"], 1)
        self.assertEqual(r["powtorne_wykrycia"], 1)
        self.assertIsNone(r["zycie_godziny"])

    def test_zycie_z_pary_logowanie_wygasniecie(self):
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 4 * H)
        r = self.licznik.raport_sesji()
        self.assertEqual(r["wygasania"], 1)
        self.assertEqual(r["zycie_godziny"], 4.0)
        self.assertIn("mediana", r["zycie_powod"])

    def test_drugie_wygasniecie_domyka_powtorne_wykrycie(self):
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 4 * H)
        # ta sama martwa sesja zgloszona drugi raz — po restarcie daemona
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 4 * H + 600)
        self.assertEqual(self.licznik.raport_sesji()["powtorne_wykrycia"], 1)
        # nowe logowanie i kolejne wygasniecie — to juz kolejna sesja
        self.licznik.rejestruj_sesje_zywa(czas=1000.0 + 6 * H)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 10 * H)
        r = self.licznik.raport_sesji()
        self.assertEqual(r["wygasania"], 2)
        self.assertEqual(r["zycie_godziny"], 4.0)  # mediana z 4 h i 4 h

    def test_zywa_sesja_nie_jest_wygasnieciem(self):
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        r = self.licznik.raport_sesji()
        self.assertIsNone(r["zycie_godziny"])
        self.assertEqual(r["wygasania"], 0)
        self.assertIn("jeszcze zyje", r["zycie_powod"])

    def test_stary_plik_z_plaska_lista_nie_daje_zycia(self):
        self.plik.write_text(json.dumps({
            "wersja": 1,
            "sesje": [{"czas": 1000.0, "status": "expired"},
                      {"czas": 1268.0, "status": "expired"}],
            "typy": {},
        }), encoding="utf-8")
        licznik = LicznikLimitow(self.plik)
        r = licznik.raport_sesji()
        self.assertIsNone(r["zycie_godziny"], "stary plik bez logowan dal zycie")
        self.assertEqual(r["wykrycia"], 2)
        # migracja NIE wymysla wygasan: wpis sprzed wdrozenia mogl byc czymkolwiek
        self.assertEqual(r["wygasania"], 0)

    def test_sygnatura_nie_sesji_nie_dotyka_sesji(self):
        self.assertFalse(self.licznik.rejestruj_wygasniecie_sesji(ValueError("coś innego")))
        self.assertEqual(self.licznik.raport_sesji()["wykrycia"], 0)

    def test_przezycie_po_reloadzie_pliku(self):
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 3 * H)
        wczytany = LicznikLimitow(self.plik)
        r = wczytany.raport_sesji()
        self.assertEqual(r["logowania"], 1)
        self.assertEqual(r["wygasania"], 1)
        self.assertEqual(r["zycie_godziny"], 3.0)


class TestOdwiezanieNieZasmieczaLicznika(unittest.TestCase):
    """Petla odwiezania co 15 min nie moze mnozyc logowan.

    Wykryte testem integracyjnym 30.09: 3 rundy odwiezenia zapisaly
    3 "logowania". Przy 96 rundach dziennie licznik pokazalby 96 logowan
    z jednego, a zycie sesji liczyloby sie od ostatniej rundy — czyli
    od czegos, co nie jest poczatkiem sesji.
    """

    def setUp(self):
        self.katalog = tempfile.mkdtemp()
        self.plik = Path(self.katalog) / "limits.json"
        self.licznik = LicznikLimitow(self.plik)

    def test_powtorzone_odwiezenie_to_jedno_logowanie(self):
        zapisane = [self.licznik.rejestruj_sesje_zywa(czas=1000.0) for _ in range(5)]
        self.assertTrue(zapisane[0], "pierwsze musi zapisac")
        self.assertFalse(any(zapisane[1:]), "kolejne nie moga zapisywac")
        self.assertEqual(self.licznik.raport_sesji()["logowania"], 1)

    def test_po_wygasnieciu_nowe_logowanie_to_nowe_zdarzenie(self):
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 2 * H)
        self.assertTrue(self.licznik.rejestruj_sesje_zywa(czas=1000.0 + 3 * H))
        self.assertEqual(self.licznik.raport_sesji()["logowania"], 2)
        self.assertEqual(self.licznik.raport_sesji()["wygasania"], 1)

    def test_zycie_nie_liczy_sie_od_ostatniej_rundy(self):
        # 96 odwiezen w ciagu jednej sesji — zycie musi zostac liczone
        # od pierwszego logowania, nie od ostatniego
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        for i in range(1, 97):
            self.licznik.rejestruj_sesje_zywa(czas=1000.0 + i * 900.0)
        self.licznik.rejestruj_wygasniecie_sesji(BLED_SESJI(), czas=1000.0 + 2 * H)
        r = self.licznik.raport_sesji()
        self.assertEqual(r["logowania"], 1)
        self.assertEqual(r["zycie_godziny"], 2.0)

    def test_przezycie_przeciezenia_czasu(self):
        self.licznik.rejestruj_sesje_zywa(czas=1000.0)
        nowy = LicznikLimitow(self.plik)
        self.assertFalse(nowy.rejestruj_sesje_zywa(czas=2000.0),
                         "po reloadzie tez nie moze dopisac")
        self.assertEqual(nowy.raport_sesji()["logowania"], 1)


class TestLiczenie(unittest.TestCase):
    def setUp(self):
        self.katalog = tempfile.mkdtemp()
        self.plik = Path(self.katalog) / "limits.json"
        self.licznik = LicznikLimitow(self.plik)

    def test_poczatek_jest_pusty_i_nie_wymaga_pliku(self):
        r = self.licznik.raport("audio")
        self.assertEqual(r["sukcesy"], 0)
        self.assertEqual(r["limity"], 0)
        self.assertIsNone(r["ostatni_limit"])
        self.assertFalse(r["znasz_juz_okno"])

    def test_sukces_i_limit_sa_rozne_liczniki(self):
        self.licznik.rejestruj_sukces("audio", czas=1000.0)
        self.licznik.rejestruj_sukces("audio", czas=1010.0)
        limit = self.licznik.rejestruj_odmowe(
            "audio", AtrapaStatus("failed", error_code="USER_DISPLAYABLE_ERROR"), czas=1020.0
        )
        self.assertTrue(limit)
        r = self.licznik.raport("audio")
        self.assertEqual(r["sukcesy"], 2)
        self.assertEqual(r["limity"], 1)
        self.assertEqual(r["sukcesy_od_ostatniego_limitu"], 0)

    def test_zwykla_awaria_nie_jest_liczona_jako_limit(self):
        self.assertFalse(
            self.licznik.rejestruj_odmowe("audio", AtrapaStatus("failed", "INTERNAL", "boom"), czas=1.0)
        )
        r = self.licznik.raport("audio")
        self.assertEqual(r["limity"], 0)
        self.assertEqual(r["awarie_inne"], 1)

    def test_typy_sa_liczone_odzielnie(self):
        self.licznik.rejestruj_sukces("audio", czas=1.0)
        self.licznik.rejestruj_sukces("video", czas=2.0)
        self.licznik.rejestruj_sukces("video", czas=3.0)
        self.assertEqual(self.licznik.raport("audio")["sukcesy"], 1)
        self.assertEqual(self.licznik.raport("video")["sukcesy"], 2)
        self.assertEqual(self.licznik.raport("quiz")["sukcesy"], 0)

    def test_sukcesy_od_ostatniego_limitu(self):
        self.licznik.rejestruj_sukces("audio", czas=1.0)
        self.licznik.rejestruj_odmowe("audio", AtrapaStatus("failed", "USER_DISPLAYABLE_ERROR"), czas=2.0)
        self.licznik.rejestruj_sukces("audio", czas=3.0)
        self.licznik.rejestruj_sukces("audio", czas=4.0)
        self.assertEqual(self.licznik.sukcesy_od_ostatniego_limitu("audio"), 2)


class TestOknoResetu(unittest.TestCase):
    def setUp(self):
        self.licznik = LicznikLimitow(Path(tempfile.mkdtemp()) / "limits.json")

    def test_bez_sukcesu_po_limicie_okno_nieznane(self):
        # Swiadomie nie zgadujemy — bez pary limit+sukces nie ma czego wyznaczyc
        self.licznik.rejestruj_odmowe("audio", AtrapaStatus("failed", "USER_DISPLAYABLE_ERROR"), czas=1000.0)
        r = self.licznik.raport("audio")
        self.assertIsNone(r["okno_resetu_godziny_max"])
        self.assertFalse(r["znasz_juz_okno"])

    def test_okno_to_najmniejsza_para(self):
        limit = AtrapaStatus("failed", error_code="USER_DISPLAYABLE_ERROR")
        # limit -> sukces po 5h, pozniej limit -> sukces po 9h
        self.licznik.rejestruj_odmowe("audio", limit, czas=1000.0)
        self.licznik.rejestruj_sukces("audio", czas=1000.0 + 5 * H)
        self.licznik.rejestruj_odmowe("audio", limit, czas=1000.0 + 6 * H)
        self.licznik.rejestruj_sukces("audio", czas=1000.0 + 6 * H + 9 * H)
        r = self.licznik.raport("audio")
        self.assertEqual(r["okno_resetu_godziny_max"], 5.0)
        self.assertTrue(r["znasz_juz_okno"])
        self.assertEqual(len(r["pary_limit_sukces"]), 2)

    def test_awaria_miedzy_limitem_a_sukcesem_nie_pomiesza_sie_w_parze(self):
        limit = AtrapaStatus("failed", error_code="USER_DISPLAYABLE_ERROR")
        self.licznik.rejestruj_odmowe("audio", limit, czas=1000.0)
        self.licznik.rejestruj_odmowe("audio", AtrapaStatus("failed", "INTERNAL", "x"), czas=2000.0)
        self.licznik.rejestruj_sukces("audio", czas=3000.0)
        r = self.licznik.raport("audio")
        self.assertEqual(r["okno_resetu_godziny_max"], round(2000.0 / 3600.0, 2))


class TestWygasanieSesji(unittest.TestCase):
    """Wygasanie sesji to osobna kategoria od limitu kwoty.

    Nie teoria: sesja wygasla w trakcie wdrozenia, zanim powstalo jakiekolwiek
    `generate_*` — wywalilo `from_storage()`. Licznik rejestrujacy tylko
    odmowy generowania by tego zdarzenia nigdy nie zapisal.
    """

    def setUp(self):
        self.licznik = LicznikLimitow(Path(tempfile.mkdtemp()) / "limits.json")

    def test_rozpoznaje_wygasanie_sesji(self):
        for tekst in [
            "Authentication expired or invalid. Redirected to: https://accounts.google.com/x",
            "Run 'notebooklm login' to re-authenticate.",
        ]:
            with self.subTest(tekst=tekst):
                self.assertTrue(limits.czy_to_wygasniecie_sesji(ValueError(tekst)))

    def test_limit_kwoty_to_nie_wygasanie_sesji(self):
        # inna skala czasu — mylenie ich znosi wyznaczanie okna
        self.assertFalse(limits.czy_to_wygasniecie_sesji(ValueError("quota exceeded")))

    def test_zapisuje_wygasanie_i_nie_miesza_z_limitami(self):
        self.assertTrue(self.licznik.rejestruj_wygasniecie_sesji(
            ValueError("Authentication expired or invalid."), czas=1000.0))
        for typ in ("audio", "video", "quiz"):
            self.assertEqual(self.licznik.raport(typ)["limity"], 0)
        self.assertEqual(self.licznik.raport_sesji()["wykrycia"], 1)

    def test_nie_zapisuje_czego_nie_dotyczy_sesji(self):
        self.assertFalse(self.licznik.rejestruj_wygasniecie_sesji(
            ValueError("quota exceeded"), czas=1000.0))
        self.assertEqual(self.licznik.raport_sesji()["wykrycia"], 0)

    def test_zycie_nie_liczy_sie_z_dwoch_wykryc(self):
        # Dwa wykrycia tej samej martwej sesji to nie dwie sesje. Wczesniejsza
        # wersja raportowala tu "zycie 5 h", co bylo odstepem miedzy nimi.
        self.licznik.rejestruj_wygasniecie_sesji(ValueError("Authentication expired"), czas=0.0)
        self.licznik.rejestruj_wygasniecie_sesji(ValueError("Authentication expired"), czas=5 * H)
        r = self.licznik.raport_sesji()
        self.assertEqual(r["wykrycia"], 2)
        self.assertEqual(r["powtorne_wykrycia"], 1)
        self.assertIsNone(r["zycie_godziny"])

    def test_przetrwaja_restart(self):
        self.licznik.rejestruj_wygasniecie_sesji(ValueError("Authentication expired"), czas=1000.0)
        nowy = LicznikLimitow(self.licznik.sciezka)
        self.assertEqual(nowy.raport_sesji()["wykrycia"], 1)


class TestTrwalosc(unittest.TestCase):
    def setUp(self):
        self.katalog = tempfile.mkdtemp()
        self.plik = Path(self.katalog) / "limits.json"

    def test_dane_przetrwaja_restart(self):
        p = LicznikLimitow(self.plik)
        p.rejestruj_sukces("audio", czas=1.0)
        p.rejestruj_odmowe("video", AtrapaStatus("failed", "USER_DISPLAYABLE_ERROR"), czas=2.0)

        nowy = LicznikLimitow(self.plik)
        self.assertEqual(nowy.raport("audio")["sukcesy"], 1)
        self.assertEqual(nowy.raport("video")["limity"], 1)

    def test_uszkodzony_plik_nie_wywraca_licznika(self):
        self.plik.write_text("{to nie jest json", encoding="utf-8")
        licznik = LicznikLimitow(self.plik)
        self.assertEqual(licznik.raport("audio")["sukcesy"], 0)
        licznik.rejestruj_sukces("audio", czas=1.0)
        self.assertEqual(LicznikLimitow(self.plik).raport("audio")["sukcesy"], 1)

    def test_nieznany_typ_nie_wywraca(self):
        licznik = LicznikLimitow(self.plik)
        licznik.rejestruj_sukces("cos_nowego", czas=1.0)
        self.assertEqual(licznik.raport("cos_nowego")["sukcesy"], 1)

    def test_zapis_jest_atomowy_i_bez_plikow_smieciowych(self):
        licznik = LicznikLimitow(self.plik)
        for i in range(5):
            licznik.rejestruj_sukces("audio", czas=float(i))
        smieci = [p.name for p in Path(self.katalog).iterdir() if p.name.startswith(".limits-")]
        self.assertEqual(smieci, [])
        self.assertTrue(self.plik.exists())

    def test_zapisuje_sie_w_formacie_z_rozszerzeniem_json(self):
        LicznikLimitow(self.plik).rejestruj_sukces("audio", czas=1.0)
        dane = json.loads(self.plik.read_text(encoding="utf-8"))
        self.assertEqual(dane["wersja"], limits.WERSJA)
        self.assertIn("typy", dane)
        self.assertIn("audio", dane["typy"])

    def test_zero_jako_czas_nie_gubi_zdarzenia(self):
        # falsy 0 to nie to samo co brak czasu — falszywka z `czas or time.time()`
        licznik = LicznikLimitow(self.plik)
        licznik.rejestruj_sukces("audio", czas=0.0)
        self.assertEqual(licznik.zdarzenia("audio")[0].czas, 0.0)


if __name__ == "__main__":
    wynik = unittest.main(exit=False, verbosity=2).result
    print()
    print("KONTRAKT:", "OK" if wynik.wasSuccessful() else "PADL")
    sys.exit(0 if wynik.wasSuccessful() else 1)