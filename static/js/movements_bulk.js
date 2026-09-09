/* movements_bulk.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.BULK_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.BULK_DATA || {};

  var descartes_id = D.descartesId;
  var stockMap = D.stockMap;
  var stockQtyMap = D.stockQtyMap;
  var externalLocIds = (D.externalLocIds || []).map(String);

  // Tope de cantidad por fila: no se puede mover más de lo que hay en el origen.
  function clampBulkQty(line) {
    if (!line) return;
    var fromSel = document.getElementById("bulk_from_location_id");
    var loc = fromSel ? String(fromSel.value || "") : "";
    var itemSel = line.querySelector(".bulk-item");
    var item = itemSel ? String(itemSel.value || "") : "";
    var qty = line.querySelector('input[name="qty[]"]');
    if (!qty) return;
    var isExternal = externalLocIds.indexOf(loc) !== -1;
    if (isExternal || !loc || !item) { qty.removeAttribute("max"); return; }
    var mx = (stockQtyMap[loc] || {})[item];
    if (mx == null) { qty.removeAttribute("max"); return; }
    qty.setAttribute("max", mx);
    var v = parseInt(qty.value, 10);
    if (!isNaN(v) && v > mx) { qty.value = mx; }
  }
  // Tope de "cantidad a devolver" por fila: nunca más de lo que se entrega en
  // esa línea. El backend ya lo rechaza (y descarta TODA la carga), así que
  // conviene no dejar cargarlo.
  function clampBulkReturnQty(line) {
    if (!line) return;
    var qty = line.querySelector('input[name="qty[]"]');
    var ret = line.querySelector(".pending-return-qty");
    if (!qty || !ret) return;
    var d = parseInt(qty.value, 10);
    if (isNaN(d) || d <= 0) { ret.removeAttribute("max"); return; }
    ret.setAttribute("max", d);
    ret.placeholder = "Cant. a devolver (máx. " + d + ")";
    var v = parseInt(ret.value, 10);
    if (!isNaN(v) && v > d) ret.value = d;
  }

  function clampBulkAll() {
    document.querySelectorAll(".bulk-line").forEach(function (line) {
      clampBulkQty(line);
      clampBulkReturnQty(line);
    });
  }

  // Responsables del destino, para el selector de pendientes.
  var bulkRespMap = D.respMap;
  function refreshBulkResponsibles() {
    var toSel = document.getElementById("bulk_to_location_id");
    var sel = document.getElementById("bulk_pending_responsible_id");
    var box = document.getElementById("bulk_pending_resp_box");
    var hint = document.getElementById("bulk_pending_resp_hint");
    if (!sel || !box) return;
    var anyPending = Array.prototype.some.call(
      document.querySelectorAll(".pending-flag"),
      function (f) { return f.value === "1"; }
    );
    box.style.display = anyPending ? "block" : "none";
    var prev = sel.value;
    var list = bulkRespMap[String(toSel ? toSel.value : "")] || [];
    sel.innerHTML = "";
    if (!list.length) {
      var o = document.createElement("option");
      o.value = ""; o.textContent = "La ubicación destino no tiene responsable";
      sel.appendChild(o);
      sel.disabled = true;
      hint.textContent = "Asignale un responsable a la ubicación antes de generar pendientes.";
      return;
    }
    sel.disabled = false;
    if (list.length > 1) {
      var ph = document.createElement("option");
      ph.value = ""; ph.textContent = "Elegí el responsable...";
      sel.appendChild(ph);
    }
    list.forEach(function (u) {
      var op = document.createElement("option");
      op.value = String(u.id); op.textContent = u.name;
      sel.appendChild(op);
    });
    if (prev && sel.querySelector('option[value="' + prev + '"]')) sel.value = prev;
    hint.textContent = list.length > 1
      ? "El destino tiene más de un responsable: elegí a nombre de quién quedan."
      : "";
  }

  // Lista completa de items (para reconstruir opciones por ubicacion)
  var allBulkItems = D.allItems;

  function allowedForCurrentFrom() {
    var fromSel = document.getElementById("bulk_from_location_id");
    var locId = fromSel ? String(fromSel.value || "") : "";
    var isExternal = externalLocIds.indexOf(locId) !== -1;
    var allowed = stockMap[locId] || [];
    var set = {};
    allowed.forEach(function (id) { set[String(id)] = true; });
    return { locId: locId, set: set, all: isExternal };
  }

  // Opciones permitidas por STOCK para las filas (sin considerar el dedupe).
  // Externa (Proveedor/Baja): todos los ítems; interna: solo con stock.
  function bulkStockOptions() {
    var info = allowedForCurrentFrom();
    if (!info.locId) return [];
    return allBulkItems.filter(function (o) { return info.all || info.set[o.value]; });
  }

  // Un ítem = una sola fila: el dedupe saca de cada listado lo ya elegido en
  // las otras filas. El backend igual rechaza item_id[] repetidos.
  //
  // OJO (no mover a nivel top-level): este <script> inline se ejecuta ANTES que
  // los <script src> de base.html, que van al final del <body>. Acá arriba
  // todavía no existe initLineDedupe, así que llamarlo directo tira
  // ReferenceError y se cae TODO el script de la pantalla (no se arma ninguna
  // fila y "+ Agregar item" deja de responder). Por eso se crea perezoso: la
  // primera llamada real ocurre ya dentro de DOMContentLoaded.
  var _bulkDedupe = null;
  function bulkDedupe() {
    if (!_bulkDedupe) {
      _bulkDedupe = initLineDedupe({
        itemSel: ".bulk-item",
        optionsFor: bulkStockOptions,
        enabledFor: function () { return !!allowedForCurrentFrom().locId; }
      });
    }
    return _bulkDedupe;
  }

  function applyStockFilterAllLines() { bulkDedupe().refresh(); }

  // Cambio de "Desde": cambia el universo de stock, se limpia lo ya elegido.
  function resetStockFilterAllLines() {
    document.querySelectorAll(".bulk-item").forEach(function (el) {
      if (el.tomselect) el.tomselect.clear(true);
    });
    bulkDedupe().refresh();
  }

  function initTomSelectFor(el) {
    if (!el) return;
    if (el.tomselect) return;
    new TomSelect(el, {
      create: false,
      sortField: { field: "text", direction: "asc" },
      placeholder: "Elegí la ubicación de origen primero...",
      closeAfterSelect: true,
      onItemAdd: function () { this.setTextboxValue(''); this.blur(); },
      onChange: function () {
        this.blur();
        clampBulkQty(el.closest(".bulk-line"));
        // Liberar/tomar el ítem en las demás filas.
        applyStockFilterAllLines();
      }
    });
    applyStockFilterAllLines();
  }

  function applyDescartes(isDescartes) {
    document.querySelectorAll(".scrap-reason-wrap").forEach(function(el) {
      el.style.display = isDescartes ? "block" : "none";
    });
  }

  function wireBulkLine(line) {
    var itemSelect = line.querySelector(".bulk-item");
    var pendingFlag = line.querySelector(".pending-flag");
    var commentWrap = line.querySelector(".pending-comment-wrap");
    var removeBtn = line.querySelector(".remove-line-btn");

    initTomSelectFor(itemSelect);

    // Selector "qué deben devolver" buscable (no se filtra por stock de origen:
    // puede volver cualquier ítem, ej. entregás domo y deben traer Onvif).
    var returnItemSel = line.querySelector(".pending-return-item");
    if (returnItemSel && window.TomSelect && !returnItemSel.tomselect) {
      new TomSelect(returnItemSel, window.TS_SINGLE_OPTS);
    }

    var qtyEl = line.querySelector('input[name="qty[]"]');
    if (qtyEl) {
      qtyEl.addEventListener("input", function () {
        clampBulkQty(line);
        clampBulkReturnQty(line);
      });
    }
    var retQtyEl = line.querySelector(".pending-return-qty");
    if (retQtyEl) {
      retQtyEl.addEventListener("input", function () { clampBulkReturnQty(line); });
      retQtyEl.addEventListener("change", function () { clampBulkReturnQty(line); });
    }

    function togglePendingComment() {
      if (pendingFlag.value === "1") {
        commentWrap.style.display = "block";
        // Autorellenar la devolución con el mismo ítem y cantidad seleccionados
        // en la línea (el usuario puede cambiarlos después).
        var itemVal = itemSelect ? String(itemSelect.value || "") : "";
        if (itemVal) {
          if (returnItemSel && returnItemSel.tomselect) { returnItemSel.tomselect.setValue(itemVal, true); }
          else if (returnItemSel) { returnItemSel.value = itemVal; }
        }
        var rqty = commentWrap.querySelector('input[name="pending_return_qty[]"]');
        if (rqty && !rqty.value && qtyEl) { rqty.value = qtyEl.value; }
        clampBulkReturnQty(line);
      } else {
        commentWrap.style.display = "none";
        var input = commentWrap.querySelector('input[name="pending_comment[]"]');
        if (input) input.value = "";
        var rqty = commentWrap.querySelector('input[name="pending_return_qty[]"]');
        if (rqty) rqty.value = "";
        if (returnItemSel && returnItemSel.tomselect) { returnItemSel.tomselect.clear(true); }
        else if (returnItemSel) { returnItemSel.value = ""; }
      }
    }
    pendingFlag.addEventListener("change", function () {
      togglePendingComment();
      refreshBulkResponsibles();
    });
    togglePendingComment();
    refreshBulkResponsibles();

    // Al borrar la fila, su ítem vuelve a estar disponible en las demás.
    removeBtn.addEventListener("click", function () {
      line.remove();
      applyStockFilterAllLines();
      refreshBulkResponsibles();
    });
  }

  function addBulkLine() {
    var tpl = document.getElementById("bulk-line-template");
    var container = document.getElementById("bulk-lines");
    var fragment = tpl.content.cloneNode(true);
    container.appendChild(fragment);
    wireBulkLine(container.lastElementChild);
    var toSel = document.getElementById("bulk_to_location_id");
    applyDescartes(toSel.value === descartes_id);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("add-line-btn").addEventListener("click", addBulkLine);
    addBulkLine();
    var bulkToSel = document.getElementById("bulk_to_location_id");
    bulkToSel.addEventListener("change", function () {
      applyDescartes(this.value === descartes_id);
      refreshBulkResponsibles();
    });
    refreshBulkResponsibles();
    var bulkForm = document.getElementById("bulk-form");
    if (bulkForm) bulkForm.addEventListener("submit", clampBulkAll);
    // "Hacia" no ofrece la ubicación elegida en "Desde" (y viceversa). El
    // backend ya lo rechaza; esto evita que el usuario llegue a mandarlo.
    initFromToExclusion({
      from: document.getElementById("bulk_from_location_id"),
      to: bulkToSel
    });

    var bulkFromSel = document.getElementById("bulk_from_location_id");
    if (bulkFromSel) {
      bulkFromSel.addEventListener("change", function () {
        resetStockFilterAllLines(); clampBulkAll();
      });
    }
  });
})();
