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
        self.sesje: list[Wydarzenie] = []
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
        for w in surowe.get("sesje") or []:
            try:
                self.sesje.append(Wydarzenie(czas=float(w["czas"]), status=str(w.get("status", ""))))
            except (KeyError, TypeError, ValueError):
                continue

    def _zapisz(self) -> None:
        dane = {
            "wersja": WERSJA,
            "zapisano": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sesje": [{"czas": w.czas, "status": w.status} for w in self.sesje],
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

    def rejestruj_wygasniecie_sesji(
        self, wyjatek: BaseException, *, czas: float | None = None
    ) -> bool:
        """Zapisuje wygasniecie sesji. Zwraca True, gdy to faktycznie sesja.

        Osobne od limitow kwoty z dwóch powodow: pada pozna wczesniej (przy
        budowie klienta, nie przy generowaniu) i resetuje sie w skali
        tygodni, a nie godzin. W jednym worku wyznaczanie okna byloby smieciowe.
        """
        if not czy_to_wygasniecie_sesji(wyjatek):
            return False
        with self._blokada:
            self.sesje.append(Wydarzenie(
                czas=czas if czas is not None else time.time(),
                status=str(wyjatek)[:200],
            ))
            self._zapisz()
        return True

    def raport_sesji(self) -> dict[str, Any]:
        """Historia wygasan. Dlugosc zycia sesji liczona miedzy nimi."""
        if not self.sesje:
            return {"wygasniecia": 0, "ostatnie": None, "zycie_godziny": None}
        uporzadkowane = sorted(self.sesje, key=lambda w: w.czas)
        ostatnie = uporzadkowane[-1]
        zycie = None
        if len(uporzadkowane) >= 2:
            roznica = ostatnie.czas - uporzadkowane[-2].czas
            zycie = round(roznica / 3600.0, 2)
        return {
            "wygasniecia": len(self.sesje),
            "ostatnie": ostatnie.iso(),
            "ostatni_tekst": ostatnie.status[:160],
            "zycie_godziny": zycie,
        }

    def reset(self) -> None:
        """Czysci zdarzenia. Do badanego okna — nie do dzialajacego boota."""
        with self._blokada:
            self._typy = {t: StanTypu(t) for t in TYPY}
            self.sesje = []
            self._zapisz()