"""Porzadkuje katalog aplikacji na serwerze: przenosi pliki i ustawia uprawnienia.

Powod, dla ktorego istnieje: FTP na tym serwerze duplikuje katalog domowy
(cwd `/home/srv120794` -> `/home/srv120794/home/srv120794`), wiec pliki
wgrywaja sie do `/home/srv120794/`, a aplikacja ma mieszkac w
`/home/srv120794/ai-dan/`. `chmod` nie jest w whitelistcie shella, wiec
uprawnienia ustawiam tu.
"""
import os
import shutil
import sys
from pathlib import Path

DOMY = Path("/home/srv120794")
APKA = DOMY / "ai-dan"
PLIKI = ["bot.py", "run.py", "daemon.py", ".env"]


def main() -> int:
    APKA.mkdir(parents=True, exist_ok=True)
    os.chmod(APKA, 0o700)

    for nazwa in PLIKI:
        zrodlo = DOMY / nazwa
        cel = APKA / nazwa
        if not zrodlo.exists():
            if cel.exists():
                print(f"  {nazwa}: juz w aplikacji (pominieto)")
                continue
            print(f"  {nazwa}: BRAK w {DOMY} i w aplikacji")
            continue
        shutil.move(str(zrodlo), str(cel))
        print(f"  {nazwa}: przeniesiony z katalogu domowego")

    # uprawnienia: .env i sesja tylko dla wlasciciela
    for nazwa, tryb in [(".env", 0o600), ("bot.py", 0o600), ("run.py", 0o600), ("daemon.py", 0o600)]:
        sciezka = APKA / nazwa
        if sciezka.exists():
            os.chmod(sciezka, tryb)

    print("\nstan katalogu aplikacji:")
    for p in sorted(APKA.rglob("*")):
        if p.is_file():
            print(f"  {oct(os.stat(p).st_mode & 0o777)}  {p.stat().st_size:>8} B  {p.relative_to(APKA)}")

    print("\nuprawnienia katalogu aplikacji:", oct(os.stat(APKA).st_mode & 0o777))
    nlm = APKA / "nlm-home"
    if nlm.exists():
        print("katalog sesji:", nlm, oct(os.stat(nlm).st_mode & 0o777))
        sesja = nlm / "storage_state.json"
        if sesja.exists():
            print("plik sesji:", sesja.stat().st_size, "B", oct(os.stat(sesja).st_mode & 0o777))
    return 0


if __name__ == "__main__":
    sys.exit(main())