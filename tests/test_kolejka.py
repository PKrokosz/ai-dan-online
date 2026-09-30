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
        self.assertEqual(kolejka.PLIK.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()