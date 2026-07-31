# templates/_deprecated

Carpeta reservada para apartar (NO borrar) templates que dejen de usarse.

## Estado a la fecha de consolidación: 2026-07-29

Se corrió un inventario automático de templates realmente usados, cruzando:

- todas las llamadas `render_template("...")` de `app.py` (parseo con AST, incluye llamadas multilínea);
- todas las referencias `{% extends %}`, `{% include %}`, `{% import %}`, `{% from %}` dentro de los templates.

**Resultado: los 27 templates en disco están todos en uso. No hay templates muertos.**

Los candidatos que figuraban en auditorías previas (`admin_backup.html`,
`category_edit.html`, `ingresos.html`, `item_edit.html`, `location_edit.html`,
`new_item.html`, `new_movement.html`, `new_user.html`, `stock_adjust.html`,
`user_edit.html`) **ya no existen** en esta versión del proyecto: fueron
removidos o renombrados en iteraciones anteriores. No hay nada que apartar.

Por lo tanto esta carpeta queda vacía (solo este README) a propósito.

## Cómo apartar un template en el futuro

1. Confirmar con el inventario que no está referenciado.
2. Mover el archivo aquí (no borrar).
3. Registrar en una tabla: nombre original | motivo | reemplazo activo | fecha | evidencia de desuso.
