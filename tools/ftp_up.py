import ftplib
import os
import sys
from pathlib import Path

REPO = Path(r"C:/Users/admin/Desktop/ai-dan-online")
PLIKI = [
    ("install_files.py", "install_files.py"),
    ("bot.py", "bot.py"),
    ("daemon.py", "daemon.py"),
    ("run.py", "run.py"),
    ("artifacts.py", "artifacts.py"),
    ("kolejka.py", "kolejka.py"),
    ("refresh.py", "refresh.py"),
    ("generowanie.py", "generowanie.py"),
    ("limits.py", "limits.py"),
    ("tools/limits_probe.py", "limits_probe.py"),
    ("tools/uruchom_testy.py", "uruchom_testy.py"),
    ("tools/test_uprawnienia.py", "test_uprawnienia.py"),
    ("tests/test_bot.py", "test_bot.py"),
    ("tests/test_artifacts.py", "test_artifacts.py"),
    ("tests/test_kolejka.py", "test_kolejka.py"),
    ("tests/test_reauth.py", "test_reauth.py"),
    ("tests/test_generowanie.py", "test_generowanie.py"),
    ("tests/test_limits.py", "test_limits.py"),
    ("tests/test_negatywny.py", "test_negatywny.py"),
]
# FTP login jest JUZ w /home/srv120794. `STOR` z bezwzgledna sciezka
# "/home/srv120794/x" ladowal plik do /home/srv120794/home/srv120794/x —
# ten sam podwojny prefiks co w `site_deploy`. Dlatego sciezki wzgledne.
SESJA = os.environ["SESJA_ZR_ZDANIEM"]
# Sesja laduje do katalogu DOMOWEGO przez FTP, a do nlm-home/ przenosi shell —
# FTP nie ma tam dostepu ("550 No such file or directory").
CEL_SESJA_ETAP = "sesja_nowa.json"

DOMY = ""


HOST = os.environ["FTP_HOST"]
USER = os.environ["FTP_USER"]
HASLO = os.environ["FTP_PASS"]

ftp = ftplib.FTP()
ftp.connect(HOST, timeout=60)
ftp.login(USER, HASLO)
ftp.set_pasv(True)
ftp.voidcmd("TYPE I")

zle, ok = [], 0
for zrodlo, cel in PLIKI:
    lokalny = REPO / zrodlo
    if not lokalny.exists():
        zle.append(f"brak pliku: {zrodlo}")
        continue
    with open(lokalny, "rb") as f:
        ftp.storbinary("STOR " + DOMY + cel, f)
    zdalny = ftp.size(DOMY + cel)
    if zdalny != lokalny.stat().st_size:
        zle.append(f"{cel}: serwer {zdalny} != lokalnie {lokalny.stat().st_size}")
    else:
        ok += 1

if Path(SESJA).exists():
    with open(SESJA, "rb") as f:
        ftp.storbinary("STOR " + CEL_SESJA_ETAP, f)
    if ftp.size(CEL_SESJA_ETAP) == Path(SESJA).stat().st_size:
        ok += 1
    else:
        zle.append("sesja: rozmiar sie nie zgadza")
else:
    zle.append(f"brak pliku sesji: {SESJA}")

ftp.quit()
print(f"wgrano poprawnie: {ok} / {len(PLIKI) + 1}")
for z in zle:
    print("  BLED: " + z)
sys.exit(1 if zle else 0)
