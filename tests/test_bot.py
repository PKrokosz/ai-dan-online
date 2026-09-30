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


class AtrapaUzytkownik:
    def __init__(self, ident, bot):
        self.id = ident
        self._bot = bot

    async def send(self, tresc):
        if self.id in self._bot.zablokuj:
            raise RuntimeError("Cannot send messages to this user")
        self._bot.wyslane.append((self.id, tresc))


class AtrapaDiscord:
    """Zbiera powiadomienia. fetch_user/get_user zwracaja atrape uzytkownika."""

    def __init__(self):
        self.wyslane = []
        self.komunikaty = []
        self.zablokuj = set()

    async def fetch_user(self, ident):
        self.komunikaty.append(("fetch", ident))
        return AtrapaUzytkownik(int(ident), self)

    def get_user(self, ident):
        self.komunikaty.append(("get", ident))
        return AtrapaUzytkownik(int(ident), self)


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


class TestTestowePobranieSesji(unittest.IsolatedAsyncioTestCase):
    """`/test` musi robic realne wywolanie, nie sprawdzac obiektu.

    Wykryte na zywo: sesja Google zostala uniewazniona, a `/test` wciaz
    pisal "gotowy do odpowiedzi", bo sprawdzal tylko `klient is not None`.
    """

    async def test_zdrowy_klient_zwraca_liste(self):
        klient = AtrapaKlient()
        zrodla = await bot.sprawdz_sesje(klient, "nb-1")
        self.assertEqual(zrodla, [])

    async def test_martwa_sesja_wywala_wyjatkiem(self):
        class KlientZly:
            class sources:  # noqa: N801 — atrapa
                @staticmethod
                async def list(nb):
                    raise ValueError(
                        "Authentication expired or invalid. Redirected to: accounts.google.com"
                    )

        with self.assertRaises(ValueError):
            await bot.sprawdz_sesje(KlientZly(), "nb-1")

    async def test_pobranie_idzie_do_konkretnego_notebooka(self):
        klient = AtrapaKlient()
        await bot.sprawdz_sesje(klient, "nb-42")
        # AtrapaKlient.list jest wspoldzielona z sources, wiec liczymy wywolania
        self.assertTrue(hasattr(klient.sources, "list"))


class TestOpisPrzerwy(unittest.TestCase):
    """Brak klienta ma dwa rozne powody i dwa rozne naprawy.

    Restart uslugi naprawia kazdy blad startu poza wygasla sesja —
    komunikat "zrestartuj" przy martwej sesji kieruje w zla strone.
    """

    def tearDown(self):
        bot._blad_polaczenia = None

    def test_wygasla_sesja_mowi_o_loginie_nie_o_restarcie(self):
        bot._blad_polaczenia = ValueError(
            "Authentication expired or invalid. Redirected to accounts.google.com"
        )
        opis, to_sesja = bot._opis_przerwy()
        self.assertTrue(to_sesja)
        self.assertIn("login", opis)

    def test_inny_blad_mowi_o_restarcie(self):
        bot._blad_polaczenia = RuntimeError("polaczenie zerwane")
        opis, to_sesja = bot._opis_przerwy()
        self.assertFalse(to_sesja)
        self.assertIn("restart", opis)

    def test_brak_bladu_to_restart(self):
        opis, to_sesja = bot._opis_przerwy()
        self.assertFalse(to_sesja)
        self.assertIn("restart", opis)


