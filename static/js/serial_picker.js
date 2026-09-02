// Selector de seriales para ítems serializados (auto/elegir).
// Compartido por Movimientos, Utilizados y Descartes.
//
// Regla: si en el origen hay 1 serial (o tantos como la cantidad a mover), se usan
// automáticamente. Si hay más seriales que la cantidad, el usuario elige cuáles.
//
// opts: { unitsMap, serializedItems, itemEl, fromSel, qtyInput,
//         pickBox, pickList, pickStatus, autoHint, autoText,
//         idsInput, summaryInput }
//
// idsInput      -> input oculto que recibe los ids elegidos separados por coma.
//                  Lo usan las pantallas MULTI-FILA (Utilizados y Descartes):
//                  ahí los checkboxes no pueden llamarse todos "unit_id" porque
//                  no habría forma de saber qué serial es de qué fila. Con este
//                  campo, cada fila manda lo suyo en un array alineado a
//                  item_id[] / qty[]. Si no se pasa, los checkboxes van con
//                  name="unit_id" como siempre (Movimientos, un solo ítem).
// summaryInput  -> input visible de solo lectura con los seriales en texto. Es
//                  lo que hace que el MODAL DE CONFIRMACIÓN diga qué seriales
//                  salen: confirm_move.js no puede leerlo de los checkboxes
//                  (sólo sabría decir "Sí") ni de un campo oculto (los saltea).
//
// Devuelve una función refresh() para re-evaluar la UI ante cambios externos.
function initSerialPicker(opts) {
  var unitsMap = opts.unitsMap || {};
  var serializedItems = opts.serializedItems || [];
  var itemEl = opts.itemEl, fromSel = opts.fromSel, qtyInput = opts.qtyInput;
  var pickBox = opts.pickBox, pickList = opts.pickList, pickStatus = opts.pickStatus;
  var autoHint = opts.autoHint, autoText = opts.autoText;
  var idsInput = opts.idsInput || null;
  var summaryInput = opts.summaryInput || null;

  function boxes() {
    return pickList ? pickList.querySelectorAll('input[type="checkbox"]') : [];
  }
  function chosen() {
    return Array.prototype.filter.call(boxes(), function (cb) { return cb.checked; });
  }
  /* Publica lo elegido: los ids para el backend, los seriales para el humano. */
  function publicar(serialesAuto) {
    if (serialesAuto) {
      if (idsInput) idsInput.value = "";          // vacío = que resuelva el backend
      if (summaryInput) summaryInput.value = serialesAuto.join(", ");
      return;
    }
    var els = chosen();
    if (idsInput) {
      idsInput.value = els.map(function (cb) { return cb.value; }).join(",");
    }
    if (summaryInput) {
      summaryInput.value = els.map(function (cb) { return cb.getAttribute("data-serial"); }).join(", ");
    }
  }
  function limpiar() {
    if (pickList) pickList.innerHTML = "";
    if (idsInput) idsInput.value = "";
    if (summaryInput) summaryInput.value = "";
  }

  function currentItemId() {
    if (itemEl && itemEl.tomselect) return String(itemEl.tomselect.getValue() || "");
    return itemEl ? String(itemEl.value || "") : "";
  }
  function currentQty() {
    var q = parseInt(qtyInput ? qtyInput.value : "1", 10);
    return (isNaN(q) || q < 1) ? 1 : q;
  }
  function hideAll() {
    if (pickBox) pickBox.style.display = "none";
    if (autoHint) autoHint.style.display = "none";
    // Se limpia además de ocultar: un checkbox que quedó tildado de un ítem
    // anterior se sigue enviando aunque no se vea.
    limpiar();
  }
  function updateStatus(need) {
    if (!pickStatus || !pickList) return;
    pickStatus.textContent = "elegí " + need + " · seleccionados " + chosen().length;
  }
  function refresh() {
    if (!qtyInput) return;
    var itemId = currentItemId();
    var fromId = fromSel ? String(fromSel.value || "") : "";
    var isSerial = itemId && serializedItems.indexOf(parseInt(itemId, 10)) !== -1;
    if (!isSerial) { hideAll(); return; }

    var byLoc = unitsMap[itemId] || {};
    var units = fromId ? (byLoc[fromId] || []) : [];
    var qty = currentQty();

    if (units.length === 0 || units.length <= qty) {
      if (pickBox) pickBox.style.display = "none";
      limpiar();
      if (autoHint) {
        autoHint.style.display = "";
        if (autoText) {
          autoText.textContent = units.length === 0
            ? "No hay seriales cargados en el origen: se mueve por cantidad."
            : "Se usan automáticamente los " + units.length + " serial(es) disponible(s).";
        }
      }
      // Aunque los elija el backend, se muestran: el modal de confirmación
      // tiene que decir QUÉ seriales salen, no cuántos.
      publicar(units.map(function (pair) { return pair[1]; }));
      return;
    }

    if (autoHint) autoHint.style.display = "none";
    if (pickBox) pickBox.style.display = "";
    if (pickList) pickList.innerHTML = "";

    units.forEach(function (pair) {
      var uid = pair[0], serial = pair[1];
      var lbl = document.createElement("label");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      // Sin idsInput (Movimientos) el backend lee los checkboxes directamente.
      // Con idsInput (multi-fila) la verdad la lleva el campo alineado.
      if (!idsInput) cb.name = "unit_id";
      cb.value = uid;
      cb.setAttribute("data-serial", serial);
      // El resumen del modal sale del summaryInput; de los checkboxes sólo
      // podría decir "Sí", que no informa nada.
      cb.setAttribute("data-confirm-skip", "");
      cb.addEventListener("change", function () {
        if (chosen().length > qty) { cb.checked = false; }
        publicar(null);
        updateStatus(qty);
      });
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(serial));
      pickList.appendChild(lbl);
    });
    publicar(null);
    updateStatus(qty);
  }

  if (qtyInput) qtyInput.addEventListener("input", refresh);
  return refresh;
}
