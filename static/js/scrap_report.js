/* scrap_report.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.SCRAP_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.SCRAP_DATA || {};

document.addEventListener("DOMContentLoaded", function () {
  // Filtros: en desktop siempre visibles; en mobile colapsados por defecto.
  var scrapFilters = document.getElementById("scrapFilters");
  if (scrapFilters) {
    var syncScrapFilters = function () {
      if (window.matchMedia("(min-width:768px)").matches) { scrapFilters.open = true; }
    };
    syncScrapFilters();
    window.addEventListener("resize", syncScrapFilters);
  }
  var stockMap = D.stockMap;
  var stockQtyMap = D.stockQtyMap;
  var fromSel = document.getElementById("from_location_id");

  // Lista de ítems seleccionables. Los serializados TAMBIÉN entran: cada fila
  // resuelve sus seriales con el mismo selector auto/elegir de Movimientos.
  var allScrapItems = D.allItems;
  var unitsMap = D.unitsMap;
  var serializedItemIds = D.serializedItems;

  function allowedForCurrentFrom() {
    var locId = fromSel ? String(fromSel.value || "") : "";
    var allowed = stockMap[locId] || [];
    var set = {};
    allowed.forEach(function (id) { set[String(id)] = true; });
    return { locId: locId, set: set };
  }

  function clampScrapQty(line) {
    if (!line) return;
    var loc = fromSel ? String(fromSel.value || "") : "";
    var itemSel = line.querySelector(".scrap-item");
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
  function scrapStockOptions() {
    var info = allowedForCurrentFrom();
    if (!info.locId) return [];
    return allScrapItems.filter(function (o) { return !!info.set[o.value]; });
  }

  // Un item = una sola fila: el dedupe saca de cada listado lo ya elegido en
  // las otras filas. El backend igual rechaza item_id[] repetidos.
  // OJO: por eso un mismo item no se puede descartar con dos motivos distintos
  // en la misma carga; hay que hacer dos cargas.
  var scrapDedupe = initLineDedupe({
    itemSel: ".scrap-item",
    optionsFor: scrapStockOptions,
    enabledFor: function () { return !!allowedForCurrentFrom().locId; }
  });

  function applyStockFilterAllLines() { scrapDedupe.refresh(); }

  // Cambio de "Desde": cambia el universo de stock, se limpia lo ya elegido.
  function resetStockFilterAllLines() {
    document.querySelectorAll(".scrap-item").forEach(function (el) {
      if (el.tomselect) el.tomselect.clear(true);
    });
    scrapDedupe.refresh();
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
        clampScrapQty(el.closest(".scrap-line"));
        // Liberar/tomar el item en las demas filas.
        applyStockFilterAllLines();
        var linea = el.closest(".scrap-line");
        if (linea && linea._syncSerials) linea._syncSerials();
      }
    });
    applyStockFilterAllLines();
  }

  function wireSerials(line) {
    var box = line.querySelector(".serial-pick-box");
    var resumen = line.querySelector(".serial-summary-box");
    var summaryInput = line.querySelector(".serial-summary");
    var refresh = initSerialPicker({
      unitsMap: unitsMap,
      serializedItems: serializedItemIds,
      itemEl: line.querySelector(".scrap-item"),
      fromSel: fromSel,
      qtyInput: line.querySelector('input[name="qty[]"]'),
      pickBox: box,
      pickList: line.querySelector(".serial-pick-list"),
      pickStatus: line.querySelector(".serial-pick-status"),
      autoHint: line.querySelector(".serial-auto-box"),
      autoText: line.querySelector(".serial-auto-text"),
      idsInput: line.querySelector(".serial-ids"),
      summaryInput: summaryInput
    });
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
    document.querySelectorAll(".scrap-line").forEach(function (l) {
      if (l._syncSerials) l._syncSerials();
    });
  }

  function wireScrapLine(line) {
    var itemSelect = line.querySelector(".scrap-item");
    var removeBtn = line.querySelector(".remove-line-btn");
    initTomSelectFor(itemSelect);
    var qtyEl = line.querySelector('input[name="qty[]"]');
    if (qtyEl) { qtyEl.addEventListener("input", function () { clampScrapQty(line); }); }
    wireSerials(line);
    // Al borrar la fila, su item vuelve a estar disponible en las demas.
    if (removeBtn) {
      removeBtn.addEventListener("click", function () {
        line.remove();
        applyStockFilterAllLines();
      });
    }
  }

  function addScrapLine() {
    var tpl = document.getElementById("scrap-line-template");
    var container = document.getElementById("scrap-lines");
    container.appendChild(tpl.content.cloneNode(true));
    wireScrapLine(container.lastElementChild);
  }

  document.getElementById("add-line-btn").addEventListener("click", addScrapLine);
  addScrapLine();

  if (fromSel && fromSel.tagName === "SELECT") {
    fromSel.addEventListener("change", function () { resetStockFilterAllLines(); });
  }
});
})();