class TestPowiadomienieSesji(unittest.IsolatedAsyncioTestCase):
    """Jednorazowe powiadomienie o wygaslej sesji.

    Wymaganie: powiadomienie RAZ na incydent, kanał zapasowy gdy DM zamknięty,
    brak wyjątku przy awarii wysyłki, reset po powrocie do zdrowia.
    """

    def setUp(self):
        from notebooklm import AuthError, NetworkError, RateLimitError

        self.AuthError = AuthError
        self.NetworkError = NetworkError
        self.RateLimitError = RateLimitError
        bot._powiadomiono_o_sesji = False
        self.bot = AtrapaDiscord()
        self.bledy = [111]

    def tearDown(self):
        bot._powiadomiono_o_sesji = False

    async def test_powiadomienie_dziala_gdy_uzytkownik_nie_ma_w_cache(self):
        """Pusty cache to stan normalny tego bota, nie wyjatek.

        Bot jest slash-only i bez message_content, wiec `get_user` zwraca
        None dla kazdego uzytkownika. Na produkcji powiadomienie o wygaslej
        sesji wlasnie tak zginelo — `get_user(...).send` na None.
        """

        class BotCachePusty:
            def __init__(self):
                self.wyslane = []

            async def fetch_user(self, uid):
                bot_outer = self

                class U:
                    async def send(self, tekst):
                        bot_outer.wyslane.append(tekst)

                return U()

            def get_user(self, uid):
                return None  # cache pusta — jak w produkcji

        atrapa = BotCachePusty()
        # Wymuszamy brak wlasciciela: inaczej pierwsza galea (owner) odpowiada
        # sama i test przechodzi bez dotkniecia sciezki awaryjnej — zielony,
        # ktory niczego nie sprawdza.
        wlasciciel = bot.OWNER_USER_ID
        bot.OWNER_USER_ID = ""
        try:
            ok = await bot.powiadom_o_wygaslej_sesji(atrapa, [], 42)
        finally:
            bot.OWNER_USER_ID = wlasciciel
        self.assertTrue(ok, "powiadomienie nie wyszlo mimo dostepnego fetch_user")
        self.assertEqual(len(atrapa.wyslane), 1)

    async def test_auth_error_jest_bladem_sesji(self):
        self.assertTrue(bot.czy_blad_sesji(self.AuthError("expired")))

    async def test_poznane_sygnaly_biblioteki_jest_bladem_sesji(self):
        for tekst in [
            "Authentication expired or invalid.",
            "Redirected to accounts.google.com",
            "Run 'notebooklm login' to re-authenticate.",
        ]:
            with self.subTest(tekst=tekst):
                self.assertTrue(bot.czy_blad_sesji(ValueError(tekst)))

    async def test_inne_bledy_nie_sa_bladem_sesji(self):
        for exc in [
            self.RateLimitError("429"),
            self.NetworkError("timeout"),
            ValueError("pusty notebook"),
        ]:
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(bot.czy_blad_sesji(exc))

    async def test_powiadomienie_idzie_do_wlasciciela(self):
        bot.OWNER_USER_ID = "222"
        ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        self.assertTrue(ok)
        self.assertEqual([id for id, _ in self.bot.wyslane], [222])
        self.assertTrue(self.bot.wyslane[0][1])

    async def test_dm_wlasciciela_zamkniety_spada_na_zglaszajacego(self):
        bot.OWNER_USER_ID = "222"
        self.bot.zablokuj.add(222)
        ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        self.assertTrue(ok)
        self.assertEqual([id for id, _ in self.bot.wyslane], [111])

    async def test_drugie_powiadomienie_w_tej_samej_incydencie_nie_wychodzi(self):
        bot.OWNER_USER_ID = "222"
        await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        self.assertEqual(len(self.bot.wyslane), 1)
        ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        self.assertFalse(ok)
        self.assertEqual(len(self.bot.wyslane), 1)

    async def test_po_powrocie_do_zdrowia_powiadomienie_znowu_dziala(self):
        bot.OWNER_USER_ID = "222"
        await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        bot._powiadomiono_o_sesji = False  # reset w handlerze po sukcesie
        ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        self.assertTrue(ok)
        self.assertEqual(len(self.bot.wyslane), 2)

    async def test_komunikat_wymaga_dzialania_czlowieka(self):
        bot.OWNER_USER_ID = ""
        await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        tresc = self.bot.wyslane[0][1]
        self.assertIn("notebooklm login", tresc)
        self.assertIn("storage_state.json", tresc)
        self.assertLessEqual(len(tresc), 2000)

    async def test_awaria_wysylki_nie_wywraca_bota(self):
        bot.OWNER_USER_ID = "222"
        self.bot.zablokuj.update({222, 111})
        ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        self.assertFalse(ok)
        self.assertEqual(self.bot.wyslane, [])


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
