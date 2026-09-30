// Kontrola integralnosci kodowania plikow repo ai-dan-online.
// Sprawdza: BOM, U+FFFD, sekwencje podwojnego kodowania, CRLF vs LF.
import fs from "node:fs";
import path from "node:path";

const PLIKI = [
  "bot.py", "run.py", ".env.example", ".gitignore", "requirements.txt", "README.md",
  "tests/test_bot.py", "tests/test_negatywny.py",
  "docs/ARCHITEKTURA.md", "docs/OPERACJE.md", "docs/NOTEBOOKLM.md",
  "docs/ZNANE-PROBLEMY.md", "docs/HISTORIA.md",
];

const PL = /[\u0105\u0107\u0119\u0141\u0142\u0143\u0144\u00F3\u015A\u0179\u017C\u017A\u017B]/g;
// sekwencja podwojnego kodowania: U+00C3 (A z ogonkiem) + drugi bajt 0x80-0xBF
const DUBLE = /\u00C3[\u0080-\u00BF]/g;

let zle = 0;
const brak = [];

for (const f of PLIKI) {
  if (!fs.existsSync(f)) {
    console.log(`  BRAK  ${f}`);
    brak.push(f);
    zle++;
    continue;
  }
  const b = fs.readFileSync(f);
  const s = b.toString("utf8");
  const fffd = s.includes("\uFFFD");
  const bom = b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf;
  const crlf = (b.toString("binary").match(/\r\n/g) || []).length;
  const pl = (s.match(PL) || []).length;
  const duble = (s.match(DUBLE) || []).length;
  const ok = !fffd && !bom && duble === 0;
  if (!ok) zle++;
  console.log(
    `${ok ? "  ok  " : "  ZLE "} ${f.padEnd(24)} CRLF:${String(crlf).padStart(3)}` +
    ` PL:${String(pl).padStart(4)} U+FFFD:${fffd ? "TAK" : "nie"}` +
    ` BOM:${bom ? "TAK" : "nie"} dbl:${duble}`
  );
}

console.log(zle === 0 ? "\nWSZYSTKIE PLIKI OK" : `\nPLIKI Z PROBLEMEM: ${zle}`);
process.exit(zle === 0 ? 0 : 1);