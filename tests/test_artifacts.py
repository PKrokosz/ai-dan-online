import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import artifacts


class AtrapaAtrybutow:
    def __init__(self, status="3", title="Tytul", url=None, aid="abc12345-0000",
                 created="2026-09-30 10:00:00"):
        self.status = status
        self.title = title
        self.url = url
        self.artifact_id = aid
        self.created_at = created


class AtrapaKlient:
    def __init__(self, lista=None):
        self.artifacts = self
        self._lista = lista or {}

    def list_audio(self, _nb):
        return self._lista.get("audio", [])

    def list_infographics(self, _nb):
        return self._lista.get("infographic", [])


class TestIdentyfikacja(unittest.TestCase):
    def test_id_z_artifact_id(self):
        self.assertEqual(artifacts.identyfikator(AtrapaAtrybutow()), "abc12345-0000")

    def test_url_z_kolumny_url(self):
        self.assertEqual(artifacts.url_pliku(AtrapaAtrybutow(url="http://x")), "http://x")

    def test_url_brak(self):
        self.assertIsNone(artifacts.url_pliku(AtrapaAtrybutow()))

    def test_tylko_gotowe_sa_artefaktami(self):
        self.assertTrue(artifacts.jest_gotowy(AtrapaAtrybutow(status="3")))
        for stan in ("0", "1", "2", "4", "5"):
            with self.subTest(stan=stan):
                self.assertFalse(artifacts.jest_gotowy(AtrapaAtrybutow(status=stan)))


class TestOpis(unittest.TestCase):
    def test_opis_zawiera_miejsce_dla_limitu(self):
        # 10 MiB to limit zalacznika — opis musi to komunikowac
        w = {"typ": "audio", "id": "abc12345-0000", "tytul": "Test",
             "utworzono": "2026-09-30", "mb": 30.35, "miesci": False}
        tekst = artifacts.opis(w)
        self.assertIn("30.35 MB", tekst)
        self.assertIn("przekracza", tekst)

    def test_opis_mieszcacego_pliku_bez_ostrzezenia(self):
        w = {"typ": "infographic", "id": "abc12345-0000", "tytul": "Test",
             "utworzono": "2026-09-30", "mb": 6.77, "miesci": True}
        self.assertNotIn("przekracza", artifacts.opis(w))

    def test_bled_odczytu_nie_wyglada_jak_dane(self):
        tekst = artifacts.opis({"typ": "audio", "blad": "TimeoutError: przekroczono limit"})
        self.assertIn("nie udało się", tekst)
        self.assertNotIn("MB", tekst)


class TestLimitZalacznika(unittest.TestCase):
    def test_limit_to_dokladnie_10_mib(self):
        # zweryfikowane 30.09 w dokumentacji Discorda: 10 MiB na zalacznik,
        # dotyczy takze Create Message i Edit Message
        self.assertEqual(artifacts.LIMIT_ZALACZNIKA_B, 10 * 1024 * 1024)

    def test_granica_jest_dokladnie_ten_limit(self):
        self.assertFalse(10 * 1024 * 1024 + 1 <= artifacts.LIMIT_ZALACZNIKA_B)
        self.assertTrue(10 * 1024 * 1024 <= artifacts.LIMIT_ZALACZNIKA_B)


class TestNazwyTypow(unittest.TestCase):
    def test_kazdy_typ_ma_nazwe_dla_uzytkownika(self):
        # nazwy maja byc krotkie i czytelne, ale "infografika" ma 11 liter
        # i jest poprawna — limit dlugosci byl tu zlym sprawdzeniem
        for typ in artifacts.LISTY:
            with self.subTest(typ=typ):
                self.assertLessEqual(len(typ), 12)
                self.assertTrue(typ.isascii())

    def test_nazwy_nie_sa_bezposrednim_importem_z_biblioteki(self):
        # uzytkownik widzi "infografika", nie "list_infographics"
        for typ, metoda in artifacts.LISTY.items():
            with self.subTest(typ=typ):
                self.assertFalse(typ.startswith("list_"))
                self.assertTrue(metoda.startswith("list_"))

    def test_nazwy_sa_unikalne(self):
        self.assertEqual(len(artifacts.LISTY), len(set(artifacts.LISTY.values())))


class TestRozszerzenia(unittest.TestCase):
    """Nazwa pliku steruje tym, jak Discord pokazuje plik w czacie.

    Regresja 30.09: nazwa byla f"{id}.mp3" dla kazdego typu, wiec infografika
    (PNG) wychodzila jako `.mp3` — bez odtwarzacza i z mylnym MIME.
    """

    def test_kazdy_typ_z_listy_ma_rozszerzenie(self):
        for typ in artifacts.LISTY:
            with self.subTest(typ=typ):
                self.assertIn(artifacts.rozszerzenie(typ),
                              artifacts.ROZSZERZENIA.values())

    def test_odtwarzacze_dostaja_swoje_rozszerzenia(self):
        self.assertEqual(artifacts.rozszerzenie("audio"), "mp3")
        self.assertEqual(artifacts.rozszerzenie("video"), "mp4")
        self.assertEqual(artifacts.rozszerzenie("infografika"), "png")

    def test_nieznany_typ_to_bin_a_nie_mp3(self):
        # lepsza neutralna nazwa niz mylace rozszerzenie, pod ktorym
        # takiej zawartosci nie ma
        self.assertEqual(artifacts.rozszerzenie("cos-obcego"), "bin")
        self.assertNotEqual(artifacts.rozszerzenie("cos-obcego"), "mp3")

    def test_typ_niewrazliwy_na_wielkosc_znakow_i_spacje(self):
        self.assertEqual(artifacts.rozszerzenie("  AUDIO "), "mp3")

    def test_rozszerzenie_nie_ma_kropli(self):
        for typ, ext in artifacts.ROZSZERZENIA.items():
            with self.subTest(typ=typ):
                self.assertFalse(ext.startswith("."))
                self.assertEqual(ext, ext.strip())

    def test_bot_nie_hardkoduje_rozszerzenia(self):
        sciezka = Path(artifacts.__file__).resolve().parent / "bot.py"
        tekst = sciezka.read_text(encoding="utf-8")
        poczatek = tekst.index("async def pobierz_i_wyslij")
        koniec = tekst.index("\nasync def ", poczatek + 1)
        cialo = tekst[poczatek:koniec]
        self.assertNotIn(".mp3", cialo)
        self.assertIn("artifacts.rozszerzenie(", cialo)


if __name__ == "__main__":
    unittest.main()