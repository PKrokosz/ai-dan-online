#!/usr/bin/env python3
"""Uruchamia bota, wczytujac sekrety z pliku .env.

Dlaczego osobny launcher, a nie `python bot.py`: token Discord nie powinien
nigdy trafic do argv (widoczny w `ps`, w historii powloki i w logach
procesow), wiec wczytujemy .env w procesie i dopiero potem startujemy bota.
Przy okazji bot.py zostaje czysty — nie wie nic o plikach konfiguracyjnych.

Uzycie:
    python run.py                 # czyta .env z katalogu repo
    python run.py --env sciezka   # inny plik .env
"""

import argparse
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(path: Path) -> tuple[list[str], list[str]]:
    """Wczytuje KEY=VALUE. Zwraca (wczytane_klucze, brakujace_klucze)."""
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ[key] = val
            loaded.append(key)
    missing = [k for k in ("NOTEBOOK_ID", "DISCORD_TOKEN") if not os.environ.get(k)]
    return loaded, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Start bota ai-dan")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="sciezka do pliku .env")
    args = parser.parse_args()

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"Brak pliku konfiguracyjnego: {env_path}")
        print("Sklonuj .env.example do .env i uzupelnij NOTEBOOK_ID oraz DISCORD_TOKEN.")
        return 2

    loaded, missing = load_env(env_path)
    print(f"konfiguracja: {env_path} ({len(loaded)} zmiennych)")

    # bled o nazwie sekretu zamiast jego wartosci
    for key in missing:
        print(f"BRAK ZMIENNEJ: {key}")
    if missing:
        return 2

    # sekrety w argv nie wypisujemy — tylko dlugosc
    tok = os.environ.get("DISCORD_TOKEN", "")
    print(f"DISCORD_TOKEN: {len(tok)} znakow (ukryty)")
    print(f"NOTEBOOK_ID:   {os.environ['NOTEBOOK_ID']}")
    print(f"start: {ROOT / 'bot.py'}")

    try:
        runpy.run_path(str(ROOT / "bot.py"), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        print("zatrzymano (Ctrl+C)")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())