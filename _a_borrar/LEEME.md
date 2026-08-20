# `_a_borrar/` — se puede borrar entera

Desde acá **no puedo eliminar archivos** en tu máquina (el puente falla con
"Operation not permitted"), así que junté todo lo descartable en una sola
carpeta. **Borrá `_a_borrar/` entera desde el Explorador** y listo.

**580 KB**, nada de esto hace falta para que el sistema funcione.

| Subcarpeta | Qué es | Por qué se puede borrar |
|---|---|---|
| `caches/` | `__pycache__`, `tests/__pycache__`, `.pytest_cache` | Los regenera Python solo al correr la app o los tests |
| `fuse_hidden/` | 3 restos de `data/` y `data_local/` | Basura del montaje de red: archivos borrados que quedaron abiertos. No son código ni datos |
| `mio/` | `_prueba_borrado.txt`, `index.lock.borrar` | Los dejé yo (uno probando si podía borrar, el otro del `git` que se me colgó) |

---

## Falta un paso que sí o sí tenés que hacer vos

Hay **8 archivos `.fuse_hidden` commiteados dentro de `templates/`** (19 KB).
Esos no los toqué a propósito: están *trackeados en git*, y si los movía te
dejaba el árbol sucio y `git am` te iba a fallar al aplicar los parches.

**Orden correcto:**

```bat
:: 1) primero aplicá los 10 parches (ver _parches_refactor\LEEME.md)

:: 2) recién después, la limpieza del repo:
git rm templates/.fuse_hidden*
git commit -m "Quitar restos .fuse_hidden del repo"
```

`git rm` los saca del índice **y** del disco de una, y queda revertible con
`git revert`. El `.gitignore` del parche 0008 evita que vuelvan a entrar, pero
no saca los que ya estaban commiteados: por eso hace falta este comando.

---

## Lo que NO borré, y por qué

Esto es lo importante. Nada de lo de abajo se toca sin que lo decidas vos.

| No lo toqué | Motivo |
|---|---|
| **`_parches_refactor/`** (400 KB) | **Todavía no aplicaste los parches.** Borrarla ahora sería tirar la entrega. Borrala después del paso 2 |
| **`.venv/`** (60 MB) | Es tu entorno de trabajo. Sin él no podés correr ni la app ni los tests hasta reinstalar todo |
| **`backups_local/`** (3,5 MB) | Tiene `stocks_PROD_20260730_112608.db`, un **backup de producción**. Ni en broma |
| **`data/`, `data_local/`** (936 KB) | Son bases de datos |
| **`templates/_deprecated/README.md`** | Lo abrí antes de decidir: **no es basura**. Es una convención tuya documentada — "carpeta reservada para apartar (NO borrar) templates que dejen de usarse", con el procedimiento escrito. Si la borraba, perdías el criterio |
| **`logs/`, `logs_local/`** | Están vacías (0 bytes). No molestan |

### Una cosa que sí quiero que decidas: `validate_scrap.py`

14 KB en la raíz. Busqué en todo el repo y **nadie lo referencia**: ni `app.py`,
ni los tests, ni el `Dockerfile`, ni el `docker-compose`. La auditoría del vault
ya lo había marcado como "no sé si sigue en uso".

No lo borré porque puede ser una herramienta que corrés a mano cada tanto, y eso
no se ve desde el código. **Si no lo usás, decime y lo saco en el mismo commit
de limpieza.**

---

## Sobre "borrar todo lo que no sea necesario"

Lo revisé en serio y la respuesta corta es: **casi no hay nada para borrar en el
sistema**. Corrí un inventario cruzando `render_template()`, `{% extends %}`,
`{% include %}` e `{% import %}` contra los archivos en disco:

- **47 templates, 46 en uso.** El único huérfano es `_metricas_macros.html`
  (1,5 KB) — y como es un archivo de macros de Jinja, prefiero que lo confirmes
  antes de sacarlo.
- **15 archivos JS y 3 CSS: todos referenciados.** Cero muertos.
- `static/vendor/assets.json` parecía sin uso desde los templates, pero **lo lee
  `app.py`** (`_load_vendor_assets`, línea 141). Falso positivo mío.

O sea: el código está limpio. Lo que sobraba era basura *alrededor* del código
—caches, restos de FUSE, cosas mías— y eso es lo que está en `_a_borrar/`.
