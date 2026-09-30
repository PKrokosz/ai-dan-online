"""Sprawdza uprawnienia plikow — na serwerze, gdzie `st_mode` dziala.

Test na Windows jest bezwartosciowy: `st_mode` zwraca 0o666 dla kazdego
pliku, niezaleznie od `chmod`. Jedyny uczciwy test jest tam, gdzie
uprawnienia sa realnym mechanizmem.
"""

import sys
from pathlib import Path

sys.path.insert(0, "/home/srv120794/ai-dan")

import kolejka

PLIKI = {
    "zadania.json": kolejka.PLIK,
    ".env": Path("/home/srv120794/ai-dan/.env"),
    "limits.json": Path("/home/srv120794/ai-dan/limits.json"),
    "storage_state.json": Path("/home/srv120794/ai-dan/nlm-home/storage_state.json"),
}

ok = True
print("=== uprawnienia plikow wrazliwych ===")
for nazwa, sciezka in PLIKI.items():
    if not sciezka.exists():
        print(f"  {nazwa:22s} BRAK PLIKU (jeszcze nie utworzony)")
        continue
    try:
        tryb = sciezka.stat().st_mode & 0o777
    except OSError as exc:
        print(f"  {nazwa:22s} blad: {exc}")
        ok = False
        continue
    oczekiwane = 0o600
    zgodne = tryb == oczekiwane
    print(f"  {nazwa:22s} {oct(tryb)}  {'OK' if zgodne else 'OCZEKIWANO 0o600'}")
    if not zgodne:
        ok = False

print()
print("=== katalog sesji (ma byc 0700) ===")
katalog = Path("/home/srv120794/ai-dan/nlm-home")
if katalog.exists():
    tryb = katalog.stat().st_mode & 0o777
    print(f"  nlm-home {oct(tryb)}  {'OK' if tryb == 0o700 else 'OCZEKIWANO 0o700'}")
    if tryb != 0o700:
        ok = False

print()
print("=== proba zapisu kolejki i odczyt trybu ===")
kolejka.dodaj("test_uprawnienia", channel_id=0, author_id=0)
tryb = kolejka.PLIK.stat().st_mode & 0o777
print(f"  po zapisie: {oct(tryb)}  {'OK' if tryb == 0o600 else 'BLED — CHMOD NIE DZIALA'}")
if tryb != 0o600:
    ok = False
# sprzatanie
stan = kolejka.wczytaj()
stan["zadania"] = {}
kolejka.zapisz(stan)

print()
print("WYNIOSEK:", "OK" if ok else "SA PROBLEMY")
sys.exit(0 if ok else 1)