"""Licznik i wyznacnik limitow NotebookLM.

Po co
====
NotebookLM nie udostepnia licznika wygenerowanych artefaktow ani pozostalej
kwoty, a limity sa rozne dla roznych typow i resetuja sie w oknach, nie
w pełnych dobach. Jedyne co da sie zmierzyc to zdarzenia:

- zadanie wystartowalo i sie zakonczlo sukcesem  -> `ok`
- Google odmowil z powodu kwoty/windowu            -> limit

Z tych zdarzen daja sie wyznaczyc **granice**, nie dokladne liczby:

- limit + pierwsza sukcesna proba po nim daje  `okno_resetu <= roznica`
- liczba sukcesow miedzy kolejnymi limitami daje `limiarnie >= minimum`

Nie zapisujemy zgadywki jako faktu. Raport podaje granice i hipoteze, a
zbieracz dopisuje surowe zdarzenia, wiec hipoteza da sie kazdy przeliczyc
od nowa po nastepnym oknie.

Wykrywanie limitu jest skopiowane z biblioteki (`GenerationStatus.is_rate_limited`
w `rpc/types.py`): status `failed` lub `removed`, a do tego `error_code ==
"USER_DISPLAYABLE_ERROR"` albo tekst z `"rate limit" / "quota" /
"limit exceeded"`. Wlasna regula rozjechalaby sie z biblioteka.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WERSJA = 1

# Typy artefaktow, ktore liczymy osobno. Limity sa rozne dla kazdego,
# wiec jedna wspolna liczba klamalaby.
TYPY = (
    "audio", "video", "cinematic_video", "infographic", "slide_deck",
    "report", "quiz", "flashcards", "data_table", "mind_map",
)

# Identyfikatory odmowy — skopiowane z biblioteki
KOD_LIMITU = "USER_DISPLAYABLE_ERROR"
STATUSY_ODMOWY = ("failed", "removed")
TEKSTY_LIMITU = ("rate limit", "quota", "limit exceeded")

# Sygnały wygasłej SESJI. Osobne od limitu kwoty, bo resetują się w zupełnie
# innej skali: kwota godziny, sesja tygodnie. Wrzucenie jednego do drugiego
# zatrułoby wyznaczanie okna — i to nie jest teoria: sesja wygasła w trakcie
# wdrożenia, zanim doszedł do tego żaden generate_* (padło from_storage()).
SYGNAŁY_WYGASŁEJ_SESJI = (
    "authentication expired",
    "redirected to",
    "run 'notebooklm login'",
)


def czy_to_wygasniecie_sesji(wyjątek: BaseException) -> bool:
    """Czy wyjątek oznacza wygasłą sesję Google (a nie limit kwoty).

    Te same trzy napisy co `_AUTH_ERROR_SIGNALS` w bibliotece — nie własna
    lista, bo rozjechałaby się przy aktualizacji.
    """
    tekst = str(wyjątek).lower()
    return any(s in tekst for s in SYGNAŁY_WYGASŁEJ_SESJI)


def czy_wymaga_oczekiwania(status: Any) -> bool:
    """Czy zadanie jest jeszcze w locie i trzeba poczekac na wynik.

    `generate_*` prawie zawsze zwraca `pending`, nie `completed`. Kod, ktory
    czeka tylko na `completed`, nigdy nie doczeka i zapisze zadanie w
    toku jako awarie.
    """
    return str(getattr(status, "status", "")) in ("pending", "in_progress")


def _iso(czas: float | None) -> str | None:
    """Znacznik czasu w UTC — raport ma byc porownywalny niezaleznie od strefy."""
    if czas is None:
        return None
    return datetime.fromtimestamp(czas, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def czy_to_limit(status: Any) -> bool:
    """Czy odmowa to limit kwoty/windowu, a nie zwykla awaria.

    Kolejna proba wynika z `GenerationStatus.is_rate_limited`: kod strukturalny
    ma pierwszenstwo, tekst jest zapasem dla starszych odpowiedzi. Status
    `removed` liczymy tak samo jak `failed` — Google potrafi po cichu zdjąc
    artefakt po odmowie.
    """
    if str(getattr(status, "status", "")) not in STATUSY_ODMOWY:
        return False
    kod = getattr(status, "error_code", None)
    if kod == KOD_LIMITU:
        return True
    tekst = getattr(status, "error", None)
    if tekst:
        niżej = str(tekst).lower()
        return any(f in niżej for f in TEKSTY_LIMITU)
    return False


@dataclass
class Wydarzenie:
    """Jedno zdarzenie: sukces albo odmowa."""

    czas: float  # epoch sekund; czas w lokalnej strefie bylby klopotem
    status: str

    def iso(self) -> str:
        return datetime.fromtimestamp(self.czas, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )


@dataclass
class StanTypu:
    typ: str
    sukcesy: list[Wydarzenie] = field(default_factory=list)
    odmowy: list[Wydarzenie] = field(default_factory=list)  # zwykle limity
    awarie: list[Wydarzenie] = field(default_factory=list)   # nie limity

    @property
    def wszystkie(self) -> list[Wydarzenie]:
        return sorted(self.sukcesy + self.odmowy + self.awarie, key=lambda w: w.czas)

    def granice_okna(self) -> dict[str, Any]:
        """Okno resetu: `<=` pierwszego sukcesu po limicie.

        Jesli limit byl o T, a pierwsza proba udala sie o T+d, to okno
        resetu jest najwyzej d — inaczej proba wypadlaby wciaz w starym
        oknie. Bez zadnego takiego przypadku nie wiadomo nic.
        """
        zdarzenia = self.wszystkie
        granice = []
        for i, z in enumerate(zdarzenia):
            if z not in self.odmowy:
                continue
            pozniejsze = [w for w in zdarzenia if w.czas > z.czas and w in self.sukcesy]
            if pozniejsze:
                d = min(pozniejsze, key=lambda w: w.czas)
                granice.append({
                    "limit": z.iso(),
                    "nastepny_sukces": d.iso(),
                    "godziny": round((d.czas - z.czas) / 3600.0, 2),
                })
        return {
            "okno_resetu_godziny_max": round(min((g["godziny"] for g in granice), default=0), 2) or None,
            "pary": granice[-5:],
        }


class LicznikLimitow:
    """Trwala liczba zdarzen per typ artefaktu.

    Zapis atomowy (tmp + rename) i tolerancja na uszkodzony plik: licznik
    jest telemetria, a nie stan krytyczny — nie moze wywrocic bota.
    """

    def __init__(self, sciezka: str | Path) -> None:
        self.sciezka = Path(sciezka)
        self._blokada = threading.Lock()
        self._typy: dict[str, StanTypu] = {t: StanTypu(t) for t in TYPY}
        # wygasania sesji nie sa przypisane do typu artefaktu: padaja wczesniej,
        # niz w ogole powstaje zadanie, i dotycza calego klienta
        self.logowania: list[Wydarzenie] = []
        self.wykrycia: list[Wydarzenie] = []
        self.wygasania: list[Wydarzenie] = []
        self.powtorne_wykrycia = 0
        self._wczytaj()

    # --- zapis / odczyt -------------------------------------------------
    def _wczytaj(self) -> None:
        if not self.sciezka.exists():
            return
        try:
            surowe = json.loads(self.sciezka.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # uszkodzony plik = brak danych, nie awaria
            return
        if not isinstance(surowe, dict):
            return
        for nazwa, dane in (surowe.get("typy") or {}).items():
            if nazwa not in self._typy or not isinstance(dane, dict):
                continue
            stan = self._typy[nazwa]
            for klucz, cel in (
                ("sukcesy", stan.sukcesy), ("odmowy", stan.odmowy), ("awarie", stan.awarie)
            ):
                for w in dane.get(klucz) or []:
                    try:
                        cel.append(Wydarzenie(czas=float(w["czas"]), status=str(w.get("status", ""))))
                    except (KeyError, TypeError, ValueError):
                        continue
        self._wczytaj_sesje(surowe)

    def _wczytaj_sesje(self, surowe: dict[str, Any]) -> None:
        """Sesje w nowym ukladzie (logowania/wykrycia/wygasania) albo starym.

        Stary uklad to plaska lista zdarzen bez informacji, czy to logowanie
        czy wykrycie. Wszystkie trafiaja do `wykrycia`, a `wygasania` zostaje
        puste — dzieki temu zycie sesji nie jest liczone z czegokolwiek,
        czego nie znamy.
        """
        sekcja = surowe.get("sesje")
        if isinstance(sekcja, list):
            for w in sekcja:
                try:
                    self.wykrycia.append(
                        Wydarzenie(czas=float(w["czas"]), status=str(w.get("status", "")))
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            self.powtorne_wykrycia = 0
            return
        if not isinstance(sekcja, dict):
            return
        for klucz, cel in (("logowania", self.logowania), ("wykrycia", self.wykrycia)):
            for w in sekcja.get(klucz) or []:
                try:
                    cel.append(
                        Wydarzenie(czas=float(w["czas"]), status=str(w.get("status", "")))
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        for w in sekcja.get("wygasania") or []:
            try:
                self.wygasania.append(Wydarzenie(
                    czas=float(w["czas"]), status=str(w.get("status", ""))))
            except (KeyError, TypeError, ValueError):
                continue
        self.powtorne_wykrycia = int(sekcja.get("powtorne_wykrycia", 0) or 0)

    def _zapisz(self) -> None:
        dane = {
            "wersja": WERSJA,
            "zapisano": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sesje": {
                "logowania": [{"czas": w.czas, "status": w.status} for w in self.logowania],
                "wykrycia": [{"czas": w.czas, "status": w.status} for w in self.wykrycia],
                "wygasania": [
                    {"czas": w.czas, "status": w.status, "login": self._login_dla(w.czas)}
                    for w in self.wygasania
                ],
                "powtorne_wykrycia": self.powtorne_wykrycia,
            },
            "typy": {
                nazwa: {
                    "sukcesy": [{"czas": w.czas, "status": w.status} for w in stan.sukcesy],
                    "odmowy": [{"czas": w.czas, "status": w.status} for w in stan.odmowy],
                    "awarie": [{"czas": w.czas, "status": w.status} for w in stan.awarie],
                }
                for nazwa, stan in self._typy.items()
            },
        }
        self.sciezka.parent.mkdir(parents=True, exist_ok=True)
        # tmp w tym samym katalogu — inaczej rename() cross-device failuje
        uchwyt, tmp = tempfile.mkstemp(dir=str(self.sciezka.parent), prefix=".limits-", suffix=".tmp")
        try:
            with os.fdopen(uchwyt, "w", encoding="utf-8") as fh:
                json.dump(dane, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.sciezka)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.sciezka, 0o600)
        except OSError:
            pass

    # --- zapis zdarzen ---------------------------------------------------
    def _stan(self, typ: str) -> StanTypu:
        if typ not in self._typy:
            self._typy[typ] = StanTypu(typ)
        return self._typy[typ]

    def rejestruj_sukces(self, typ: str, *, czas: float | None = None) -> None:
        with self._blokada:
            self._stan(typ).sukcesy.append(
                Wydarzenie(czas=czas if czas is not None else time.time(), status="completed")
            )
            self._zapisz()

    def rejestruj_odmowe(
        self, typ: str, status: Any, *, czas: float | None = None
    ) -> bool:
        """Zapisuje odmowe. Zwraca True, gdy to limit (a nie zwykla awaria)."""
        with self._blokada:
            stan = self._stan(typ)
            w = Wydarzenie(
                czas=czas if czas is not None else time.time(),
                status=str(getattr(status, "status", status)),
            )
            if czy_to_limit(status):
                stan.odmowy.append(w)
                self._zapisz()
                return True
            stan.awarie.append(w)
            self._zapisz()
            return False

    # --- odczyt ----------------------------------------------------------
    def zapisz_wynik(
        self, typ: str, status: Any, *, czas: float | None = None
    ) -> tuple[bool, str]:
        """Zapisuje wynik generowania: jeden wynik = jedno zdarzenie.

        Zwraca `(czy_limit, opis)`. Wyjatkiem moze byc `status` — timeout
        oczekiwania nie jest limitem, wiec trafia do awarii, a nie do odmow.
        Wywolywanie `rejestruj_odmowe` dwa razy pod rzad dalo dwa zdarzenia
        z jednego zdarzenia, bo zapisuje niezaleznie od wyniku testu limitu.
        """
        if str(getattr(status, "status", "")) == "completed":
            self.rejestruj_sukces(typ, czas=czas)
            return False, "sukces"
        to_limit = self.rejestruj_odmowe(typ, status, czas=czas)
        return to_limit, ("LIMIT KWOTY" if to_limit else "awaria (nie limit)")

    def zdarzenia(self, typ: str) -> list[Wydarzenie]:
        return self._stan(typ).wszystkie

    def ostatni_limit(self, typ: str) -> Wydarzenie | None:
        odmowy = self._stan(typ).odmowy
        return max(odmowy, key=lambda w: w.czas) if odmowy else None

    def sukcesy_od_ostatniego_limitu(self, typ: str) -> int:
        limit = self.ostatni_limit(typ)
        if limit is None:
            return len(self._stan(typ).sukcesy)
        return sum(1 for w in self._stan(typ).sukcesy if w.czas > limit.czas)

    def raport(self, typ: str) -> dict[str, Any]:
        stan = self._stan(typ)
        okno = stan.granice_okna()
        ostatni = self.ostatni_limit(typ)
        return {
            "typ": typ,
            "sukcesy": len(stan.sukcesy),
            "limity": len(stan.odmowy),
            "awarie_inne": len(stan.awarie),
            "sukcesy_od_ostatniego_limitu": self.sukcesy_od_ostatniego_limitu(typ),
            "okno_resetu_godziny_max": okno["okno_resetu_godziny_max"],
            "pary_limit_sukces": okno["pary"],
            "ostatni_limit": ostatni.iso() if ostatni else None,
            "znasz_juz_okno": okno["okno_resetu_godziny_max"] is not None,
        }

    def wszystkie_raporty(self) -> list[dict[str, Any]]:
        return [self.raport(t) for t in sorted(self._typy)]

    def _login_dla(self, czas: float) -> float | None:
        """Najpozniejsze logowanie, ktore nastapilo nie pozniej niz `czas`."""
        wcześniejsze = [w.czas for w in self.logowania if w.czas <= czas]
        return max(wcześniejsze) if wcześniejsze else None

    def rejestruj_sesje_zywa(self, *, czas: float | None = None) -> None:
        """Zapisuje fakt, ze uwierzytelnienie sie powiodlo (token pobrany).

        Bez tego zdarzenia nie da sie policzyc zycia sesji: roznica miedzy
        dwoma wykryciami nie jest dlugoscia sesji, tylko odstepem miedzy
        podejrzeniami o tej samej martwej sesji.
        """
        with self._blokada:
            self.logowania.append(Wydarzenie(
                czas=czas if czas is not None else time.time(), status="ok"))
            self._zapisz()

    def rejestruj_wygasniecie_sesji(
        self, wyjatek: BaseException, *, czas: float | None = None
    ) -> bool:
        """Zapisuje wykrycie martwej sesji. Zwraca True, gdy to faktycznie sesja.

        Rozroznia dwa zdarzenia, ktore wczesniej byly jednym:
          * `wygasanie` — znamy udane logowanie, ktorego nikt nie zamknal,
          * `powtorne wykrycie` — ta sama martwa sesja zgloszona drugi raz
            (np. po restarcie daemona). Nie jest nowym wygasnieciem i nie
            moze wydluzacz zycia sesji.

        Osobne od limitow kwoty z dwoch powodow: pada pozna wczesniej (przy
        budowie klienta, nie przy generowaniu) i resetuje sie w skali
        tygodni, a nie godzin. W jednym worku wyznaczanie okna byloby smieciowe.
        """
        if not czy_to_wygasniecie_sesji(wyjatek):
            return False
        kiedy = czas if czas is not None else time.time()
        with self._blokada:
            self.wykrycia.append(Wydarzenie(czas=kiedy, status=str(wyjatek)[:200]))
            ostatnie_wygasanie = max((w.czas for w in self.wygasania), default=None)
            if ostatnie_wygasanie is None:
                # Pierwsze wykrycie w zapisanej historii: to prawdziwe wygasniecie,
                # po prostu nie znamy kiedy ta sesja sie zaczela (mogla zaczac sie
                # zanim w ogole zapisalismy). Zapisujemy ja BEZ znanego poczatku,
                # dzieki czemu nie wydluza zycia, ale nie ginie z historii.
                self.wygasania.append(Wydarzenie(czas=kiedy, status=str(wyjatek)[:200]))
            elif any(w.czas > ostatnie_wygasanie for w in self.logowania):
                # Od ostatniego wygasniecia bylo nowe logowanie — to kolejna sesja.
                self.wygasania.append(Wydarzenie(czas=kiedy, status=str(wyjatek)[:200]))
            else:
                # Ta sama martwa sesja zgloszona ponownie, np. po restarcie daemona.
                self.powtorne_wykrycia += 1
            self._zapisz()
        return True

    def raport_sesji(self) -> dict[str, Any]:
        """Stan sesji Google. Zycie liczone WYLACZNIE z par logowanie->wygasanie.

        Bez pary zwracamy `zycie_godziny = None` i wypisujemy powod. Wczesniejsza
        wersja liczyla roznice miedzy kolejnymi wykryciami i raportowala
        "zycie 0,58 h" dla jednej martwej sesji zgloszonej dwa razy.
        """
        teraz = time.time()
        ostatnie_wykrycie = max((w.czas for w in self.wykrycia), default=None)
        ostatnie_wygasanie = max((w.czas for w in self.wygasania), default=None)
        ostatnie_logowanie = max((w.czas for w in self.logowania), default=None)
        zycia = sorted(
            w.czas - self._login_dla(w.czas)
            for w in self.wygasania
            if self._login_dla(w.czas) is not None
        )
        raport: dict[str, Any] = {
            "logowania": len(self.logowania),
            "wykrycia": len(self.wykrycia),
            "wygasania": len(self.wygasania),
            "powtorne_wykrycia": self.powtorne_wykrycia,
            "ostatnie_logowanie": _iso(ostatnie_logowanie),
            "ostatnie_wygasniecie": _iso(ostatnie_wygasanie),
            "ostatnie_wykrycie": _iso(ostatnie_wykrycie),
            "sekundy_od_wykrycia": (round(teraz - ostatnie_wykrycie, 1)
                                    if ostatnie_wykrycie else None),
            "zycie_godziny": None,
            "zycie_powod": "brak pary logowanie -> wygasniecie",
        }
        if zycia:
            raport["zycie_godziny"] = round(statistics.median(zycia) / 3600.0, 2)
            raport["zycie_powod"] = f"mediana z {len(zycia)} zamknietej sesji"
        elif self.logowania and not self.wygasania:
            raport["zycie_powod"] = "sesja od ostatniego logowania jeszcze zyje"
        return raport

    def reset(self) -> None:
        """Czysci zdarzenia. Do badanego okna — nie do dzialajacego boota."""
        with self._blokada:
            self._typy = {t: StanTypu(t) for t in TYPY}
            self.logowania = []
            self.wykrycia = []
            self.wygasania = []
            self.powtorne_wykrycia = 0
            self._zapisz()