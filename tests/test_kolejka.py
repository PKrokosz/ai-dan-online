import json
import tempfile
import time
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kolejka


class PodmienKatalog(unittest.TestCase):
    """Kolejka pisze do katalogu aplikacji — testy nie maja prawa go dotykac."""

    def setUp(self):
        self.katalog = tempfile.mkdtemp()
        self.stary = kolejka.PLIK
        kolejka.PLIK = Path(self.katalog) / "zadania.json"

    def tearDown(self):
        kolejka.PLIK = self.stary


class TestDodawanie(PodmienKatalog, unittest.TestCase):
    def test_zadanie_dostaje_stan_czekajace(self):
        z = kolejka.dodaj("audio", channel_id=1, author_id=2)
        self.assertEqual(z["stan"], "czekajace")
        self.assertEqual(kolejka.podsumowanie()["czekajace"], 1)

    def test_parametry_sie_zapisuja(self):
        kolejka.dodaj("audio", channel_id=1, author_id=2,
                      parametry={"dlugosc": "short", "jezyk": "pl"})
        z = kolejka.nastepne()
        self.assertEqual(z["parametry"]["dlugosc"], "short")

    def test_kolejka_pelna_odrzuca(self):
        for i in range(kolejka.LIMIT_UROJONYCH):
            kolejka.dodaj("audio", channel_id=1, author_id=2)
        with self.assertRaises(ValueError):
            kolejka.dodaj("video", channel_id=1, author_id=2)


class TestKolejnosc(PodmienKatalog, unittest.TestCase):
    def test_kolejnosc_wg_czasu_utworzenia(self):
        pierwsze = kolejka.dodaj("audio", channel_id=1, author_id=2)
        drugie = kolejka.dodaj("video", channel_id=1, author_id=2)
        # czas jest znaczkiem do sekundy, wiec wymuszamy rozroznienie
        stan = kolejka.wczytaj()
        stan["zadania"][pierwsze["id"]]["utworzono"] = "2026-09-30 10:00:00"
        stan["zadania"][drugie["id"]]["utworzono"] = "2026-09-30 10:05:00"
        kolejka.zapisz(stan)
        self.assertEqual(kolejka.nastepne()["id"], pierwsze["id"])

    def test_w_toku_nie_jest_nastepnym(self):
        z = kolejka.dodaj("audio", channel_id=1, author_id=2)
        kolejka.oznacz(z["id"], "w_toku")
        kolejka.dodaj("video", channel_id=1, author_id=2)
        self.assertEqual(kolejka.nastepne()["typ"], "video")


class TestPrzerwaniePoRestarcie(PodmienKatalog, unittest.TestCase):
    """Zadanie `w_toku` po restarcie czeka na wynik procesu, ktorego nie ma.

    Bez sprzatania kolejka nigdy nie ruszy — `nastepne()` zwraca tylko
    `czekajace`, a `w_toku` zostaje na wieki.
    """

    def test_w_toku_po_restarcie_oznaczane_jako_przerwane(self):
        z = kolejka.dodaj("audio", channel_id=1, author_id=2)
        kolejka.oznacz(z["id"], "w_toku")
        zmienione = kolejka.sprzataj_przerwane()
        self.assertEqual(zmienione, 1)
        self.assertEqual(kolejka.wczytaj()["zadania"][z["id"]]["stan"], "przerwane")

    def test_po_sprzataniu_kolejka_rusza_dalej(self):
        z1 = kolejka.dodaj("audio", channel_id=1, author_id=2)
        kolejka.oznacz(z1["id"], "w_toku")
        kolejka.dodaj("video", channel_id=1, author_id=2)
        kolejka.sprzataj_przerwane()
        self.assertEqual(kolejka.nastepne()["typ"], "video")


class TestTrwalosc(PodmienKatalog, unittest.TestCase):
    def test_przezycie_reloadu(self):
        z = kolejka.dodaj("audio", channel_id=1, author_id=2,
                          parametry={"dlugosc": "short"})
        odczytane = kolejka.wczytaj()["zadania"][z["id"]]
        self.assertEqual(odczytane["parametry"]["dlugosc"], "short")

    def test_uszkodzony_plik_to_brak_danych_nie_awaria(self):
        kolejka.PLIK.write_text("to nie json", encoding="utf-8")
        stan = kolejka.wczytaj()
        self.assertEqual(stan["zadania"], {})

    def test_plik_ma_uprawnienia_0600(self):
        # Na Windows `st_mode` zwraca 0o666 dla kazdego pliku, niezaleznie
        # od chmod — to ograniczenie platformy, nie blad kodu. Test ma sens
        # tylko tam, gdzie uprawnienia realnie dzialaja, czyli na serwerze.
        # Wersja dla serwera jest w tools/test_uprawnienia.py.
        import sys as _sys
        if _sys.platform == "win32":
            self.skipTest("Windows: st_mode nie odzwierciedla chmod")
        # plik tworzymy TU — wczesniej test zakladal, ze zostal go jakis
        # poprzedni, i na serwerze konczyl sie FileNotFoundError zamiast
        # sprawdzeniem uprawnien
        kolejka.dodaj("audio", channel_id=1, author_id=2)
        self.assertTrue(kolejka.PLIK.exists())
        self.assertEqual(kolejka.PLIK.stat().st_mode & 0o777, 0o600)

    def test_dwa_zadania_w_tej_samej_milisekundzie_maja_rozne_id(self):
        # Regresja 30.09: id bylo z milisekund, wiec petla dodajaca 5 zadan
        # dostawala ten sam klucz i zadania nadpisywal sie nawzajem. Kolejka
        # nigdy nie byla pelna, a LIMIT_UROJONYCH nie chroniczyl przed zalewem.
        #
        # Milisekunde WYMUSZAMY: bez tego zalezy to od szybkosci dysku i na
        # Windows przechodzi nawet z bugiem — bramka, ktora nie lapie regresji
        # na maszynie deweloperskiej, jest bramka udajaca.
        from unittest.mock import patch
        with patch.object(kolejka.time, "time", return_value=1759000000.123):
            ids = {kolejka.dodaj("audio", channel_id=1, author_id=2)["id"]
                   for _ in range(5)}
        self.assertEqual(len(ids), 5)

    def test_kolejka_pelna_liczy_wszystkie_zadania_nie_ostatnie(self):
        # ta sama wymuszona milisekunda — inaczej na wolnym dysku limit
        # nigdy nie zostalby osiagniety i test niczego by nie sprawdzal
        from unittest.mock import patch
        with patch.object(kolejka.time, "time", return_value=1759000000.123):
            for _ in range(kolejka.LIMIT_UROJONYCH):
                kolejka.dodaj("audio", channel_id=1, author_id=2)
            self.assertEqual(len(kolejka.aktywne()), kolejka.LIMIT_UROJONYCH)
            with self.assertRaises(ValueError):
                kolejka.dodaj("video", channel_id=1, author_id=2)


if __name__ == "__main__":
    unittest.main()