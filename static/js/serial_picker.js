// Selector de seriales para ítems serializados (auto/elegir).
// Compartido por Movimientos, Utilizados y Descarte.
//
// Regla: si en el origen hay 1 serial (o tantos como la cantidad a mover), se usan
// automáticamente. Si hay más seriales que la cantidad, el usuario elige cuáles.
//
// opts: { unitsMap, serializedItems, itemEl, fromSel, qtyInput,
//         pickBox, pickList, pickStatus, autoHint, autoText }
// Devuelve una función refresh() para re-evaluar la UI ante cambios externos.
function initSerialPicker(opts) {
  var unitsMap = opts.unitsMap || {};
  var serializedItems = opts.serializedItems || [];
  var itemEl = opts.itemEl, fromSel = opts.fromSel, qtyInput = opts.qtyInput;
  var pickBox = opts.pickBox, pickList = opts.pickList, pickStatus = opts.pickStatus;
  var autoHint = opts.autoHint, autoText = opts.autoText;

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
  }
  function updateStatus(need) {
    if (!pickStatus || !pickList) return;
    var n = pickList.querySelectorAll('input[name="unit_id"]:checked').length;
    pickStatus.textContent = "elegí " + need + " · seleccionados " + n;
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
      if (autoHint) {
        autoHint.style.display = "";
        if (autoText) {
          autoText.textContent = units.length === 0
            ? "No hay seriales cargados en el origen: se mueve por cantidad."
            : "Se usan automáticamente los " + units.length + " serial(es) disponible(s).";
        }
      }
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
      cb.name = "unit_id";
      cb.value = uid;
      cb.addEventListener("change", function () {
        var checked = pickList.querySelectorAll('input[name="unit_id"]:checked');
        if (checked.length > qty) { cb.checked = false; }
        updateStatus(qty);
      });
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(serial));
      pickList.appendChild(lbl);
    });
    updateStatus(qty);
  }

  if (qtyInput) qtyInput.addEventListener("input", refresh);
  return refresh;
}
