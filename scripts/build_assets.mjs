/* Minifica los assets propios del front hacia static/dist/.
 *
 * Por que existe
 * --------------
 * El sistema no tiene bundler ni nunca lo necesito: Flask sirve static/ tal
 * cual. El problema es que asi el archivo que esta en git es exactamente el que
 * le llega al navegador, comentarios incluidos. Este script separa las dos
 * cosas: el FUENTE sigue en static/js y static/css, comentado y legible, y lo
 * que se sirve sale de static/dist, minificado.
 *
 * Que NO hace, a proposito
 * ------------------------
 * No bundlea, no resuelve imports, no transpila y no toca static/vendor (que ya
 * viene minificado de origen). Cada archivo entra y sale de a uno, con el mismo
 * nombre. Asi el mapeo es obvio y un problema se rastrea leyendo un solo
 * archivo, no un bundle.
 *
 * Uso
 * ---
 *   npm run build     genera static/dist/
 *   npm run check     NO escribe: dice si dist quedo desactualizado (lo usa el test)
 *
 * Si static/dist/ no existe, la app sirve los fuentes y funciona igual: ver
 * _asset_servido() en app.py. Es degradacion segura, no una dependencia dura.
 */
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = join(dirname(fileURLToPath(import.meta.url)), "..");
const STATIC = join(RAIZ, "static");
const DIST = join(STATIC, "dist");
const MANIFEST = join(DIST, "manifest.json");
const SOLO_CHEQUEAR = process.argv.includes("--check");

const hash = (buf) => createHash("sha1").update(buf).digest("hex").slice(0, 12);

/* Los fuentes: todo lo de static/js y static/css. Se saltean los .bak_* que a
   veces quedan al costado y no son codigo vivo. */
function fuentes() {
  const out = [];
  for (const carpeta of ["js", "css"]) {
    const dir = join(STATIC, carpeta);
    if (!existsSync(dir)) continue;
    for (const nombre of readdirSync(dir)) {
      if (!/\.(js|css)$/.test(nombre)) continue;
      if (nombre.includes(".bak")) continue;
      out.push(`${carpeta}/${nombre}`);
    }
  }
  return out.sort();
}

const esperado = {};
for (const rel of fuentes()) {
  esperado[rel] = hash(readFileSync(join(STATIC, rel)));
}

if (SOLO_CHEQUEAR) {
  if (!existsSync(MANIFEST)) {
    console.log("[!!] static/dist/ no existe. Corre: npm run build");
    process.exit(1);
  }
  const actual = JSON.parse(readFileSync(MANIFEST, "utf8")).fuentes || {};
  const desactualizados = [];
  for (const [rel, h] of Object.entries(esperado)) {
    if (actual[rel] !== h) desactualizados.push(rel);
  }
  for (const rel of Object.keys(actual)) {
    if (!(rel in esperado)) desactualizados.push(`${rel} (ya no existe)`);
  }
  if (desactualizados.length) {
    console.log("[!!] static/dist/ quedo desactualizado. Corre: npm run build");
    desactualizados.forEach((r) => console.log("     - " + r));
    process.exit(1);
  }
  console.log(`[OK] static/dist/ al dia (${Object.keys(esperado).length} archivos)`);
  process.exit(0);
}

/* Se sobrescribe archivo por archivo en vez de borrar static/dist/ entero: en
   algunos entornos (carpetas montadas, permisos) el borrado recursivo falla y
   cortaba el build por algo que no hacía falta. Los sobrantes se limpian abajo,
   uno por uno y tolerando el error. */
mkdirSync(join(DIST, "js"), { recursive: true });
mkdirSync(join(DIST, "css"), { recursive: true });

let totalOrigen = 0;
let totalMin = 0;

for (const rel of fuentes()) {
  const entrada = join(STATIC, rel);
  const salida = join(DIST, rel);
  await build({
    entryPoints: [entrada],
    outfile: salida,
    minify: true,
    // Sin bundle a proposito: cada archivo se minifica solo, sin resolver
    // imports ni juntar nada. Los archivos ya son independientes y se cargan
    // con <script src> en el orden que define base.html.
    bundle: false,
    target: ["es2017"],
    legalComments: "none",
    logLevel: "warning",
  });
  const o = readFileSync(entrada).length;
  const m = readFileSync(salida).length;
  totalOrigen += o;
  totalMin += m;
  console.log(`  ${rel.padEnd(34)} ${String(o).padStart(7)} -> ${String(m).padStart(7)} bytes`);
}

/* Minificados que ya no tienen fuente: si el borrado no se puede, se avisa y
   listo. Un archivo de más en dist no rompe nada -- nadie lo referencia -- pero
   conviene que no quede dando vueltas. */
const vivos = new Set(fuentes());
const sobrantes = [];
for (const carpeta of ["js", "css"]) {
  const dir = join(DIST, carpeta);
  if (!existsSync(dir)) continue;
  for (const nombre of readdirSync(dir)) {
    const rel = `${carpeta}/${nombre}`;
    if (vivos.has(rel)) continue;
    try {
      rmSync(join(DIST, rel), { force: true });
    } catch {
      sobrantes.push(rel);
    }
  }
}
if (sobrantes.length) {
  console.log(`\n  [!] no se pudieron borrar de static/dist/: ${sobrantes.join(", ")}`);
  console.log("      Borralos a mano: ya no tienen fuente.");
}

writeFileSync(
  MANIFEST,
  JSON.stringify(
    {
      _readme: [
        "Generado por scripts/build_assets.mjs. No editar a mano.",
        "'fuentes' guarda el hash de cada archivo de static/js y static/css al",
        "momento del build. tests/test_build_assets.py lo compara contra los",
        "fuentes actuales: si alguien edita el fuente y no corre 'npm run build',",
        "el test falla. Sin eso, produccion serviria la version vieja en silencio.",
      ],
      generado: new Date().toISOString(),
      fuentes: esperado,
    },
    null,
    2
  ) + "\n",
  "utf8"
);

const pct = Math.round(100 - (totalMin * 100) / totalOrigen);
console.log(`\n  ${Object.keys(esperado).length} archivos: ${totalOrigen} -> ${totalMin} bytes (${pct}% menos)`);
console.log("  Recorda commitear static/dist/: el deploy no corre el build.");
