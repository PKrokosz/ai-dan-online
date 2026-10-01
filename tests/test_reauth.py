import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KAT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KAT / "tools"))

import cookies_z_profilu as ciasteczka_mod  # noqa: E402
import reauth  # noqa: E402


def postaw_sesje(katalog: Path, nazwa: str = "storage_state.json",
                 ciasteczka=("SID", "SAPISID")) -> Path:
    plik = katalog / nazwa
    plik.write_text(json.dumps({
        "cookies": [{"name": n, "value": "x", "domain": ".google.com"} for n in ciasteczka],
        "origins": [],
    }), encoding="utf-8")
    return plik


def atrapa_klienta(wywal: bool = False):
    """Podmienia NotebookLMClient; zapamietuje widziany NOTEBOOKLM_HOME.

    `sesja_zyje` importuje biblioteke w srodku funkcji, wiec podmiana musi
    leciec na module `notebooklm`, nie na zalaczonym przez nas nazwie.
    """
    import notebooklm

    widziane = {}

    class Klient:
        def __init__(self, **kwargs):
            widziane["home"] = os.environ.get("NOTEBOOKLM_HOME")

    class Fake:
        # `asyncio.run()` wymaga awaitable, wiec `from_storage` musi byc
        # `async` — inaczej test pada na TypeError zamiast sprawdzac sesje
        @staticmethod
        async def from_storage(**kwargs):
            home = Path(os.environ["NOTEBOOKLM_HOME"])
            widziane["home"] = str(home)
            widziane["jest_plik"] = (home / "storage_state.json").exists()
            if wywal:
                raise RuntimeError("Token fetch failed: Authentication expired or invalid")
            return Klient(**kwargs)

    notebooklm.NotebookLMClient = Fake
    return widziane


class SesjaTestowa(unittest.TestCase):
    def setUp(self):
        self.katalog = Path(tempfile.mkdtemp(prefix="nlm-test-"))
        self.addCleanup(shutil.rmtree, self.katalog, True)


class TestSesjaZyje(SesjaTestowa):
    def test_brak_pliku_to_nie_zyje(self):
        zyje, komunikat = reauth.sesja_zyje(self.katalog / "nie_ma.json")
        self.assertFalse(zyje)
        self.assertIn("brak pliku", komunikat)

    def test_zywa_sesja_przechodzi(self):
        plik = postaw_sesje(self.katalog)
        atrapa_klienta(wywal=False)
        zyje, _ = reauth.sesja_zyje(plik)
        self.assertTrue(zyje)

    def test_wygasla_sesja_jest_odparta(self):
        plik = postaw_sesje(self.katalog)
        atrapa_klienta(wywal=True)
        zyje, komunikat = reauth.sesja_zyje(plik)
        self.assertFalse(zyje)
        self.assertIn("Authentication expired", komunikat)

    def test_dziala_plik_o_innej_nazwie(self):
        # Regresja 30.09: funkcja zakladala nazwe `storage_state.json`, przez co
        # plik celu `sesja_na_serwer.json` zawsze raportowal "martwa" i skrypt
        # rotowal sesje codziennie, nawet zdrowa.
        plik = postaw_sesje(self.katalog, nazwa="sesja_na_serwer.json")
        atrapa_klienta(wywal=False)
        zyje, _ = reauth.sesja_zyje(plik)
        self.assertTrue(zyje, "plik o innej nazwie musi byc weryfikowalny")

    def test_weryfikacja_leci_w_izolacji(self):
        # biblioteka szuka `storage_state.json` w NOTEBOOKLM_HOME, wiec skrypt
        # musi wystawic plik pod ta nazwa w katalogu roboczym
        plik = postaw_sesje(self.katalog, nazwa="cos_innego.json")
        widziane = atrapa_klienta(wywal=False)
        reauth.sesja_zyje(plik)
        self.assertNotEqual(Path(widziane["home"]), self.katalog)
        self.assertTrue(widziane["jest_plik"],
                        "w katalogu roboczym musi leziec storage_state.json")

    def test_plik_zrodlowy_nie_jest_dotykany(self):
        plik = postaw_sesje(self.katalog, nazwa="sesja_na_serwer.json")
        przed = plik.read_bytes()
        atrapa_klienta(wywal=True)
        reauth.sesja_zyje(plik)
        self.assertEqual(plik.read_bytes(), przed)

    def test_notebooklm_home_wraca_do_poprzedniej_wartosci(self):
        plik = postaw_sesje(self.katalog)
        atrapa_klienta(wywal=False)
        os.environ["NOTEBOOKLM_HOME"] = "/gdzie/obok"
        try:
            reauth.sesja_zyje(plik)
            self.assertEqual(os.environ["NOTEBOOKLM_HOME"], "/gdzie/obok")
        finally:
            os.environ.pop("NOTEBOOKLM_HOME", None)

    def test_temporary_sie_sprzata(self):
        plik = postaw_sesje(self.katalog)
        atrapa_klienta(wywal=False)
        widziane = atrapa_klienta(wywal=False)
        reauth.sesja_zyje(plik)
        self.assertFalse(Path(widziane["home"]).exists(),
                         "katalog roboczy musi znikac po weryfikacji")


