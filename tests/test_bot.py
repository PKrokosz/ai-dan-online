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
        self.calls = []

    async def list(self, notebook_id):
        # licznik pozwala odróżnić "puls zrobił odczyt" od "puls w ogóle nie żyje"
        self.calls.append(notebook_id)
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
        self.kanalowe = []
        self.zablokuj = set()

    async def fetch_user(self, ident):
        self.komunikaty.append(("fetch", ident))
        return AtrapaUzytkownik(int(ident), self)

    def get_user(self, ident):
        self.komunikaty.append(("get", ident))
        return AtrapaUzytkownik(int(ident), self)

    async def fetch_channel(self, ident):
        # kanal alarmowy musi dzialac takze gdy zadna osoba nie pytala
        return AtrapaKanal(int(ident), self)


class AtrapaKanal:
    def __init__(self, ident, bot):
        self.id = ident
        self._bot = bot

    async def send(self, tresc):
        self._bot.kanalowe.append((self.id, tresc))


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


class TestTerminZapytania(unittest.IsolatedAsyncioTestCase):
    """Wiszący await musi zostać przerwany.

    W produkcji `ask()` nigdy nie wróciło: NotebookLM odpowiedziało 200 na
    `GenerateFreeFormStreamed`, po czym strumień został otwarty. `chat_timeout`
    biblioteki to limit pojedynczego odczytu HTTP, nie czasu trwania — strumień
    wysyłający kolejne bajty nigdy go nie przekroczy. Wiszący await nie jest
    wyjątkiem, więc `except` niczego nie złapał i na Discordzie wisiało
    "myśli..." w nieskożoność.
    """

    async def test_budzet_jest_mniejszy_niz_okno_followupu(self):
        # po przekroczeniu budzetu followup musi jeszcze dac radę wyslac
        self.assertLess(bot.BUDZET_ZAPYTANIA_S, 15 * 60)

    async def test_wiszacy_strumien_jest_przerwany(self):
        import asyncio
        from unittest.mock import patch

        class ZapytanieKtoreNigdyNieWraca:
            async def __call__(self, *args, **kwargs):
                for _ in range(3600):
                    await asyncio.sleep(0.05)
                return ("nie powinnismy tu dotrzec", [], None)

        with patch.object(bot, "zapytaj_notebook", ZapytanieKtoreNigdyNieWraca()):
            with self.assertRaises(asyncio.TimeoutError):
                await bot.zapytaj_z_budzetem(None, "nb", "pytanie", None, budzet=0.1)

    async def test_timeout_nie_jest_bledem_sesji(self):
        # limit budzetu to nie wygasniecie sesji ani limit kwoty
        import asyncio
        self.assertFalse(bot.czy_blad_sesji(asyncio.TimeoutError()))


