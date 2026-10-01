"""Test negatywny dla trzech poprawek z 01.10.

Zielona bramka, ktorej sie nie zlamalo, nie jest bramka. Cofamy kazda
poprawke, uruchamiamy DOKLADNIE wskazany test i przywracamy plik.
Wyjscie 0 = kazda zlamana wersja padla (czyli bramka ma zadanie).
"""
import subprocess, sys, pathlib, re

BOT = pathlib.Path('bot.py')
TESTY = pathlib.Path('tests/test_bot.py')
oraz = BOT.read_bytes()
testy_oraz = TESTY.read_bytes()


def uruchom(nazwa):
    wynik = subprocess.run([sys.executable, '-m', 'unittest', f'tests.test_bot.{nazwa}'],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
    return 'FAILED' in wynik.stderr or wynik.returncode != 0


def podmien(stary, nowy):
    tekst = BOT.read_text(encoding='utf-8')
    assert stary in tekst, f'NIE ZNALEZIONO w bot.py: {stary[:60]}'
    BOT.write_text(tekst.replace(stary, nowy, 1), encoding='utf-8')


przypadki = [
    ('brak importu artifacts',
     'test_artifacts_zaimportowane_na_poziomie_modulu',
     'import artifacts  # wywolywany tez z workerow kolejki, nie tylko z komend\n',
     ''),
    ('ciche pominięcie licznika',
     'test_worker_nie_pomija_licznika_gdy_jest_none',
     '    try:\n        to_limit, opis = _pobierz_licznika().zapisz_wynik("audio", status)\n',
     '    to_limit, opis = False, ""\n    if _licznik is not None:\n        to_limit, opis = _licznik.zapisz_wynik("audio", status)\n'),
    ('gotowe przed wysłaniem',
     'test_gotowe_oznaczane_dopiero_po_wyslaniu',
     '    wysłane = await _wyślij_wynik_audio(dysk, kanal_id, zadanie, raport)\n    kolejka.oznacz(zadanie_id, "gotowe" if wysłane else "nieudane",\n                   {"task": str(getattr(status, "task_id", ""))})',
     '    kolejka.oznacz(zadanie_id, "gotowe", {})\n    await _wyślij_wynik_audio(dysk, kanal_id, zadanie, raport)'),
    ('temat gubiony w kolejce',
     'test_temat_jest_przekazywany_do_instructions',
     '        instructions=temat or None,\n',
     ''),
]

bledy = []
try:
    for opis, test, stary, nowy in przypadki:
        podmien(stary, nowy)
        padl = uruchom(test)
        print(f'{"OK  " if padl else "PRZEJSCIE BEZ FAIL"}  {opis:32s} -> {test}')
        if not padl:
            bledy.append(opis)
        BOT.write_bytes(oraz)  # przywroc po kazdym przypadku
finally:
    BOT.write_bytes(oraz)
    TESTY.write_bytes(testy_oraz)

print()
if bledy:
    print(f'Bramka NIE lapie {len(bledy)}/{len(przypadki)}:', ', '.join(bledy))
    sys.exit(1)
print(f'Wszystkie {len(przypadki)} regresje wykryte. Pliki przywrocone.')
