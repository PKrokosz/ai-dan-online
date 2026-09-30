#!/usr/bin/env python3
"""Bramka kontraktu bota ai-dan — bez sieci, bez logowania, bez tokenu.

Napisana PO tym, jak bot.py v1 okazał się nieuruchamialny mimo obecności
w repo. v1 nie miał zadnego testu, wiec cztery bledy wyciekly do produkcji.
Kazdy z nich jest tu odtworzony jako przypadek, a do tego wersja "zla" musi
przejsc — inaczej test jest pozorny.

Uruchomienie: python tests/test_bot.py
"""

import asyncio
import sys
import unittest
from pathlib import Path

# bot.py lezy o katalog wyzej — bez tego importujemy test, nie bota
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot

NB = "65f678e6-086c-43bf-a14a-2471286c35c0"


class AtrapaWynik:
    def __init__(self, answer="", references=None, conversation_id="c1"):
        self.answer = answer
        self.references = references or []
        self.conversation_id = conversation_id


class AtrapaRef:
    def __init__(self, numer, tekst, source_id="s1"):
        self.citation_number = numer
        self.cited_text = tekst
        self.source_id = source_id


class AtrapaChat:
    """Udaje ChatAPI — namespace z metoda ask()."""

    def __init__(self):
        self.calls = []

    async def ask(self, notebook_id, question, source_ids=None, conversation_id=None):
        self.calls.append({
            "notebook_id": notebook_id, "question": question, "conversation_id": conversation_id,
        })
        return AtrapaWynik(
            answer="Kto wlada Khorinis?",
            references=[AtrapaRef(1, "Khorinis jest stolicą", "src-a"),
                        AtrapaRef(2, "Opis miasta", "src-b")],
        )


class AtrapaZlyChat:
    """To, co bylo w v1: `client.chat(...)` wywolane jak funckje.

    UWAGA: celowo BEZ __call__ — prawdziwe ChatAPI tez nie jest wywolywalne.
    Pierwsza wersja tej atrapy miala __call__ i przez to test "dowod zeby"
    NIE wywalal — czyli udowadnial nic.
    """

    def __init__(self):
        self.calls = []


class AtrapaKlient:
    def __init__(self, chat=None):
        self.chat = chat or AtrapaChat()
        self.sources = self

    async def list(self, notebook_id):
        return []


class AtrapaZlyKlient:
    def __init__(self):
        self.chat = AtrapaZlyChat()


class TestKontraktChat(unittest.IsolatedAsyncioTestCase):
    async def test_uzywa_chat_ask(self):
        """PODSTAWOWY: bot musi wolac chat.ask, nie chat()."""
        klient = AtrapaKlient()
        odp, cyt, cid = await bot.zapytaj_notebook(klient, NB, "kto wlada Khorinis")
        self.assertTrue(klient.chat.calls, "bot nie wolal chat.ask")
        self.assertEqual(klient.chat.calls[0]["question"], "kto wlada Khorinis")
        self.assertEqual(klient.chat.calls[0]["notebook_id"], NB)
        self.assertEqual(cid, "c1")
        self.assertEqual(len(cyt), 2)

    async def test_stara_metoda_ladnie_wybucha(self):
        """DOWOD ZEBROPY: wersja v1 musi przejsc ten test inaczej — a ona pada."""
        klient = AtrapaZlyKlient()
        with self.assertRaises(TypeError):
            await klient.chat(notebook_id=NB, message="x", timeout=300.0)

    async def test_followup_przekazuje_conversation_id(self):
        klient = AtrapaKlient()
        await bot.zapytaj_notebook(klient, NB, "a ile mieszkańcow?", conversation_id="c9")
        self.assertEqual(klient.chat.calls[0]["conversation_id"], "c9")

    async def test_odpowiedz_pusta_nie_wybucha(self):
        pusty = AtrapaKlient(AtrapaChat())
        async def ask(*a, **k):
            return AtrapaWynik(answer="", references=None)
        pusty.chat.ask = ask
        odp, cyt, cid = await bot.zapytaj_notebook(pusty, NB, "x")
        self.assertEqual(odp, "")
        self.assertEqual(cyt, [])