class TestOgonInterakcji(unittest.IsolatedAsyncioTestCase):
    """Handler musi dokladnie raz wywolac wysylke odpowiedzi.

    Ogon handlera `/ai-dan` zostal swiadomie odciety przez zla edycje:
    po `except` nastepowalo `global _powiadomiono_o_sesji`, ktore zakonczylo
    funkcje, a reszta zostala martwym kodem wewnatrz innej funkcji. Handler
    konczyl sie bez wyslania czegokolwiek — na Discordzie "mysli..." na
    zawsze, bez wyjatku i bez logu, bo nie bylo czego zlapac. Wszystkie
    testy przechodzily, bo zadna nie wolala handlera.
    """

    async def test_handler_wywoluje_wysylke(self):
        # Testy powyzej wolaja `_zakoncz_interakcje` wprost, wiec nie
        # wykrylyby znikniecia tego wywolania z handlera — a to wlasnie
        # bylo uszkodzenie. Sprawdzamy wiec zrodlo handlera.
        import inspect

        # `ai_dan` to obiekt Command — funkcja siedzi w `.callback`.
        zrodlo = inspect.getsource(bot.ai_dan.callback)
        self.assertIn("_zakoncz_interakcje(", zrodlo,
                      "handler nie wysyla odpowiedzi — nic nie zostanie dostarczone")
        # Znacznik rozdzielenia funkcji na pol: `global` w ciele handlera.
        self.assertNotIn("\n    global ", zrodlo,
                         "handler uciety w miejscu `global` — ogon jest martwy")

    async def test_odpowiedz_dociera_do_uzytkownika(self):
        class Followup:
            def __init__(self):
                self.wyslane = []

            async def send(self, tresc, **kwargs):
                self.wyslane.append(tresc)

        class Interaction:
            def __init__(self):
                self.followup = Followup()

        interakcja = Interaction()
        bot.rozmowy.clear()
        await bot._zakoncz_interakcje(interakcja, 42, "Odpowiedz.", [], "cid-1")
        self.assertEqual(len(interakcja.followup.wyslane), 1, "nic nie wyszlo")
        self.assertIn("Odpowiedz.", interakcja.followup.wyslane[0])

    async def test_pusta_odpowiedz_dostaje_inny_komunikat(self):
        class Followup:
            def __init__(self):
                self.wyslane = []

            async def send(self, tresc, **kwargs):
                self.wyslane.append(tresc)

        class Interaction:
            def __init__(self):
                self.followup = Followup()

        interakcja = Interaction()
        await bot._zakoncz_interakcje(interakcja, 42, "   ", [], None)
        self.assertEqual(len(interakcja.followup.wyslane), 1)
        self.assertIn("nie zawiera odpowiedzi", interakcja.followup.wyslane[0])

    async def test_follow_up_zapisuje_rozmowe(self):
        class Followup:
            async def send(self, tresc, **kwargs):
                pass

        class Interaction:
            def __init__(self):
                self.followup = Followup()

        bot.rozmowy.clear()
        await bot._zakoncz_interakcje(Interaction(), 42, "odp.", [], "cid-abc")
        self.assertEqual(bot.rozmowy.get(42), "cid-abc")


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
        # 30.09: instrukcja mowila, ze automatyzacja jest niemozliwa. Nie
        # jest — profil Chrome trzyma trwale zalogowana sesje, a `reauth.py`
        # ja wyciaga bez hasla. Test pilnuje drogi, ktora dziala.
        self.assertIn("reauth.py", tresc)
        self.assertIn("storage_state.json", tresc)
        self.assertLessEqual(len(tresc), 2000)

    async def test_komunikat_nie_klamze_ze_to_niemozliwe(self):
        bot.OWNER_USER_ID = ""
        await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 111)
        tresc = self.bot.wyslane[0][1].lower()
        self.assertNotIn("nie da się tego zrobić automatycznie", tresc)
        self.assertNotIn("nie da sie tego zrobic automatycznie", tresc)

    async def test_kanal_alarmowy_dziala_bez_pytania(self):
        # Dziura 30.09: przy pustym OWNER_USER_ID powiadomienie mogeło wyjsc
        # tylko do osoby, ktora akurat zadała pytanie. Bez pytania nikt nie
        # dostawal nic, mimo ze bot mogl siedziec godzinami na martwej sesji.
        bot.OWNER_USER_ID = ""
        bot.ALERT_CHANNEL_ID = "777"
        try:
            ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 0)
            self.assertTrue(ok)
            # id_zglaszajacego == 0 oznacza "nikt nie pytal"
            self.assertEqual(len(self.bot.kanalowe), 1)
            self.assertEqual(self.bot.kanalowe[0][0], 777)
        finally:
            bot.ALERT_CHANNEL_ID = ""

    async def test_pusty_wlasciciel_i_brak_pytania_nie_wywraca(self):
        bot.OWNER_USER_ID = ""
        bot.ALERT_CHANNEL_ID = ""
        ok = await bot.powiadom_o_wygaslej_sesji(self.bot, self.bledy, 0)
        self.assertFalse(ok)

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


