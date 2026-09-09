/* item_usage.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.USAGE_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.USAGE_DATA || {};

document.addEventListener("DOMContentLoaded", function () {
  // Filtros: en desktop siempre visibles; en mobile colapsados por defecto.
  var usageFilters = document.getElementById("usageFilters");
  if (usageFilters) {
    var syncUsageFilters = function () {
      if (window.matchMedia("(min-width:768px)").matches) { usageFilters.open = true; }
    };
    syncUsageFilters();
    window.addEventListener("resize", syncUsageFilters);
  }
  var stockMap = D.stockMap;
  var stockQtyMap = D.stockQtyMap;
  var serializedItems = (D.serializedItems || []).map(String);
  var fromSel = document.getElementById("from_location_id");

  // Lista de ítems seleccionables. Los serializados TAMBIÉN entran: cada fila
  // resuelve sus seriales con el mismo selector auto/elegir de Movimientos.
  var allUsageItems = D.allItems;
  var unitsMap = D.unitsMap;
  var serializedItemIds = D.serializedItems;

  function allowedForCurrentFrom() {
    var locId = fromSel ? String(fromSel.value || "") : "";
    var allowed = stockMap[locId] || [];
    var set = {};
    allowed.forEach(function (id) { set[String(id)] = true; });
    return { locId: locId, set: set };
  }

  function clampUsageQty(line) {
    if (!line) return;
    var loc = fromSel ? String(fromSel.value || "") : "";
    var itemSel = line.querySelector(".usage-item");
    var item = itemSel ? String(itemSel.value || "") : "";
    var qty = line.querySelector('input[name="qty[]"]');
    if (!qty) return;
    if (!loc || !item) { qty.removeAttribute("max"); return; }
    var mx = (stockQtyMap[loc] || {})[item];
    if (mx == null) { qty.removeAttribute("max"); return; }
    qty.setAttribute("max", mx);
    var v = parseInt(qty.value, 10);
    if (!isNaN(v) && v > mx) { qty.value = mx; }
  }

  // Opciones permitidas por STOCK para las filas (sin considerar el dedupe).
  function usageStockOptions() {
    var info = allowedForCurrentFrom();
    if (!info.locId) return [];
    return allUsageItems.filter(function (o) { return !!info.set[o.value]; });
  }

  // Un item = una sola fila: el dedupe saca de cada listado lo ya elegido en
  // las otras filas. El backend igual rechaza item_id[] repetidos.
  var usageDedupe = initLineDedupe({
    itemSel: ".usage-item",
    optionsFor: usageStockOptions,
    enabledFor: function () { return !!allowedForCurrentFrom().locId; }
  });

  function applyStockFilterAllLines() { usageDedupe.refresh(); }

  // Cambio de "Desde": cambia el universo de stock, se limpia lo ya elegido.
  function resetStockFilterAllLines() {
    document.querySelectorAll(".usage-item").forEach(function (el) {
      if (el.tomselect) el.tomselect.clear(true);
    });
    usageDedupe.refresh();
    syncSerialsAllLines();
  }

  function initTomSelectFor(el) {
    if (!el || el.tomselect) return;
    new TomSelect(el, {
      create: false,
      sortField: { field: "text", direction: "asc" },
      placeholder: "Elegí la ubicación de origen primero...",
      closeAfterSelect: true,
      onItemAdd: function () { this.setTextboxValue(''); this.blur(); },
      onChange: function () {
        this.blur();
        clampUsageQty(el.closest(".usage-line"));
        // Liberar/tomar el item en las demas filas.
        applyStockFilterAllLines();
        var linea = el.closest(".usage-line");
        if (linea && linea._syncSerials) linea._syncSerials();
      }
    });
    applyStockFilterAllLines();
  }

  function wireSerials(line) {
    var box = line.querySelector(".serial-pick-box");
    var auto = line.querySelector(".serial-auto-box");
    var resumen = line.querySelector(".serial-summary-box");
    var summaryInput = line.querySelector(".serial-summary");
    var refresh = initSerialPicker({
      unitsMap: unitsMap,
      serializedItems: serializedItemIds,
      itemEl: line.querySelector(".usage-item"),
      fromSel: fromSel,
      qtyInput: line.querySelector('input[name="qty[]"]'),
      pickBox: box,
      pickList: line.querySelector(".serial-pick-list"),
      pickStatus: line.querySelector(".serial-pick-status"),
      autoHint: auto,
      autoText: line.querySelector(".serial-auto-text"),
      idsInput: line.querySelector(".serial-ids"),
      summaryInput: summaryInput
    });
    // El bloque del resumen solo se muestra si hay algo que resumir.
    function sync() {
      refresh();
      if (resumen) {
        resumen.style.display = (summaryInput && summaryInput.value) ? "" : "none";
      }
    }
    var qtyEl = line.querySelector('input[name="qty[]"]');
    if (qtyEl) qtyEl.addEventListener("input", sync);
    if (box) box.addEventListener("change", function () {
      if (resumen) resumen.style.display = (summaryInput && summaryInput.value) ? "" : "none";
    });
    line._syncSerials = sync;
    sync();
  }

  function syncSerialsAllLines() {
    document.querySelectorAll(".usage-line").forEach(function (l) {
      if (l._syncSerials) l._syncSerials();
    });
  }

  function wireUsageLine(line) {
    var itemSelect = line.querySelector(".usage-item");
    var removeBtn = line.querySelector(".remove-line-btn");
    initTomSelectFor(itemSelect);
    var qtyEl = line.querySelector('input[name="qty[]"]');
    if (qtyEl) { qtyEl.addEventListener("input", function () { clampUsageQty(line); }); }
    wireSerials(line);
    // Al borrar la fila, su item vuelve a estar disponible en las demas.
    if (removeBtn) {
      removeBtn.addEventListener("click", function () {
        line.remove();
        applyStockFilterAllLines();
      });
    }
  }

  function addUsageLine() {
    var tpl = document.getElementById("usage-line-template");
    var container = document.getElementById("usage-lines");
    container.appendChild(tpl.content.cloneNode(true));
    wireUsageLine(container.lastElementChild);
  }

  document.getElementById("add-line-btn").addEventListener("click", addUsageLine);
  addUsageLine();

  if (fromSel && fromSel.tagName === "SELECT") {
    fromSel.addEventListener("change", function () { resetStockFilterAllLines(); });
  }
});
})();
