"""Kolejka generowania — pracuje OSOBNO od czatu.

Ustalone 30.09: generowanie ma być oddzielone od czatu, bo `/audio` potrafi
trwać kilka minut, a `/ai-dan` musi odpowiadać natychmiast. Jedna kolejka,
jeden worker, jeden zadanie naraz — dwie rzeczy naraz to dwa wygenerowania
i dwa limity, a tego nikt nie zamawiał.

Kolejka przeżywa restart: `zadania.json` na dysku. Przerwane zadania wracaja
jako `przerwane`, nie jako `czekajace` — inaczej po restarcie czekalyby w nieskonczonosc
na sesje, ktora juz nie istnieje.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

KATALOG = Path(__file__).resolve().parent
PLIK = KATALOG / "zadania.json"
LIMIT_UROJONYCH = 5          # ile zadan czeka jednoczesnie
STANY = ("czekajace", "w_toku", "gotowe", "nieudane", "przerwane", "limit")


def _stan_poczatkowy() -> dict[str, Any]:
    return {"zadania": {}, "historia": []}


def wczytaj() -> dict[str, Any]:
    if not PLIK.exists():
        return _stan_poczatkowy()
    try:
        d = json.loads(PLIK.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or "zadania" not in d:
            return _stan_poczatkowy()
        return d
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # uszkodzony plik = brak danych, nie awaria
        return _stan_poczatkowy()


def zapisz(stan: dict[str, Any]) -> None:
    """Zapis atomowy — kolejka nie moze sie uciac w polu zapisu.

    `umask` jest ustawiony przez daemon (0o077) w procesie produkcyjnym, ale
    testy i uruchomienie reczne dziedzicza umask shella, wiec uprawnienia
    ustawiamy jawnie. Plik zawiera ID kanałów i użytkowników.
    """
    KATALOG.mkdir(parents=True, exist_ok=True)
    tmp = PLIK.with_suffix(".tmp")
    tmp.write_text(json.dumps(stan, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)          # PRZED rename — inaczej okno na zly tryb
    except OSError:
        pass
    os.replace(tmp, PLIK)


def dodaj(typ: str, *, channel_id: int, author_id: int,
          parametry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Wstawia zadanie. Zwraca wpis z nadanym id."""
    stan = wczytaj()
    czekajace = [z for z in stan["zadania"].values() if z["stan"] in ("czekajace", "w_toku")]
    if len(czekajace) >= LIMIT_UROJONYCH:
        raise ValueError(
            f"kolejka pelna ({LIMIT_UROJONYCH} zadan w toku) — sprobuj za chwile")
    zadanie_id = f"z{int(time.time() * 1000)}"
    wpis = {
        "id": zadanie_id,
        "typ": typ,
        "stan": "czekajace",
        "channel_id": channel_id,
        "author_id": author_id,
        "parametry": parametry or {},
        "utworzono": time.strftime("%Y-%m-%d %H:%M:%S"),
        "wynik": None,
    }
    stan["zadania"][zadanie_id] = wpis
    zapisz(stan)
    return wpis


def nastepne() -> dict[str, Any] | None:
    """Pierwsze czekajace zadanie. Kolejnosc wg czasu utworzenia."""
    stan = wczytaj()
    czekajace = [z for z in stan["zadania"].values() if z["stan"] == "czekajace"]
    if not czekajace:
        return None
    return min(czekajace, key=lambda z: z["utworzono"])


def oznacz(zadanie_id: str, nowy_stan: str, wynik: Any = None) -> dict[str, Any] | None:
    stan = wczytaj()
    z = stan["zadania"].get(zadanie_id)
    if z is None:
        return None
    z["stan"] = nowy_stan
    if wynik is not None:
        z["wynik"] = wynik
    z["zakonczono"] = time.strftime("%Y-%m-%d %H:%M:%S")
    zapisz(stan)
    return z


def aktywne() -> list[dict[str, Any]]:
    stan = wczytaj()
    return [z for z in stan["zadania"].values() if z["stan"] in ("czekajace", "w_toku")]


def ostatnie(n: int = 5) -> list[dict[str, Any]]:
    stan = wczytaj()
    wszystkie = sorted(stan["zadania"].values(), key=lambda z: z["utworzono"], reverse=True)
    return wszystkie[:n]


def sprzataj_przerwane() -> int:
    """Po restarcie: zadania `w_toku` nie moga czekac na wynik.

    Proces, ktory je prowadzil, nie istnieje. Zostawiaja je na zawsze
    w stanie `w_toku`, a kolejka nigdy nie ruszy. Oznaczamy jako `przerwane`.
    """
    stan = wczytaj()
    zmienione = 0
    for z in stan["zadania"].values():
        if z["stan"] == "w_toku":
            z["stan"] = "przerwane"
            z["wynik"] = "proces zostal zatrzymany w trakcie generowania"
            z["zakonczono"] = time.strftime("%Y-%m-%d %H:%M:%S")
            zmienione += 1
    if zmienione:
        zapisz(stan)
    return zmienione


def podsumowanie() -> dict[str, int]:
    stan = wczytaj()
    licz: dict[str, int] = {s: 0 for s in STANY}
    for z in stan["zadania"].values():
        licz[z["stan"]] = licz.get(z["stan"], 0) + 1
    return licz
