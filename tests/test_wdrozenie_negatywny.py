"""Test negatywny bramki manifestow: cofam wpis i sprawdzam, ze pada.

Reprodukuje realny stan z 01.10 — `daemon.py` i `run.py` byly w MAPA
instalatora, ale nie w manifeście wysylania, wiec na serwerze siedzialy
od 30.09, a instalacja konczyla sie `ok`.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAZOWY = (ROOT / "tools" / "ftp_up.py").read_bytes()


def uruchom() -> bool:
    w = subprocess.run([sys.executable, "-m", "unittest", "tests.test_wdrozenie"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT))
    return w.returncode != 0 or "FAILED" in w.stderr


class TestNegatywnyBramkiManifestow(unittest.TestCase):
    def setUp(self):
        (ROOT / "tools" / "ftp_up.py").write_bytes(BAZOWY)

    def tearDown(self):
        (ROOT / "tools" / "ftp_up.py").write_bytes(BAZOWY)

    def test_usuniecie_z_manifestu_wysylania_jest_wykrywane(self):
        tekst = BAZOWY.decode("utf-8")
        zlamany = tekst.replace('    ("daemon.py", "daemon.py"),\n', "")
        self.assertNotEqual(tekst, zlamany, "nie znaleziono wpisu do usuniecia")
        (ROOT / "tools" / "ftp_up.py").write_text(zlamany, encoding="utf-8")
        self.assertTrue(uruchom(),
                        "usunięcie pliku z manifestu wysyłania przeszło "
                        "po cichu — czyli plik zostalby stary na serwerze")

    def test_dodanie_lokalnego_narzedia_do_serwera_jest_wykrywane(self):
        tekst = BAZOWY.decode("utf-8")
        zlamany = tekst.replace(
            '    ("tools/limits_probe.py", "limits_probe.py"),',
            '    ("tools/dziennie_reauth.py", "dziennie_reauth.py"),\n'
            '    ("tools/limits_probe.py", "limits_probe.py"),')
        self.assertNotEqual(tekst, zlamany)
        (ROOT / "tools" / "ftp_up.py").write_text(zlamany, encoding="utf-8")
        self.assertTrue(uruchom(),
                        "narzędzie tylko-lokalne przeszło na serwer — "
                        "czyli sugeruje tam działanie, którego nie ma")

    def test_po_przywroceniu_wszystko_zielone(self):
        self.assertFalse(uruchom(), "bramka nie jest zielona na pliku naprawionym")


if __name__ == "__main__":
    unittest.main(verbosity=2)