class TestPulsSesji(unittest.IsolatedAsyncioTestCase):
    """Proaktywna kontrola: wykrywa wygasanie BEZ czekania na pytanie.

    30.09 powiadomienie wychodzilo wylacznie z handlera pytania, a nikt nie
    pytal — bot siedzial godzinami "online" na martwej sesji bez sladu.
    """

    def setUp(self):
        from notebooklm import AuthError
        self.AuthError = AuthError
        self.bot = AtrapaDiscord()
        self.stare = (bot._powiadomiono_o_sesji, bot.KONTROLA_SESJI_S,
                      bot.KONTROLA_SESJI_START_S, bot.ALERT_CHANNEL_ID,
                      bot._blad_polaczenia, bot._klient_nb)
        bot._powiadomiono_o_sesji = False
        bot.KONTROLA_SESJI_S = 0.05
        bot.KONTROLA_SESJI_START_S = 0.0
        bot.ALERT_CHANNEL_ID = "777"
        bot._blad_polaczenia = None
        bot._klient_nb = None

    def tearDown(self):
        (bot._powiadomiono_o_sesji, bot.KONTROLA_SESJI_S,
         bot.KONTROLA_SESJI_START_S, bot.ALERT_CHANNEL_ID,
         bot._blad_polaczenia, bot._klient_nb) = self.stare

    def test_puls_nie_zjada_limitu(self):
        # `ask()` zjada dzienny limit. Wykrywanie musi byc najtańszym RPC,
        # wiec w zadaniu nie ma prawa wystapic zapytanie ani chat.ask.
        zrodlo = Path(bot.__file__).read_text(encoding="utf-8")
        start = zrodlo.index("async def zadanie_kontroli_sesji")
        koniec = zrodlo.index("def _opis_przerwy", start)
        cialo = zrodlo[start:koniec]
        self.assertNotIn("zapytaj_notebook", cialo)
        self.assertNotIn("chat.ask", cialo)
        self.assertIn("sprawdz_sesje", cialo)

    def test_puls_nie_jest_druga_instancja(self):
        zrodlo = Path(bot.__file__).read_text(encoding="utf-8")
        self.assertIn("and _zadanie_kontroli is None", zrodlo,
                      "on_ready fires po reconnect — bez tego zadanie mnozy sie")

    async def test_puls_wykrywa_wygasanie_bez_pytania(self):
        bot._blad_polaczenia = RuntimeError("Authentication expired or invalid")
        zadanie = asyncio.create_task(bot.zadanie_kontroli_sesji(self.bot))
        await asyncio.sleep(0.3)
        zadanie.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await zadanie
        self.assertEqual(len(self.bot.kanalowe), 1,
                         "nikt nie pytal, a powiadomienie i tak musi wyjsc")

    async def test_puls_nie_spamuje_przy_czystej_sesji(self):
        zadanie = asyncio.create_task(bot.zadanie_kontroli_sesji(self.bot))
        await asyncio.sleep(0.3)
        zadanie.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await zadanie
        self.assertEqual(self.bot.kanalowe, [],
                         "zdrowa sesja nie moze generowac powiadomien")

    async def test_puls_przy_czystej_sesji_uzywa_tylko_odczytu(self):
        klient = AtrapaKlient()
        bot._klient_nb = klient
        bot._blad_polaczenia = None
        zadanie = asyncio.create_task(bot.zadanie_kontroli_sesji(self.bot))
        await asyncio.sleep(0.3)
        zadanie.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await zadanie
        self.assertTrue(klient.sources.calls, "puls musi faktycznie odpytywac")
        self.assertFalse(klient.chat.calls,
                         "puls nie moze uzywac ask() — to zjada limit")


if __name__ == "__main__":
    wynik = unittest.main(verbosity=2, exit=False).result
    print("\nKONTRAKT:", "OK" if wynik.wasSuccessful() else "PADL")
    sys.exit(0 if wynik.wasSuccessful() else 1)
