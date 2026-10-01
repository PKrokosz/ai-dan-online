"""Wgrywa sesje na serwer i restartuje bota — zamyka petle reautoryzacji.

Dlaczego osobny plik: `reauth.py` odswieza plik LOKALNIE, ale serwer go nie
widzi. Bez tego kroku "codziennie robie reautoryzacje" nie znaczy nic dla bota.
To jest wlasnie brakujace ogniwo.

Transport jest wymieszany celowo:
- PLIK idzie FTP (`STOR` ze sciezka wzgledna — login jest juz w
  /home/srv120794, a `STOR` z bezwzgledna ladowal plik do
  /home/srv120794/home/srv120794/... i zwracalo przy tym "uploaded" bez bledow)
- SHELL (`/agentmg/api/shell`) robi tylko `cp` + `chmod 600` + restart, bo
  FTP nie ma dostepu do `nlm-home/` ("550 No such file or directory"), a sesja
  to ciasteczka Google i musi miec 0600 — samo STOR daje 0644, czyli sesja do
  odczytu przez innych na wspoldzielonym hostingu.

Sesja jest ~17 kB, a limit komendy shella to 1000 znakow, wiec plik NIE moze
isciec komenda — stad FTP.

Restart tylko gdy plik sie zmienil: bez tego kazde uruchomienie zadania
odbijalo by bota, a bot gubi wtedy pamiec rozmow.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cookies_z_profilu as ciasteczka_mod  # noqa: E402

DOMY_SERWERA = "srv120794"          # login FTP
KATALOG_APKI = "/home/srv120794/ai-dan"
CEL_SESJI = f"{KATALOG_APKI}/nlm-home/storage_state.json"
ETAP_SESJI = "sesja_nowa.json"      # w katalogu domowym, FTP widzi tylko to
DOMY_ENV = Path("C:/Users/admin/Desktop/pkrokosz.pl/mcp/.env")


def wczytaj_env() -> dict[str, str]:
    """Zmienne z mcp/.env. Zwraca kopie stringow, nigdy nie wypisuje wartosci."""
    if not DOMY_ENV.exists():
        return {}
    cfg: dict[str, str] = {}
    for linia in DOMY_ENV.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", linia)
        if m:
            cfg[m[1]] = m[2].strip().strip("\"'")
    return cfg


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def shell(cfg: dict[str, str], command: str, ponow: int = 4) -> dict[str, Any]:
    """POST na /agentmg/api/shell z ponawianiem przy 429.

    429 to nie awaria — to zadanie dzienne trafiło na limit i ma za chwile
    puścić. Bez ponawiania jedno wywolanie w zlym momencie zostawialoby serwer
    z nowym plikiem sesji i bez zrestartowanego bota, czyli wylacznie
    rozdroznionym stanem.
    """
    base = (cfg.get("AGENTMG_BASE_URL") or "").rstrip("/")
    key = cfg.get("AGENTMG_ADMIN_KEY") or cfg.get("AGENTMG_API_KEY")
    if not base or not key:
        return {"ok": False, "output": "brak AGENTMG_BASE_URL/AGENTMG_ADMIN_KEY"}
    dane = json.dumps({"command": command}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/agentmg/api/shell", data=dane, method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "X-API-Key": key},
    )
    for proba in range(ponow + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and proba < ponow:
                # rosnaco: 5 s, 10 s, 20 s, 40 s
                czas = 5 * (2 ** proba)
                print(f"  429 — ponawiam za {czas} s (próba {proba + 1}/{ponow})")
                time.sleep(czas)
                continue
            return {"ok": False, "output": f"blad http {exc.code}: {exc}"}
        except urllib.error.URLError as exc:
            if proba < ponow:
                czas = 5 * (2 ** proba)
                print(f"  niedostepny ({exc.reason}) — ponawiam za {czas} s")
                time.sleep(czas)
                continue
            return {"ok": False, "output": f"blad sieci: {exc}"}
    return {"ok": False, "output": "niepowodzenie po ponawianiu"}


def hash_na_serwerze(cfg: dict[str, str]) -> str:
    # `md5sum` NIE ma na whitlist shella (npm, node, ls, cat, pwd, which,
    # python3, python3.11, whoami, date, uname) — liczy wiec python3.11.
    # Potok `cat plik | md5sum` tez odpada: shell odrzuca potoki (403).
    r = shell(cfg, "python3.11 -c \"import hashlib;print(hashlib.md5(open('%s','rb').read()).hexdigest())\"" % CEL_SESJI)
    wyjscie = (r.get("output") or r.get("raw") or "").strip()
    for slowo in wyjscie.split():
        slowo = slowo.strip()
        if len(slowo) == 32 and all(c in "0123456789abcdef" for c in slowo):
            return slowo
    return ""


def znacznik_wgrania() -> Path:
    return ciasteczka_mod.katalog_bazowy() / ".notebooklm" / "ostatnie_wgranie.json"


def zapisz_znacznik(md5: str, bajty: int, stan: str) -> None:
    p = znacznik_wgrania()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "md5": md5, "bajty": bajty, "stan": stan,
        "czas": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, indent=2), encoding="utf-8")


def main() -> int:
    zrodlo = Path(os.environ.get("USERPROFILE", str(Path.home()))) / \
        ".notebooklm" / "sesja_na_serwer.json"
    if not zrodlo.exists():
        print(f"brak pliku sesji: {zrodlo}\nNajpierw: python tools/reauth.py")
        return 2
    tresc = zrodlo.read_bytes()
    lokalny = hashlib.md5(tresc).hexdigest()
    print(f"sesja lokalnie: {len(tresc)} B, md5={lokalny}")

    # Znacznik ostatniego UDANEGO wgrania. Bez tego zadanie dzienne musialo
    # pytac serwer o hash przy kazdym przebiegu, a API ma limit — przy 429
    # konczylo sie to bledem, mimo ze sesja na serwerze byla aktualna.
    # Najczestszy przypadek (nic sie nie zmienilo) teraz nie dotyka sieci.
    znak = znacznik_wgrania()
    if znak.exists():
        try:
            dane = json.loads(znak.read_text(encoding="utf-8"))
            if dane.get("md5") == lokalny:
                print("ostatnie wgranie ma ten sam md5 — nic nie robie")
                return 0
        except (OSError, ValueError):
            pass  # uszkodzony znacznik -> traktujemy jak brak

    cfg = wczytaj_env()
    for k in ("PKROKOSZ_FTP_HOST", "PKROKOSZ_FTP_USER", "PKROKOSZ_FTP_PASS"):
        if not cfg.get(k):
            print(f"brak {k} w {DOMY_ENV}")
            return 2

    zdalny = hash_na_serwerze(cfg)
    if zdalny == lokalny:
        print("serwer ma identyczna sesje — restart pomijam")
        zapisz_znacznik(lokalny, len(tresc), "serwer juz aktualny")
        return 0
    print(f"serwer ma inna sesje (md5={zdalny or 'nieczytelne'}) — wysylam")

    # 1. FTP: plik do katalogu domowego, sciezka WZGLEDNA
    import ftplib
    ftp = ftplib.FTP()
    ftp.connect(cfg["PKROKOSZ_FTP_HOST"], timeout=60)
    ftp.login(cfg["PKROKOSZ_FTP_USER"], cfg["PKROKOSZ_FTP_PASS"])
    ftp.set_pasv(True)
    ftp.voidcmd("TYPE I")
    import io
    ftp.storbinary("STOR " + ETAP_SESJI, io.BytesIO(tresc))
    if ftp.size(ETAP_SESJI) != len(tresc):
        ftp.quit()
        print("FTP: rozmiar po wgraniu sie nie zgadza")
        return 3
    ftp.quit()
    print(f"FTP: wgrano {ETAP_SESJI} ({len(tresc)} B)")

    # 2. shell: przeniesienie + 0600 + restart
    r = shell(cfg, "python3.11 " + f"{KATALOG_APKI}/daemon.py stop")
    print("stop: " + ((r.get("output") or r.get("raw") or "").strip() or "brak odpowiedzi"))

    r = shell(cfg, "python3.11 -c \"import os,shutil;p='/home/srv120794/{e}';d='{c}';shutil.copy2(p,d);os.chmod(d,0o600);print('sesja B:',os.stat(d).st_size,'tryb:',oct(os.stat(d).st_mode & 0o777))\"".format(e=ETAP_SESJI, c=CEL_SESJI))
    print("podmiana: " + ((r.get("output") or r.get("raw") or "").strip() or "brak odpowiedzi"))
    if not r.get("ok"):
        # Bot zostal zatrzymany, wiec ZAWSZE go wstawiamy — nawet gdy podmiana
        # nie wyszla. Bot na starej, moze wygasla sesji jest lepszy niz zaden,
        # a pominiety restart zostawial go martwego na czas do kolejnego
        # przebiegu (zdarzylo sie 01.10 przy 429 na samym starcie).
        print("podmiana nieudana — mimo to wstawiam bota, bo stoi zatrzymany")
        r2 = shell(cfg, "python3.11 " + f"{KATALOG_APKI}/daemon.py start")
        print("start (awaryjny): " + ((r2.get("output") or r2.get("raw") or "").strip() or "brak odpowiedzi"))
        return 4

    r = shell(cfg, "python3.11 " + f"{KATALOG_APKI}/daemon.py start")
    print("start: " + ((r.get("output") or r.get("raw") or "").strip() or "brak odpowiedzi"))
    if r.get("ok"):
        zapisz_znacznik(lokalny, len(tresc), "wgrano i zrestartowano")
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())
