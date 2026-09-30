// Bramka negatywna dla check_encoding.mjs: psuje plik i wymaga, zeby
// bramka to zglosila. Bez tego "WSZYSTKIE PLIKI OK" mogloby oznaczac, ze
// skrypt nic nie sprawdza — a tak bylo przy pierwszym podejsciu, gdy test
// wstrzykiwal uszkodzenie do niewlasciwego pliku.
//
// Uruchomienie: node tests/negative_encoding.mjs
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = path.resolve(import.meta.dirname, "..");
const CEL = path.join(ROOT, "README.md");
const SKRYPT = path.join(ROOT, "check_encoding.mjs");

// "ó" zapisane dwukrotnie: C3 B3 -> C3 83 C2 B3
const DWOJNE = Buffer.from([0xc3, 0x83, 0xc2, 0xb3]);

function uruchom() {
  const r = spawnSync(process.execPath, [SKRYPT], { cwd: ROOT, encoding: "utf-8" });
  return { kod: r.status, wyjscie: r.stdout + r.stderr };
}

function sha(b) {
  return b.toString("hex").slice(0, 16);
}

if (!fs.existsSync(CEL)) {
  console.log(`BLOKADA: brak pliku testowego ${CEL}`);
  process.exit(1);
}

const oryginal = fs.readFileSync(CEL);
console.log(`plik: ${path.basename(CEL)} ${oryginal.length} B sha=${sha(oryginal)}`);

// 1. stan wyjsciowy — musi byc zielony
const przed = uruchom();
console.log(`stan wyjsciowy: kod=${przed.kod} (oczekiwane 0)`);
if (przed.kod !== 0) {
  console.log("BLOKADA: bramka nie jest zielona na czystym repo — test negatywny nie ma sensu");
  console.log(przed.wyjscie.slice(0, 400));
  process.exit(1);
}

let wynik = 1;
try {
  // 2. wstrzykniecie uszkodzenia (dopis, bez kotwicy tekstowej)
  fs.writeFileSync(CEL, Buffer.concat([oryginal, DWOJNE]));
  const zepsute = uruchom();
  const wykrylo = zepsute.kod !== 0 && /dbl:\s*[1-9]/.test(zepsute.wyjscie);
  console.log(`po uszkodzeniu: kod=${zepsute.kod} wykryto=${wykrylo} (oczekiwane 1/true)`);
  const linia = zepsute.wyjscie.split("\n").find((l) => l.includes("README.md"));
  if (linia) console.log(`  ${linia.trim()}`);
  if (!wykrylo) {
    console.log("BRAK: bramka przeszla na uszkodzonym pliku — albo nie widzi uszkodzenia, albo go nie raportuje");
    wynik = 1;
  } else {
    wynik = 0;
  }
} finally {
  // 3. przywrocenie — bajt w bajt
  fs.writeFileSync(CEL, oryginal);
  const po = fs.readFileSync(CEL);
  const identyczny = po.equals(oryginal);
  console.log(`po przywroceniu: identyczny=${identyczny} sha=${sha(po)}`);
  if (!identyczny) {
    console.log("BRAK: plik nie wrócil do stanu wyjsciowego");
    wynik = 1;
  }
  const znowu = uruchom();
  console.log(`stan koncowy: kod=${znowu.kod} (oczekiwane 0)`);
  if (znowu.kod !== 0) wynik = 1;
}

console.log(
  wynik === 0
    ? "\nWNIOSEK: bramka ma zęby — łapie wstrzyknięte podwójne kodowanie i nie zostawia pliku"
    : "\nWNIOSEK: BRAKI — bramka jest pozorna"
);
process.exit(wynik);