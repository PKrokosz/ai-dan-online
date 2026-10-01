import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import generowanie


class AtrapaStatus:
    def __init__(self, status="pending", task_id="t1"):
        self.status = status
        self.task_id = task_id
        self.error = None
        self.error_code = None


class AtrapaArtifacts:
    def __init__(self, status=None, blad=None):
        self.status = status or AtrapaStatus()
        self.blad = blad
        self.wolane: list[dict] = []
        self.oczekiwane: list[dict] = []

    async def generate_audio(self, notebook_id, **kwargs):
        self.wolane.append({"notebook_id": notebook_id, **kwargs})
        if self.blad:
            raise self.blad
        return self.status

    async def wait_for_completion(self, notebook_id, task_id, **kwargs):
        self.oczekiwane.append({"notebook_id": notebook_id, "task_id": task_id, **kwargs})
        return AtrapaStatus("completed")


class AtrapaKlient:
    def __init__(self, status=None, blad=None):
        self.artifacts = AtrapaArtifacts(status, blad)


class TestNazwyParametrow(unittest.TestCase):
    """Nazwy argumentow w notebooklm-py sa NIEOCZEWISTE.

    To jest pulapka, na ktora juz wpadlismy: dokumentacja sugerowala
    `length=` / `format=`, a biblioteka przyjmuje `audio_length` /
    `audio_format`. Domyslnie `None` oznacza "nie podawaj", a wtedy
    NotebookLM wybiera wlasny default — stąd 30-50 MB w pierwszych probe'ach.
    """

    def test_enumy_sa_z_biblioteki_nie_z_tekstu(self):
        AudioFormat, AudioLength = generowanie._enumy()
        for _, nazwa in generowanie.DLUGOSC.values():
            self.assertTrue(hasattr(AudioLength, nazwa), f"AudioLength.{nazwa}")
        for _, nazwa in generowanie.FORMAT.values():
            self.assertTrue(hasattr(AudioFormat, nazwa), f"AudioFormat.{nazwa}")

    def test_wartosci_sa_zgodne_z_fonte(self):
        # odczytane z zainstalowanej biblioteki 01.10 (notebooklm-py 0.7.2)
        AudioFormat, AudioLength = generowanie._enumy()
        self.assertEqual(AudioLength.SHORT, 1)
        self.assertEqual(AudioLength.DEFAULT, 2)
        self.assertEqual(AudioLength.LONG, 3)
        self.assertEqual(AudioFormat.DEEP_DIVE, 1)
        self.assertEqual(AudioFormat.BRIEF, 2)
        self.assertEqual(AudioFormat.CRITIQUE, 3)
        self.assertEqual(AudioFormat.DEBATE, 4)

    def test_jezyk_to_pl_nie_en(self):
        # biblioteka domyslnie daje "en", a to material o Gothicu po angielsku
        # jest bezuzyteczny dla serwera
        self.assertEqual(generowanie.JEZYK, "pl")

    def test_domyslna_dlugosc_miesci_sie_w_zalaczniku(self):
        # SHORT+BRIEF ~3,7 MB przy 1,85 MB/min z pomiaru 30.09; DEFAULT dawalo
        # 30-50 MB i nie miescilo sie w 10 MiB
        self.assertEqual(generowanie.DOMYSLNA_DLUGOSC, "krotka")
        self.assertEqual(generowanie.DOMYSLNY_FORMAT, "brief")


class TestWygeneruj(unittest.IsolatedAsyncioTestCase):
    async def test_przekazuje_enumy_a_nie_gole_liczby(self):
        # `class AudioLength(int, Enum)` — wiec `isinstance(x, int)` jest
        # prawda i taki test niczego by nie sprawdzal. Liczymy typ: ma byc
        # czlonkiem enuma, nie gołą jedynką.
        AudioFormat, AudioLength = generowanie._enumy()
        klient = AtrapaKlient(AtrapaStatus("completed"))
        await generowanie.wygeneruj_audio(klient, "nb", dlugosc="krotka",
                                          format_audio="brief")
        wolane = klient.artifacts.wolane[0]
        self.assertIs(wolane["audio_length"], AudioLength.SHORT)
        self.assertIs(wolane["audio_format"], AudioFormat.BRIEF)
        self.assertEqual(wolane["language"], "pl")

    async def test_czeka_tylko_gdy_status_wymaga(self):
        klient = AtrapaKlient(AtrapaStatus("completed"))
        await generowanie.wygeneruj_audio(klient, "nb")
        self.assertEqual(klient.artifacts.oczekiwane, [],
                         "gotowy wynik nie moze czekac na wait_for_completion")

    async def test_czeka_gdy_status_pending(self):
        klient = AtrapaKlient(AtrapaStatus("pending"))
        raport = await generowanie.wygeneruj_audio(klient, "nb")
        self.assertEqual(len(klient.artifacts.oczekiwane), 1)
        self.assertEqual(raport["status"].status, "completed")

    async def test_wyjatek_z_zlecenia_nie_wypada_z_funkcji(self):
        # wywolujacy to worker w tle: wyjatek zostawilby zadanie w `w_toku`
        # na wiecznosc, bo nikt by go nie zdal
        klient = AtrapaKlient(blad=RuntimeError("Authentication expired"))
        raport = await generowanie.wygeneruj_audio(klient, "nb")
        self.assertIn("blad", raport)
        self.assertIn("Authentication expired", raport["blad"])

    async def test_timeout_oczekiwania_to_nie_limit(self):
        async def przekrocz(*a, **k):
            raise asyncio.TimeoutError("minelo 1800 s")
        klient = AtrapaKlient(AtrapaStatus("pending"))
        klient.artifacts.wait_for_completion = przekrocz
        raport = await generowanie.wygeneruj_audio(klient, "nb")
        self.assertIn("blad", raport)
        self.assertNotIn("limit", raport)

    async def test_nieznana_dlugosc_nie_wywala(self):
        # z UI przyjdzie tylko wartosc z listy, ale obcej nie wolno przepuszczac
        klient = AtrapaKlient(AtrapaStatus("completed"))
        raport = await generowanie.wygeneruj_audio(klient, "nb", dlugosc="niesia")
        self.assertIn("status", raport)

    async def test_source_ids_przechodza(self):
        klient = AtrapaKlient(AtrapaStatus("completed"))
        await generowanie.wygeneruj_audio(klient, "nb", source_ids=["s1", "s2"])
        self.assertEqual(klient.artifacts.wolane[0]["source_ids"], ["s1", "s2"])

    async def test_zgloszenie_dostaje_postep(self):
        klient = AtrapaKlient(AtrapaStatus("pending"))
        wiadomosci: list[str] = []

        async def zglos(tekst: str) -> None:
            wiadomosci.append(tekst)

        await generowanie.wygeneruj_audio(klient, "nb", zgloszenie=zglos)
        self.assertTrue(wiadomosci, "uzytkownik musi widziec, ze cos sie dzieje")
        self.assertTrue(any("minut" in w for w in wiadomosci))

    async def test_zepsute_zgloszenie_nie_zabija_generowania(self):
        klient = AtrapaKlient(AtrapaStatus("completed"))

        async def zglos(tekst: str) -> None:
            raise RuntimeError("kanal zamkniety")

        raport = await generowanie.wygeneruj_audio(klient, "nb", zgloszenie=zglos)
        self.assertIn("status", raport, "zgloszenie nie moze przerwac generowania")


if __name__ == "__main__":
    unittest.main()
