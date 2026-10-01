"""Zgodnosc manifestow wdrozenia — bramka na ciche pozostawienie pliku.

Tylko w REPO, nie na serwerze: bramka czyta `install_files.py` i
`tools/ftp_up.py`, a tych plikow w katalogu aplikacji nie ma — opisuja
sama droge wdrozenia. Dlatego plik swiadomie nie trafia do manifestu
serwerowego, a `tools/ftp_up.py` go nie wysyla.

`install_files.py` mowi wprost: "plik spoza listy zostaje stary po cichu,
a instalacja konczy sie ok bez sladu". Dzialalo to w druga strone: plik
MOGL byc na liscie instalatora i nie trafic na serwer, bo zaden uploader
go nie wysylal. Tak znikly `daemon.py` i `run.py` — na serwerze z 30.09,
podczas gdy instalacja wypisywala "juz w aplikacji" i konczyla sie `ok`.

Manifesty musza byc jednym zestawem plikow, inaczej roznica jest cicha
po obu stronach. Ta bramka porownuje je i wymusza jawne wyjatki.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _lista_z_pliku(nazwa: str, zmienna: str) -> list:
    """Wczytuje liste literalna z modulu, bez wykonywania go.

    Modulow nie wolno importowac: `ftp_up.py` laczy sie z FTP przy
    imporcie, a `install_files.py` kopiuje pliki. Sama lista wystarczy.
    """
    drzewo = ast.parse((ROOT / nazwa).read_text(encoding="utf-8"))
    for wezel in drzewo.body:
        if isinstance(wezel, ast.Assign) and wezel.targets[0].id == zmienna:
            return ast.literal_eval(wezel.value)
    raise AssertionError(f"{nazwa}: nie znaleziono {zmienna}")


class TestZgodnoscManifestow(unittest.TestCase):
    # `install_files.py` jest wysylany, ale nie instaluje sam siebie — musi
    # leżec w katalogu zrodlowym, bo stamtad go uruchamiamy. To jedyny
    # plik, ktory moze byc "nadmiarowy" z samej definicji.
    TYLKO_UPLOAD = {"install_files.py"}

    def setUp(self):
        self.instalowane = set(_lista_z_pliku("install_files.py", "PLIKI_PROSTE"))
        mapa = _lista_z_pliku("install_files.py", "MAPA")
        # klucze MAPA to nazwy plikow w katalogu zrodlowym (na FTP)
        self.instalowane |= {k for k, v in mapa.items() if v is not None}
        self.lokalne = set(_lista_z_pliku("install_files.py", "TYLKO_LOKALNIE"))

        wysylane = _lista_z_pliku("tools/ftp_up.py", "PLIKI")
        # Zrodla w ftp_up sa SZKIEZKAMI ("tools/reauth.py"), a klucze MAPA
        # plaskimi nazwami ("reauth.py"). Porownanie bez sprowadzenia do
        # nazwy pliku zglaszalo by roznice, ktorej nie ma.
        self.wysylane = {Path(zrodlo).name for zrodlo, _cel in wysylane}

    def test_kazdy_instalowany_plik_jest_wysylany(self):
        brak = sorted(self.instalowane - self.wysylane)
        self.assertEqual(
            brak, [],
            "pliki instalowane przez install_files.py, ale nigdy nie "
            "wysylane przez tools/ftp_up.py — na serwerze zostaja stare "
            "po cichu, a instalacja konczy sie ok")

    def test_nadbitkowe_wysylanie_nadchodzi_niepotrzebnie(self):
        # Odwrotna niezgodnosc jest mniej szkodliwa (plik tylko laduje na
        # dysk), ale nadal sygnalizuje pomylke w jednym z dwoch manifestow.
        nadmiar = sorted(self.wysylane - self.instalowane - self.TYLKO_UPLOAD)
        self.assertEqual(
            nadmiar, [],
            "pliki wysylane na serwer, ale nie instalowane do aplikacji — "
            "manifesty sie rozjechaly")

    def test_lokalne_narzedzia_nie_wchodza_na_serwer(self):
        # Swiadoma decyzja, nie przypadek: czytaja profil Chrome Windows
        # albo same wysylaja sesje na serwer. Serwer nie ma czego w tym
        # czytac, wiec wgranie ich tylko sugeruje, ze cos tam dziala.
        na_serwer = sorted(self.lokalne & self.instalowane)
        self.assertEqual(na_serwer, [],
                         "narzedzia tylko-lokalne nadal sa w manifeście "
                         "serwera — albo je usun, albo napisz, czemu")
        wyslane = sorted(self.lokalne & self.wysylane)
        self.assertEqual(wyslane, [],
                         "narzedzia tylko-lokalne sa wysylane na serwer")

    def test_lokalne_narzedzia_w_repo(self):
        # Sa lokalne, ale musza byc w repo pod kontrola wersji.
        for nazwa in sorted(self.lokalne):
            self.assertTrue(
                (ROOT / "tools" / nazwa).exists(),
                f"{nazwa} jest uzywany przez zadanie dzienne, ale nie ma go "
                f"w repo — wtedy zadanie dzienne psuje sie po cichu")

    def test_ftp_up_w_repo(self):
        # Manifest decydujacy, co jedzie, mieszkal w %TEMP% — poza kontrola
        # wersji. To byl korzen roznicy, nie sam brak wpisu.
        self.assertTrue((ROOT / "tools" / "ftp_up.py").exists())
        self.assertFalse((ROOT / "ftp_up.py").exists(),
                         "kopia w katalogu glownym rozszyłaby zbiory")


if __name__ == "__main__":
    unittest.main(verbosity=2)
