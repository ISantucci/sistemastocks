/* movements.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.MOV_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.MOV_DATA || {};

document.addEventListener("DOMContentLoaded", function () {
  // Filtros: en desktop siempre visibles; en mobile colapsados por defecto.
  var movFilters = document.getElementById("movFilters");
  if (movFilters) {
    var syncFilters = function () {
      if (window.matchMedia("(min-width:768px)").matches) { movFilters.open = true; }
    };
    syncFilters();
    window.addEventListener("resize", syncFilters);
  }
  // Item dinamico segun "Desde": solo se ofrecen items con stock (>0) en esa ubicacion.
  var stockMap = D.stockMap;
  var stockQtyMap = D.stockQtyMap;
  var externalLocIds = (D.externalLocIds || []).map(String);
  var fromSel = document.getElementById("from_location_id");
  var itemEl = document.getElementById("item_id");
  var qtyInputEl = document.getElementById("qty_input");

  // Tope de cantidad: no se puede mover más de lo que hay en el origen.
  // Origen externo (Proveedor/Baja) = sin tope. Si tipea de más, al máximo.
  function clampMovQty() {
    if (!qtyInputEl) return;
    var loc = fromSel ? String(fromSel.value || "") : "";
    var item = itemEl ? String(itemEl.value || "") : "";
    var isExternal = externalLocIds.indexOf(loc) !== -1;
    if (isExternal || !loc || !item) { qtyInputEl.removeAttribute("max"); return; }
    var mx = (stockQtyMap[loc] || {})[item];
    if (mx == null) { qtyInputEl.removeAttribute("max"); return; }
    qtyInputEl.setAttribute("max", mx);
    var v = parseInt(qtyInputEl.value, 10);
    if (!isNaN(v) && v > mx) { qtyInputEl.value = mx; }
  }
  if (qtyInputEl) { qtyInputEl.addEventListener("input", clampMovQty); }

  // Selector de seriales (auto/elegir) compartido.
  var refreshSerialUI = initSerialPicker({
    unitsMap: D.unitsMap,
    serializedItems: D.serializedItems,
    itemEl: itemEl,
    fromSel: fromSel,
    qtyInput: document.getElementById("qty_input"),
    pickBox: document.getElementById("serial_pick_box"),
    pickList: document.getElementById("serial_pick_list"),
    pickStatus: document.getElementById("serial_pick_status"),
    autoHint: document.getElementById("serial_auto_hint"),
    autoText: document.getElementById("serial_auto_text"),
    summaryInput: document.getElementById("serial_summary")
  });

  // El resumen sólo se muestra cuando hay seriales que resumir.
  (function () {
    var caja = document.getElementById("serial_summary_box");
    var campo = document.getElementById("serial_summary");
    var form = campo ? campo.form : null;
    if (!caja || !campo) return;
    function sync() { caja.style.display = campo.value ? "" : "none"; }
    var base = refreshSerialUI;
    refreshSerialUI = function () { base(); sync(); };
    if (form) form.addEventListener("change", sync);
    refreshSerialUI();
  })();

  if (itemEl && !itemEl.tomselect) {
    var allItems = [];
    Array.from(itemEl.options).forEach(function (o) {
      if (o.value) allItems.push({ value: o.value, text: o.text });
    });

    var tsItem = new TomSelect(itemEl, {
      create: false,
      sortField: { field: "text", direction: "asc" },
      placeholder: "Elegí la ubicación de origen primero...",
      closeAfterSelect: true,
      onItemAdd: function () { this.setTextboxValue(''); this.blur(); },
      onChange: function () {
        this.blur(); refreshSerialUI(); clampMovQty();
        // Tom Select ya dispara 'change' en el <select> original, pero aca el
        // select ademas se repuebla entero al cambiar la ubicacion de origen.
        // Llamarlo explicito evita depender de ese detalle interno.
        if (window.refrescarUnidades) window.refrescarUnidades();
      }
    });

    var refreshItems = function () {
      var locId = fromSel ? String(fromSel.value || "") : "";
      var isExternal = externalLocIds.indexOf(locId) !== -1;
      tsItem.clear(true);
      tsItem.clearOptions();
      if (isExternal) {
        // Ubicación externa (Proveedor/Baja): puede mover cualquier ítem.
        allItems.forEach(function (o) { tsItem.addOption(o); });
      } else {
        var allowed = stockMap[locId] || [];
        var allowedSet = {};
        allowed.forEach(function (id) { allowedSet[String(id)] = true; });
        allItems.forEach(function (o) { if (allowedSet[o.value]) tsItem.addOption(o); });
      }
      tsItem.refreshOptions(false);
      if (!locId) { tsItem.disable(); } else { tsItem.enable(); }
      refreshSerialUI();
      clampMovQty();
    };

    if (fromSel && fromSel.tagName === "SELECT") {
      fromSel.addEventListener("change", refreshItems);
    }
    refreshItems();
  }

  var toSel = document.getElementById("to_location_id");

  // "Hacia" no ofrece la ubicación elegida en "Desde" (y viceversa). El backend
  // ya lo rechaza; esto evita que el usuario llegue a mandarlo.
  initFromToExclusion({ from: fromSel, to: toSel });

  var scrapBox = document.getElementById("scrap_reason_box");
  var descartes_id = D.descartesId;
  function toggleScrap() {
    scrapBox.style.display = (toSel.value === descartes_id) ? "block" : "none";
  }
  if (toSel) { toSel.addEventListener("change", toggleScrap); toggleScrap(); }
});
})();
