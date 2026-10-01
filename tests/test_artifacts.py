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


class TestPodpowiedzi(unittest.TestCase):
    """Autocomplete `/pobierz` — lista wyboru zamiast wpisywania id.

    Dwie granice Discora, ktore trzeba pilnowac: 25 wyborow i 100 znakow
    etykiety. Przekroczenie nie jest bledem — Discord po prostu obcina, wiec
    uzytkownik widzi liste wygladajaca jak kompletna, a nie jest.
    """

    @staticmethod
    def wpis(tytul="Przewodnik po awansach w Kolonii", typ="audio", ident="abc12345-0000"):
        return {"typ": typ, "id": ident, "tytul": tytul,
                "utworzono": "2026-09-30", "url": "https://x/y.mp3"}

    def test_etykieta_zawiera_tytul_i_typ(self):
        etykieta = artifacts.etykieta_autocomplete(self.wpis())
        self.assertIn("Przewodnik po awansach", etykieta)
        self.assertIn("audio", etykieta)

    def test_ciecie_zawsze_na_granicy_slowa(self):
        # Jeden tytul to za malo: przy dlugosci dobranej tak, by znak wypadl
        # na spacji, test przechodzil nawet na kodzie tniejacym w polowie
        # slowa (sprawdzone 01.10). "ab ab ab ..." daje co trzeci znak jako
        # spacje, wiec surowe cięcie trafia w slowo w wiekszosci przypadkow.
        zlamane = 0
        for n in range(1, 60):
            tytul = "ab " * n + "cd ef gh"
            etykieta = artifacts.etykieta_autocomplete(self.wpis(tytul, "audio"))
            if "…" not in etykieta:
                continue
            przed = etykieta.split("…")[0]
            pozycja = len(przed)
            if not (pozycja >= len(tytul) or tytul[pozycja] == " "):
                zlamane += 1
        self.assertEqual(zlamane, 0,
                         f"cięcie w połowie słowa w {zlamane} przypadkach")

    def test_etykieta_nie_przekracza_limitu(self):
        dlugi = "Bardzo dlugi tytul materialu " * 10
        for typ in ("audio", "video"):
            etykieta = artifacts.etykieta_autocomplete(self.wpis(dlugi, typ))
            self.assertLessEqual(len(etykieta), 100, f"{typ}: za dlugie")

    def test_dluga_etykieta_nie_urwa_slowa(self):
        # musi realnie przekroczyc 100 znakow, inaczej skracanie sie nie
        # odpali i test na niczym by nie czekal — taki test jest pozorny
        dlugi = ("Płacili za bycie więźniem w Gothicu i jeszcze o tym opowiadali "
                 "przez cały sezon przygód w krainie mgły")
        etykieta = artifacts.etykieta_autocomplete(self.wpis(dlugi, "audio"))
        self.assertGreater(len(dlugi) + len(" (audio)"), 100, "przypadek nie jest długi")
        self.assertLessEqual(len(etykieta), 100)
        self.assertIn("audio", etykieta, "typ nie moze zniknac przy skracaniu")
        self.assertIn("…", etykieta)
        # cięcie następuje na granicy słowa: znak w oryginale tuż za skrótem
        # musi być spacją (albo skrót sięga końca tytułu), inaczej wielokrop
        # ląduje w połowie słowa
        przed = etykieta.split("…")[0]
        pozycja = len(przed)
        self.assertTrue(
            pozycja >= len(dlugi) or dlugi[pozycja] == " ",
            f"cięcie w połowie słowa: …{dlugi[max(0, pozycja - 5):pozycja + 5]}…"
        )

    def test_podpowiedzi_uzywaja_pelnego_id(self):
        # wartosc musi byc jednoznaczna; prefiks byl zrodlem kolizji id
        wybor = artifacts.podpowiedzi([self.wpis(ident="abc12345-0000")])
        self.assertEqual(wybor[0]["value"], "abc12345-0000")

    def test_podpowiedzi_omijaja_bledy_i_bez_url(self):
        wpisy = [
            {"typ": "audio", "blad": "TimeoutError: cos"},
            {"typ": "audio", "id": "x1", "tytul": "Bez adresu"},
            self.wpis(ident="x2"),
        ]
        wybor = artifacts.podpowiedzi(wpisy)
        self.assertEqual([w["value"] for w in wybor], ["x2"])

    def test_limit_wyborow_to_dokladnie_25(self):
        wpisy = [self.wpis(tytul=f"Materiał {i}", ident=f"id{i}") for i in range(40)]
        wybor = artifacts.podpowiedzi(wpisy)
        self.assertEqual(len(wybor), 25,
                         "Discord obcina do 25 — myslimy za uzytkownika")

    def test_pusta_lista_gdy_nic_nie_ma(self):
        self.assertEqual(artifacts.podpowiedzi([]), [])

    def test_typ_bez_spacji_nie_wywala(self):
        self.assertIn("audio", artifacts.etykieta_autocomplete(self.wpis("a", "audio")))


if __name__ == "__main__":
    unittest.main()