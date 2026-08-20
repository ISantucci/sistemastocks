/* ---------- Deduplicación de ítems en formularios multi-línea ----------
   Problema que resuelve: en las cargas multi-fila (Carga múltiple, Utilizados,
   Descartes, Ingresos/Egresos, Solicitud de repuestos) cada fila armaba su
   selector con el catálogo completo, sin mirar lo elegido en las otras. Se
   podía cargar el MISMO ítem en varias filas y "mover" más unidades de las que
   existen (o partir un mismo ítem en movimientos separados, ensuciando la
   trazabilidad).

   Criterio: un ítem = una sola fila. Una vez elegido, desaparece del listado de
   las demás filas; si se borra la fila o se cambia la elección, vuelve a estar
   disponible.

   OJO: esto es solo UX. La validación real vive en el backend (app.py), que
   rechaza el POST con item_id[] repetidos. No alcanza con ocultar la opción.

   Uso:
     var dd = initLineDedupe({
       itemSel: ".bulk-item",                  // selector de los <select> de ítem
       optionsFor: function (el) { ... },      // opciones permitidas SIN dedupe
       enabledFor: function (el) { ... }       // opcional: habilitar/deshabilitar
     });
     dd.refresh();   // recalcular todas las filas

   Requiere TomSelect ya inicializado en cada select (2.x: clearOptions()
   conserva la opción actualmente seleccionada, por eso no se pierde el valor
   propio de la fila al rearmar). */
(function (w, d) {
  "use strict";

  function tsValue(el) {
    if (!el) return "";
    if (el.tomselect) {
      var v = el.tomselect.getValue();
      return v == null ? "" : String(v);
    }
    return String(el.value || "");
  }

  function initLineDedupe(cfg) {
    cfg = cfg || {};
    var scope = cfg.scope || d;
    var itemSel = cfg.itemSel;
    var busy = false;   // evita reentrada: rearmar opciones puede disparar 'change'

    // OJO: TomSelect copia las clases del <select> original al .ts-wrapper que
    // genera, así que un selector por clase (ej. ".bulk-item") matchea DOS
    // elementos por fila. Nos quedamos solo con el control real.
    function selects() {
      return Array.prototype.filter.call(
        scope.querySelectorAll(itemSel),
        function (el) { return el.tagName === "SELECT" || el.tagName === "INPUT"; }
      );
    }

    // Ítems ya tomados por las OTRAS filas.
    function takenExcept(el) {
      var taken = {};
      selects().forEach(function (other) {
        if (other === el) return;
        var v = tsValue(other);
        if (v) taken[v] = true;
      });
      return taken;
    }

    function rebuild(el) {
      var ts = el.tomselect;
      if (!ts) return;
      var mine = tsValue(el);
      var taken = takenExcept(el);
      var base = cfg.optionsFor ? (cfg.optionsFor(el) || []) : [];

      ts.clearOptions();   // conserva la opción seleccionada de esta fila
      base.forEach(function (o) {
        // La propia elección de la fila siempre se conserva; las de otras filas
        // se sacan del listado.
        if (o.value !== mine && taken[o.value]) return;
        ts.addOption(o);
      });
      ts.refreshOptions(false);

      if (cfg.enabledFor) {
        if (cfg.enabledFor(el)) { ts.enable(); } else { ts.disable(); }
      }
    }

    function refresh() {
      if (busy) return;
      busy = true;
      try { selects().forEach(rebuild); } finally { busy = false; }
    }

    // Devuelve los valores duplicados entre filas (red de seguridad para el
    // submit; en condiciones normales nunca debería haber ninguno).
    function duplicates() {
      var seen = {}, dups = [];
      selects().forEach(function (el) {
        var v = tsValue(el);
        if (!v) return;
        if (seen[v]) { if (dups.indexOf(v) === -1) dups.push(v); }
        seen[v] = true;
      });
      return dups;
    }

    return { refresh: refresh, duplicates: duplicates, value: tsValue };
  }

  w.initLineDedupe = initLineDedupe;
  w.lineDedupeValue = tsValue;

  /* ---------- "Desde" y "Hacia" no pueden ser la misma ubicación ----------
     El backend ya lo rechaza (ver movements() y movements_bulk() en app.py),
     pero el desplegable "Hacia" igual ofrecía la ubicación de origen: el
     usuario la elegía, mandaba el formulario y recién ahí le rebotaba. Acá se
     saca la opción del listado para que el caso no exista en pantalla.

     Funciona con <select> nativos (no TomSelect). Si "Desde" es un <input
     hidden> —el TECNICO con una sola ubicación asignada— igual filtra el
     destino; simplemente no hay nada que filtrar del lado del origen.

     El filtro es UNIDIRECCIONAL a propósito: "Desde" restringe "Hacia", nunca
     al revés. Filtrar en los dos sentidos deja el formulario trabado: con
     Jaula -> Berlingo cargado, invertirlo es imposible (Berlingo no se puede
     elegir como origen porque es el destino, y Jaula no se puede elegir como
     destino porque es el origen) y el placeholder está disabled, así que no
     hay forma de volver atrás sin recargar. Con el filtro en un solo sentido,
     cambiar "Desde" al destino actual simplemente limpia "Hacia" y se sigue.

     Uso:
       initFromToExclusion({
         from: document.getElementById("from_location_id"),
         to:   document.getElementById("to_location_id")
       }); */
  function initFromToExclusion(cfg) {
    cfg = cfg || {};
    var fromEl = cfg.from, toEl = cfg.to;
    var noop = { refresh: function () {} };
    if (!fromEl || !toEl) return noop;

    var busy = false;

    function hideMatching(sel, value) {
      // Un <input hidden> no tiene .options: no hay nada que ocultar.
      var opts = sel && sel.options;
      if (!opts) return;
      Array.prototype.forEach.call(opts, function (o) {
        if (!o.value) return;                 // el placeholder se deja siempre
        var hide = !!value && o.value === value;
        o.hidden = hide;
        o.disabled = hide;                    // refuerzo: Safari ignora [hidden]
      });
    }

    function sync() {
      if (busy) return;
      busy = true;
      var cleared = false;
      try {
        var fromVal = String(fromEl.value || "");
        // Si el destino quedó apuntando al origen, se limpia.
        if (fromVal && String(toEl.value || "") === fromVal) {
          toEl.value = "";
          cleared = true;
        }
        hideMatching(toEl, fromVal);
      } finally {
        busy = false;
      }
      // Avisar a los demás listeners (ej. el motivo de descarte, que depende
      // de "Hacia"). Reentra en sync(), pero ya sin nada que limpiar.
      if (cleared) { toEl.dispatchEvent(new Event("change", { bubbles: true })); }
    }

    fromEl.addEventListener("change", sync);
    toEl.addEventListener("change", sync);
    sync();
    return { refresh: sync };
  }

  w.initFromToExclusion = initFromToExclusion;
})(window, document);