class TestCiasteczkaModul(unittest.TestCase):
    def test_wymagane_ciasteczka_sa_zadeklarowane(self):
        # brak ktoregokolwiek konczy `auth check` bledem tokenu
        for nazwa in ("SID", "__Secure-1PSIDTS", "HSID", "SSID", "APISID", "SAPISID"):
            self.assertIn(nazwa, ciasteczka_mod.WYMAGANE)

    def test_brakujace_sa_wypisywane_nazwami_nie_wartosciami(self):
        katalog = Path(tempfile.mkdtemp(prefix="nlm-c-"))
        self.addCleanup(shutil.rmtree, katalog, True)
        raport = ciasteczka_mod.zapisz(katalog / "s.json", [
            {"name": "SID", "value": "tajne", "domain": ".google.com"},
        ])
        self.assertEqual(raport["wyrane"], 1)
        self.assertIn("SAPISID", raport["brakujace"])
        # raport nie moze携带 wartosci ciasteczek
        self.assertNotIn("tajne", json.dumps(raport, ensure_ascii=False))

    def test_filtruje_tylko_domeny_google(self):
        self.assertTrue(ciasteczka_mod.pasuje(".google.com"))
        self.assertTrue(ciasteczka_mod.pasuje("notebooklm.google.com"))
        self.assertFalse(ciasteczka_mod.pasuje("example.com"))


class TestBezpieczenstwoPodmiany(SesjaTestowa):
    """Najwazniejsza wlasnosc: zla sesja nie moze zepsuc dobrej."""

    def _przepusc(self, wywal: bool, raport: dict) -> tuple[int, Path]:
        dobry = postaw_sesje(self.katalog, nazwa="sesja_na_serwer.json")
        atrapa_klienta(wywal=wywal)

        def wyciagnij_kontrfake(cel: Path, **_):
            # atrapa musi faktycznie napisac plik — inaczej krok 3 widzi
            # "brak pliku" i test nie sprawdza w ogole podmiany
            if "blad" not in raport and not raport.get("brakujace"):
                postaw_sesje(cel.parent, nazwa=cel.name)
            return raport

        with patch.object(ciasteczka_mod, "wyciagnij", wyciagnij_kontrfake), \
             patch.object(reauth, "plik_dla_serwera", return_value=dobry), \
             patch.object(reauth, "znacznik_ostatniej_proby",
                          return_value=self.katalog / "znacznik.json"):
            kod = reauth.wykonaj(wymuszaj=True)
        return kod, dobry

    def test_odrzucona_sesja_zostawia_stara_nietknieta(self):
        dobry = postaw_sesje(self.katalog, nazwa="sesja_na_serwer.json")
        przed_bajty = dobry.read_bytes()
        atrapa_klienta(wywal=True)
        with patch.object(ciasteczka_mod, "wyciagnij", lambda cel, **_: (
                postaw_sesje(cel.parent, nazwa=cel.name),
                {"sciezka": "x", "bajty": 10, "wyrane": 6, "brakujace": []})[1]), \
             patch.object(reauth, "plik_dla_serwera", return_value=dobry), \
             patch.object(reauth, "znacznik_ostatniej_proby",
                          return_value=self.katalog / "znacznik.json"):
            kod = reauth.wykonaj(wymuszaj=True)
        self.assertEqual(kod, 4, "odrzucona sesja musi dac kod 4")
        self.assertEqual(dobry.read_bytes(), przed_bajty,
                         "plik sesji nie moze byc dotkniety niezweryfikowanym")

    def test_dobra_sesja_jest_podmieniana(self):
        kod, cel = self._przepusc(wywal=False, raport={
            "sciezka": "x", "bajty": 10, "wyrane": 6, "brakujace": []})
        self.assertEqual(kod, 0)
        self.assertTrue(cel.exists())

    def test_brakujace_ciasteczka_koncza_przed_weryfikacja(self):
        kod, cel = self._przepusc(wywal=False, raport={
            "sciezka": "x", "bajty": 10, "wyrane": 3,
            "brakujace": ["SAPISID"]})
        self.assertEqual(kod, 3)

    def test_blad_wyciagania_nie_tworzy_pliku(self):
        kod, cel = self._przepusc(wywal=False, raport={"blad": "brak profilu"})
        self.assertEqual(kod, 2)

    def test_znacznik_zapisuje_wynik(self):
        # zadanie harmonogramu ma od czego odczytac, co zadzialo ostatnio
        cel = postaw_sesje(self.katalog, nazwa="sesja_na_serwer.json")
        znacznik = self.katalog / "znacznik.json"
        atrapa_klienta(wywal=False)
        with patch.object(ciasteczka_mod, "wyciagnij", lambda cel, **_: (
                postaw_sesje(cel.parent, nazwa=cel.name),
                {"sciezka": "x", "bajty": 10, "wyrane": 6, "brakujace": []})[1]), \
             patch.object(reauth, "plik_dla_serwera", return_value=cel), \
             patch.object(reauth, "znacznik_ostatniej_proby", return_value=znacznik):
            reauth.wykonaj(wymuszaj=True)
        self.assertTrue(znacznik.exists())
        dane = json.loads(znacznik.read_text(encoding="utf-8"))
        self.assertEqual(dane["wynik"], "ok")
        self.assertIn("czas", dane)


class TestHarmonogram(unittest.TestCase):
    def test_plik_celu_jest_staly(self):
        # harmonogram ma wskazywac jeden plik; cel poza katalogiem profilu,
        # bo `notebooklm login` nadpisuje wlasny stan w katalogu
        p = reauth.plik_dla_serwera()
        self.assertEqual(p.name, "sesja_na_serwer.json")
        self.assertNotIn("profiles", str(p))


if __name__ == "__main__":
    unittest.main()
