# Cómo aplicar los parches

Siete parches, uno por fase. Están en `_parches_refactor/` dentro del repo.
**Reemplazan a los seis que te pasé antes** (los regeneré: ver "Qué cambió" abajo).

**No toqué tu git.** Seguís en la rama `Changes&Updates`, con el árbol limpio.

---

## ⚠️ Primero: borrá dos archivos que dejé

```
SistemaStocksTNG\.git\index.lock
SistemaStocksTNG\_parches_refactor\index.lock.borrar
```

Intenté crear la rama desde acá y el puente con tu máquina no puede borrar
archivos; git necesita borrar su `index.lock` al terminar cada comando, así que
quedó huérfano y hace fallar el siguiente comando con *"Another git process
seems to be running"*. No hay ningún proceso colgado: es un archivo vacío.

---

## Aplicar

**Importante: usá `--keep-cr`.** Tus archivos tienen finales de línea CRLF
(Windows). Sin ese flag, `git am` los interpreta como si fueran de un mail, se
come los `\r` y los parches no aplican. Con el flag aplican los siete limpios.

```bat
cd C:\Users\ControlEquipos\Documents\GitHub\SistemaStocksTNG

git status                        :: tiene que estar limpio
git checkout -b refactor/fases-1-6

git am --keep-cr _parches_refactor\0001-Fase-1.patch
git am --keep-cr _parches_refactor\0002-Fase-2.patch
git am --keep-cr _parches_refactor\0003-Fase-3.patch
git am --keep-cr _parches_refactor\0004-Fase-4.patch
git am --keep-cr _parches_refactor\0005-Fase-5.patch
git am --keep-cr _parches_refactor\0006-Verificacion.patch
git am --keep-cr _parches_refactor\0007-Fase-6.patch
```

De a uno, así si algo falla sabés en cuál. Si uno falla: `git am --abort` y
quedás como estabas.

Cuando termines, sacá `_parches_refactor\` del repo (no la commitees).

## Probar

```bat
python -m pytest -q
```

Tiene que dar **378 passed, 0 failed**. El punto de partida eran 213.

## Cómo verifiqué que esto funciona

No te lo digo de palabra: copié tu código tal cual está hoy en un árbol limpio,
apliqué los siete parches en orden y corrí la suite ahí. Resultado: 378 passed,
y el árbol resultante quedó **byte por byte idéntico** al mío.

---

## Qué cambió respecto de los seis parches anteriores

1. **`--keep-cr`.** La primera tanda no aplicaba por el tema de CRLF. Lo detecté
   probando la aplicación desde cero, no en tu máquina.

2. **Parches más chicos.** Mis ediciones habían convertido CRLF → LF en 12
   archivos, así que el diff mostraba archivos enteros reescritos aunque el
   cambio real fueran 3 líneas. Rehice el historial preservando los finales de
   línea originales: el parche de la Fase 3 pasó de 46 KB a 9 KB. Ahora el diff
   es solo lo funcional, que es lo que tenés que poder revisar.

   Ojo: eso significa que **estos parches no incluyen ninguna normalización de
   finales de línea**. El `git add --renormalize .` que figura pendiente en el
   vault sigue pendiente, y va en un commit aparte, como corresponde.

3. **Se coló basura mía y la saqué.** `medir_fase0.py`, `resultado_fase0.json` y
   `_fase5_extraer.py` eran scripts descartables míos que se habían commiteado
   sin querer. Ya no están en el historial.

4. **`static/vendor/assets.json` volvió a su estado original.** Lo había
   apuntado a los assets locales para poder correr las verificaciones en un
   navegador sin internet, y ese cambio se coló en un commit. Está revertido:
   los tres assets siguen con `"local": null`, igual que hoy.

---

## Volver atrás

Cada fase es un commit. Para deshacer una sola:

```bat
git revert <hash-de-esa-fase>
```

Para tirar todo abajo: `git checkout Changes&Updates` y borrá la rama. No hay
nada que deshacer fuera de git — **ninguna fase toca la base de datos, ni el
esquema, ni los permisos**.

---

## Antes de subirlo al servidor

1. Backup de la base.
2. `python scripts\vendor_assets.py` y commitear `static\vendor\`.
   Ahora importa más que antes: `script-src` quedó sin `'unsafe-inline'`, y si
   los assets siguen viniendo del CDN la política tiene que seguir permitiendo
   esos orígenes. Vendorizándolos se cierra sola.
3. Probar a mano: login, un movimiento simple, un movimiento de ítem
   serializado, un remito, y una carga en Ingresos/Egresos.
4. `CSP_ENFORCE=true` recién después de validar en staging que Report-Only no
   reporta violaciones.

### Una variable nueva, opcional

`STOCK_MAP_MAX_INLINE` (default 5000). Junto con `ITEM_PICKER_MAX_INLINE`
(default 800, ya existía) controlan a partir de qué volumen las pantallas de
carga dejan de embeber datos y pasan a pedirlos por API.

**Con tus 299 ítems y ~400 filas de stock no hacen falta tocar**: los defaults
están calculados para que hoy no cambie absolutamente nada. Se activan solas
cuando el sistema crezca.
