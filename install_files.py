"""Wstawia pliki bota do katalogu aplikacji i ustawia uprawnienia.

Rozszerzenie setup_app.py: obsluguje takze podkatalog tools/ i tests/ oraz
limits.py. Bez tego kolejne wdrozenie wymagalo recznego dopychania katalogow.

Uwaga o FTP: logowanie ftp jest juz w katalogu domowym, a `remote_root`
narzedzia `site_deploy` jest skladany wzglednie niego. Dlatego dla tego
katalogu wazne sa `remote_root="/"` i `dest` względne — inaczej plik ląduje
w `/home/srv120794/home/srv120794/...`, a narzedzie i tak zwraca
`uploaded` bez `errors`. Dlatego manifest ponizej musi wymieniac KAZDY plik
aplikacji: plik spoza listy zostaje stary po cichu, a instalacja konczy sie
`ok` bez sladu.
"""
import os
import shutil
import sys
from pathlib import Path

DOMY = Path("/home/srv120794")
APKA = DOMY / "ai-dan"

# pliki proste: nazwa -> nazwa docelowa w aplikacji
PLIKI_PROSTE = [
    "limits.py", "daemon.py", "bot.py", "run.py",
    # dodane 30.09 — modulow nie bylo w manifeście, wiec `install_files.py`
    # zostawial w aplikacji ich poprzednie wersje (brak rozszerzen w
    # artifacts.py, brak refresh.py po restarcie hosta)
    "artifacts.py", "kolejka.py", "refresh.py",
]
# z katalogu glownego do podkatalogow
MAPA = {
    "limits_probe.py": "tools/limits_probe.py",
    "reauth.py": "tools/reauth.py",
    "cookies_z_profilu.py": "tools/cookies_z_profilu.py",
    "test_uprawnienia.py": "tools/test_uprawnienia.py",
    "test_limits.py": "tests/test_limits.py",
    "test_bot.py": "tests/test_bot.py",
    "test_artifacts.py": "tests/test_artifacts.py",
    "test_kolejka.py": "tests/test_kolejka.py",
    "test_reauth.py": "tests/test_reauth.py",
    "test_negatywny.py": "tests/test_negatywny.py",
    "setup_app.py": None,  # juz jest w aplikacji, nie nadpisujemy
}
# modulow NIE wolno kopiowac zglownego katalogu, bo maja wlasne podkatalogi
# i instalacja skasowalaby im strukture


def wstaw(zrodlo: Path, cel: Path, tryb: int) -> bool:
    if not zrodlo.exists():
        print(f"  BRAK pliku: {zrodlo}")
        return False
    cel.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(zrodlo, cel)
    os.chmod(cel, tryb)
    print(f"  {zrodlo.name:<22} -> {cel.relative_to(APKA)}  ({cel.stat().st_size} B)")
    return True


def main() -> int:
    ok = True
    for nazwa in PLIKI_PROSTE:
        zrodlo = DOMY / nazwa
        if zrodlo.exists():
            ok &= wstaw(zrodlo, APKA / nazwa, 0o600)
        elif (APKA / nazwa).exists():
            print(f"  {nazwa:<22} -> juz w aplikacji")
        else:
            print(f"  BRAK: {nazwa} nigdzie")
            ok = False

    for nazwa, cel in MAPA.items():
        if cel is None:
            continue
        zrodlo = DOMY / nazwa
        if zrodlo.exists():
            ok &= wstaw(zrodlo, APKA / cel, 0o600)

    print("\nstan katalogu aplikacji:")
    for p in sorted(APKA.rglob("*")):
        if p.is_file() and "__pycache__" not in str(p):
            print(f"  {oct(os.stat(p).st_mode & 0o777)}  {p.stat().st_size:>8} B  {p.relative_to(APKA)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
