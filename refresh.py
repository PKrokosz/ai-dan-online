"""Odwieza sesji Google — automatycznie, w tle, bez czlowieka.

Dlaczego to w ogole istnieje
-----------------------------
Sesja wygasla trzy razy w ciagu jednego dnia: po 2,5 h, po 1,6 h, po ~2 h.
Przyczyny nie znamy. Reakcja na to byla prosta i bledna: kazdy raz prosilem
o nowe logowanie. To oznacza 3-5 razy dziennie proszenia czlowieka o wpisanie
hasla — nieakceptowalne dla uslugi, ktora ma dzialac sama.

Rozwiazanie jest w bibliotece, nie wymyslone tutaj. `notebooklm auth refresh`
opisuje sam: "one-shot keepalive ... Designed to be scheduled by the OS so that
an otherwise-idle profile does not stale out between user-driven calls.
Cadence: 15-20 minutes."

Wczesniej testowalem refresh PO smierci sesji i wyciagnalem wniosek, ze nie
dziala. To byl test po fakcie, nie test zapobiegania — nie mialo prawa zadzialac.
Zmierzono 30.09: refresh na zywej sesji NIE psuje sesji, a slodek na dysku
faktycznie sie zmienia (rotacja SIDTS).

Co to robi, a czego nie
-----------------------
  * co ~15 min: pobiera token, wymusza rotacje ciasteczek, zapisuje slodek
  * przy wygaslej sesji: loguje i zapisuje zdarzenie w liczniku, konczy sie
  * NIE potrafi wskrzesic sesji, ktora Google juz uniewaznil. Tego nie da sie
    zautomatyzowac — wymaga hasla i 2FA. Dlatego obok jest komenda /sesja
    i gotowa procedura.

Czego to NIE gwarantuje
-----------------------
Google moze uniewaznic sesje z wlasnej inicjatywy, wtedy zaden heartbeat tego
nie pomoze. To ryzyko zostaje i jest opisane w docs/ZNANE-PROBLEMY.md. To narzedzie
zmniejsza prawdopodobnosc, nie eliminuje.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

KATALOG = Path(__file__).resolve().parent
NB = "65f678e6-086c-43bf-a14a-2471286c35c0"
INTERWAL_S = int(os.getenv("ODWIEZKA_CO", "900"))   # 15 min
LOG = KATALOG / "refresh.log"
PIDFILE = KATALOG / "refresh.pid"


def log(tekst: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {tekst}", flush=True)


def wczytaj_env() -> None:
    for raw in (KATALOG / ".env").read_text(encoding="utf-8").splitlines():
        linia = raw.strip()
        if linia and not linia.startswith("#") and "=" in linia:
            k, _, v = linia.partition("=")
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


async def jedna_runda(licznik) -> bool:
    """Jedna proba odwiezenia. Zwraca True gdy sesja zyje."""
    from notebooklm import NotebookLMClient

    try:
        klient = await NotebookLMClient.from_storage(
            keepalive=None, chat_timeout=60.0).__aenter__()
    except Exception as exc:
        if licznik is not None and licznik.rejestruj_wygasniecie_sesji(exc):
            log(f"SESJA WYGASLA — nie da sie tego naprawic automatycznie: {str(exc)[:100]}")
            return False
        log(f"blad budowy klienta (nie wygasniecie?): {type(exc).__name__}: {str(exc)[:100]}")
        return False

    try:
        await klient.sources.list(NB)     # uwierzytelnione wywolanie
        if licznik is not None and licznik.rejestruj_sesje_zywa():
            log("sesja zyje — token pobrany, ciasteczka zrotowane (nowa sesja)")
        else:
            log("sesja zyje — token pobrany, ciasteczka zrotowane")
        return True
    except Exception as exc:
        log(f"token nie dziala: {type(exc).__name__}: {str(exc)[:120]}")
        return False
    finally:
        await klient.__aexit__(None, None, None)


async def petla() -> int:
    wczytaj_env()
    sys.path.insert(0, str(KATALOG))
    licznik = None
    try:
        from limits import LicznikLimitow
        licznik = LicznikLimitow(KATALOG / "limits.json")
    except Exception as exc:
        log(f"licznik niedostepny (nie blokuje odwiezania): {exc}")

    log(f"start odwiezania, interwal {INTERWAL_S} s")
    await jedna_runda(licznik)
    while True:
        time.sleep(INTERWAL_S)
        try:
            await jedna_runda(licznik)
        except Exception as exc:
            log(f"blad w rundzie (petla leci dalej): {type(exc).__name__}: {str(exc)[:100]}")


def start() -> int:
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text().strip())
            os.kill(pid, 0)
            print(f"odwiezanie juz leci (PID {pid})")
            return 0
        except (OSError, ValueError):
            PIDFILE.unlink(missing_ok=True)

    log_fd = os.open(str(LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    if os.fork() > 0:
        os.close(log_fd)
        for _ in range(20):
            time.sleep(0.5)
            if PIDFILE.exists():
                print(f"odwiezanie wystartowalo w tle, PID={PIDFILE.read_text().strip()}")
                print(f"log: {LOG}")
                return 0
        print("nie zapisalo pidfile")
        return 1

    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir(str(KATALOG))
    os.umask(0o077)
    dn = os.open(os.devnull, os.O_RDONLY)
    os.dup2(dn, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(dn)
    os.close(log_fd)
    PIDFILE.write_text(str(os.getpid()), encoding="utf-8")
    os.chmod(PIDFILE, 0o600)
    sys.exit(asyncio.run(petla()))


def status() -> int:
    if not PIDFILE.exists():
        print("odwiezanie: NIE DZIALA")
        return 1
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        print("odwiezanie: martwe (pidfile nieaktualny)")
        return 1
    print(f"odwiezanie: dziala (PID {pid}), interwal {INTERWAL_S} s")
    if LOG.exists():
        linie = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        for l in linie[-4:]:
            print("   " + l)
    return 0


def stop() -> int:
    if not PIDFILE.exists():
        print("odwiezanie: nie dziala")
        return 0
    pid = int(PIDFILE.read_text().strip())
    try:
        os.kill(pid, 15)
    except OSError:
        print("proces juz nie istnieje")
        PIDFILE.unlink(missing_ok=True)
        return 0
    for _ in range(20):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except OSError:
            PIDFILE.unlink(missing_ok=True)
            print(f"zatrzymane (PID {pid})")
            return 0
    print("nie zatrzymane")
    return 1


if __name__ == "__main__":
    akcja = sys.argv[1] if len(sys.argv) > 1 else "start"
    sys.exit({"start": start, "stop": stop, "status": status, "log": lambda: (print(
        LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else "brak logu"), 0)[1]
    }[akcja]())