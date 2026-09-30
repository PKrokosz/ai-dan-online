"""Odala bota w tle: podwojny fork + setsid + pidfile + log.

Whitelist serwera pozwala wykonywac tylko `python3.11`, wiec nie mozna
odpalic `screen`/`nohup` — daemonizer musi byc w Pythonie.

Uzycie:
    python3.11 daemon.py          # uruchom (albo: juz dziala)
    python3.11 daemon.py status   # stan
    python3.11 daemon.py stop     # zatrzymaj
    python3.11 daemon.py log      # ostatnie linie logu
"""
import os
import signal
import sys
import time
from pathlib import Path

KATALOG = Path(__file__).resolve().parent
LOG = KATALOG / "bot.log"
PIDFILE = KATALOG / "bot.pid"
BOT = KATALOG / "run.py"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError) as exc:
        # PermissionError = istnieje, ale nalezy do innego uzytkownika
        return isinstance(exc, PermissionError)
    except Exception:  # noqa: BLE001
        return False
    return True


def read_pid() -> int | None:
    if not PIDFILE.exists():
        return None
    try:
        pid = int(PIDFILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    return pid if pid_alive(pid) else None


def status() -> int:
    pid = read_pid()
    if pid is None:
        print("bot: NIE DZIALA (brak pidfile lub proces martwy)")
        return 1
    print(f"bot: dziala, PID={pid}")
    if LOG.exists():
        print(f"log: {LOG} ({LOG.stat().st_size} B)")
    return 0


def stop() -> int:
    pid = read_pid()
    if pid is None:
        print("bot: nie dziala, nic do zatrzymania")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.5)
        if not pid_alive(pid):
            print(f"bot: zatrzymany (PID {pid})")
            PIDFILE.unlink(missing_ok=True)
            return 0
    os.kill(pid, signal.SIGKILL)
    PIDFILE.unlink(missing_ok=True)
    print(f"bot: zabity (SIGKILL, PID {pid})")
    return 0


def start() -> int:
    if not BOT.exists():
        print(f"brak {BOT}")
        return 1
    if not (KATALOG / ".env").exists():
        print(f"brak {KATALOG / '.env'}")
        return 1

    pid = read_pid()
    if pid is not None:
        print(f"bot juz dziala (PID {pid}) — nie uruchamiam drugiego")
        return 0

    log_fd = os.open(str(LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)

    if os.fork() > 0:
        # rodzic: nie czeka na dziecko, wychodzi
        os.close(log_fd)
        for _ in range(30):
            time.sleep(0.5)
            pid = read_pid()
            if pid is not None:
                print(f"bot wystartowal w tle, PID={pid}")
                print(f"log: {LOG}")
                return 0
        print("bot nie zapisal pidfile w ciagu 15 s — sprawdz log")
        return 1

    # pierwsze dziecko: nowa sesja
    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    # drugie dziecko: prawdziwy daemon
    os.chdir(str(KATALOG))
    os.umask(0o077)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(devnull)
    os.close(log_fd)

    pid = os.getpid()
    PIDFILE.write_text(str(pid), encoding="utf-8")
    os.chmod(PIDFILE, 0o600)
    os.execv(sys.executable, [sys.executable, str(BOT)])
    os._exit(127)


def log() -> int:
    if not LOG.exists():
        print("brak logu")
        return 1
    print(f"--- ostatnie 40 linii {LOG} ---")
    print("".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-40:]))
    return 0


if __name__ == "__main__":
    akcja = sys.argv[1] if len(sys.argv) > 1 else "start"
    try:
        sys.exit({"start": start, "stop": stop, "status": status, "log": log}[akcja]())
    except KeyError:
        print("uzycie: daemon.py [start|stop|status|log]")
        sys.exit(2)