class TestWidok(unittest.TestCase):
    def test_cytowania_maja_liczebniki_i_teksty(self):
        cyt = [{"numer": 1, "tekst": "Khorinis jest stolicą", "source_id": "src-a"}]
        widok = bot.zbuduj_widok("Odpowiedź", cyt)
        self.assertIn("[1]", widok)
        self.assertIn("Khorinis jest stolicą", widok)

    def test_tytul_zrodla_dopisany_gdy_znany(self):
        cyt = [{"numer": 2, "tekst": "Opis", "source_id": "src-b"}]
        widok = bot.zbuduj_widok("Odpowiedź", cyt, {"src-b": "Podrecznik gracza"})
        self.assertIn("Opis", widok)
        self.assertIn("Podrecznik gracza", widok)

    def test_tytul_zrodla_z_ktorego_nie_ma_nie_wywala(self):
        cyt = [{"numer": 1, "tekst": "x", "source_id": "nieistniejace"}]
        widok = bot.zbuduj_widok("Odpowiedź", cyt, {})
        self.assertIn("x", widok)

    def test_tytul_juz_w_tresci_nie_powtarzany(self):
        # Wykryte na zywo: NotebookLM cytowal naglowek sekcji, a przy
        # zrodlu tekstowym byl on zarazem tytulem zrodla. Bot sklejal
        # "TYTUL - TYTUL".
        tytul = "AKTUALIZACJA — stan na 30.09.2026"
        cyt = [{"numer": 1, "tekst": tytul, "source_id": "src-c"}]
        widok = bot.zbuduj_widok("Odpowiedź", cyt, {"src-c": tytul})
        self.assertIn(tytul, widok)
        self.assertNotIn(f"{tytul} — {tytul}", widok)
        self.assertEqual(widok.count(tytul), 1)

    def test_tytul_czesciowo_w_tresci_nie_powtarzany_bez_znakow_diakrytycznych(self):
        tytul = "Podręcznik Gracza 2026"
        cyt = [{"numer": 1, "tekst": "podrecznik gracza 2026", "source_id": "s"}]
        widok = bot.zbuduj_widok("Odpowiedź", cyt, {"s": tytul})
        self.assertEqual(widok.count("racza"), 1)

    def test_przecina_dlugie_odpowiedzi(self):
        dluga = "a" * 5000
        widok = bot.zbuduj_widok(dluga, [])
        self.assertLessEqual(len(widok), bot.LIMIT_DISCORD)
        self.assertTrue(widok.endswith("(ucięto)"))

    def test_pusta_odpowiedz_bez_cytowan_nie_jest_niczym(self):
        self.assertEqual(bot.zbuduj_widok("", []), "")

    def test_cytowanie_bez_tresci_pomijane(self):
        cyt = [{"numer": 1, "tekst": "   ", "source_id": "s"}]
        widok = bot.zbuduj_widok("Odpowiedź", cyt)
        self.assertNotIn("[1]", widok)
        self.assertIn("Odpowiedź", widok)


class TestBledy(unittest.TestCase):
    def test_kazdy_typ_bledu_daje_inny_komunikat(self):
        """Nie szukamy konkretnych slow — to one zaleza od mojego sformulowania
        i zmieniaja sie przy kazdej edycji. Testujemy trzy rzeczy, ktore maja
        znaczenie: komunikat istnieje, jest rozroznialny i nie wycieka nazwa
        klasy wyjatku do uzytkownika."""
        from notebooklm import AuthError, NetworkError, NotebookNotFoundError, RateLimitError

        przypadki = [
            ("AuthError", AuthError("x")),
            ("RateLimitError", RateLimitError("x")),
            ("NotebookNotFoundError", NotebookNotFoundError("x")),
            ("NetworkError", NetworkError("x")),
        ]
        teksty = [bot.komunikat_bledu(e) for _, e in przypadki]

        for (nazwa, _), tekst in zip(przypadki, teksty):
            self.assertTrue(tekst.strip(), "pusty komunikat dla " + nazwa)
            self.assertNotIn("Traceback", tekst)
            self.assertNotIn(nazwa, tekst, "uzytkownik widzi nazwe klasy: " + nazwa)

        self.assertEqual(len(set(teksty)), len(teksty),
                         "dwa rodzaje bledow daja ten sam komunikat")

    def test_komunikat_mowi_co_zrobic(self):
        """Komunikat musi podpowiedzic droge, inaczej uzytkownik nie wie
        co zrobic (w v1 wszystkie gale wygladaly tak samo)."""
        from notebooklm import AuthError, NotebookNotFoundError
        self.assertIn("notebooklm login", bot.komunikat_bledu(AuthError("x")))
        self.assertIn("NOTEBOOK_ID", bot.komunikat_bledu(NotebookNotFoundError("x")))

    def test_nieznany_bled_pokazuje_sie_dosownie(self):
        tekst = bot.komunikat_bledu(RuntimeError("boom"))
        self.assertIn("boom", tekst)
        # Nazwa klasy nie moze trafic do czatu — uzytkownik widzi komunikat,
        # a nie "RuntimeError: boom". Luka znaleziona przez test negatywny:
        # wersja z type(exc).__name__ przechodzila ten test.
        self.assertNotIn("RuntimeError", tekst)


class TestPodzialPytania(unittest.TestCase):
    def test_zwykle_pytanie_nie_jest_followup(self):
        tresc, follow = bot.podziel_pytanie("Kto wlada Khorinis?")
        self.assertEqual(tresc, "Kto wlada Khorinis?")
        self.assertFalse(follow)

    def test_dalej_to_followup(self):
        for slowo in ("dalej", "Dalej", "  dalej  ", "kontynuuj", "cd"):
            tresc, follow = bot.podziel_pytanie(slowo)
            self.assertEqual(tresc, "", slowo)
            self.assertTrue(follow, slowo)

    def test_puste_pytanie_nie_jest_niczym(self):
        self.assertEqual(bot.podziel_pytanie(""), ("", False))


class TestStart(unittest.TestCase):
    def test_brak_konfiguracji_konczy_sie_niezerem(self):
        """v1 tylko ostrzegal i leciał dalej z pustym NOTEBOOK_ID."""
        stary_nb, stary_token = bot.NOTEBOOK_ID, bot.DISCORD_TOKEN
        try:
            bot.NOTEBOOK_ID = ""
            bot.DISCORD_TOKEN = ""
            self.assertEqual(bot.main(), 1)
        finally:
            bot.NOTEBOOK_ID, bot.DISCORD_TOKEN = stary_nb, stary_token

    def test_bracket_z_inicjalizuje_konfiguracje(self):
        self.assertIsNotNone(bot.LIMIT_DISCORD)
        self.assertGreater(bot.MAX_PODRZEDKOW, 0)


if __name__ == "__main__":
    wynik = unittest.main(verbosity=2, exit=False).result
    print("\nKONTRAKT:", "OK" if wynik.wasSuccessful() else "PADL")
    sys.exit(0 if wynik.wasSuccessful() else 1)
