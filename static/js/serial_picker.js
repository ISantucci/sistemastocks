// Selector de seriales para ítems serializados.
// Compartido por Movimientos, Utilizados, Descartes, Carga múltiple (con
// initSerialPicker) y por Pendientes, Reparaciones y Entrega de repuestos (con
// los selectores armados en el template, ver initStaticSerialPickers).
//
// Desde 2026-10-01 se elige IGUAL QUE EN EGRESOS (pedido tras la capacitación):
// un botón «Elegir S/N» abre un popup con los seriales disponibles, un contador
// "Seleccionados n de N · disponibles en <origen>" y Confirmar, que se habilita
// recién con la cantidad exacta. Después el botón dice "S/N: n elegidos".
// Antes cada pantalla dibujaba la lista de casillas en el propio formulario y
// con muchos seriales la pantalla se volvía inmanejable (sobre todo en mobile).
//
// Regla (no cambió): si en el origen hay 1 serial (o tantos como la cantidad a
// mover), se usan automáticamente. El botón lo dice ("automático") y el popup
// los muestra ya tildados. Si hay más seriales que la cantidad, hay que elegir.
// Si no hay seriales cargados, se mueve por cantidad.
//
// Lo que viaja al servidor es EXACTAMENTE lo mismo que antes: el backend no
// cambió.
//
// opts: { unitsMap, serializedItems, itemEl, fromSel, qtyInput,
//         pickBox, pickList, pickStatus, autoHint, autoText,
//         idsInput, summaryInput }
//
// idsInput      -> input oculto que recibe los ids elegidos separados por coma.
//                  Lo usan las pantallas MULTI-FILA (Utilizados, Descartes,
//                  Carga múltiple): ahí no pueden viajar todos como "unit_id"
//                  porque no habría forma de saber qué serial es de qué fila.
//                  Si no se pasa, los ids viajan como inputs ocultos
//                  name="unit_id" dentro de pickList (Movimientos, un ítem).
// summaryInput  -> input visible de solo lectura con los seriales en texto. Es
//                  lo que hace que el MODAL DE CONFIRMACIÓN diga qué seriales
//                  salen: confirm_move.js saltea los campos ocultos.
//
// Devuelve una función refresh() para re-evaluar la UI ante cambios externos.

var SN_MODAL_ID = "modal-sn-picker";

function snEl(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

/* El popup es UNO para toda la pantalla (como el de Egresos): se arma la
   primera vez que se usa y se rellena en cada apertura. Vive fuera de los
   formularios, por eso las casillas no viajan: al confirmar se publica lo
   elegido en los campos del formulario. */
function snModal() {
  var overlay = document.getElementById(SN_MODAL_ID);
  if (overlay) return overlay;
  overlay = snEl("div", "modal-overlay");
  overlay.id = SN_MODAL_ID;
  var modal = snEl("div", "modal");
  var header = snEl("div", "modal-header");
  var title = snEl("span", "modal-title", "Elegí los seriales");
  title.id = "sn-picker-title";
  var close = snEl("button", "modal-close", "×");
  close.type = "button";
  close.setAttribute("aria-label", "Cerrar");
  header.appendChild(title);
  header.appendChild(close);
  var body = snEl("div", "modal-body");
  body.id = "sn-picker-body";
  body.style.maxHeight = "60vh";
  body.style.overflow = "auto";
  var footer = snEl("div", "modal-footer");
  var cancel = snEl("button", "btn btn-secondary", "Cancelar");
  cancel.type = "button";
  var ok = snEl("button", "btn btn-primary", "Confirmar");
  ok.type = "button";
  ok.id = "sn-picker-ok";
  footer.appendChild(cancel);
  footer.appendChild(ok);
  modal.appendChild(header);
  modal.appendChild(body);
  modal.appendChild(footer);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
  close.addEventListener("click", function () { closeModal(SN_MODAL_ID); });
  cancel.addEventListener("click", function () { closeModal(SN_MODAL_ID); });
  return overlay;
}

/* cfg: { units: [[id, serial], ...], qty, mode: "exact" | "max" | "auto",
          selected: [ids], origin, onConfirm(ids) }
   exact -> hay que elegir exactamente qty (Confirmar se habilita recién ahí).
   max   -> hasta qty (entrega de repuestos: se puede entregar menos).
   auto  -> se usan todos: se muestran tildados y no se pueden cambiar. */
function openSerialModal(cfg) {
  var overlay = snModal();
  var body = document.getElementById("sn-picker-body");
  var origin = cfg.origin || "el origen";
  document.getElementById("sn-picker-title").textContent =
    "Elegí los seriales (" + origin + ")";
  body.textContent = "";

  var counter = snEl("p", "muted");
  counter.style.marginTop = "0";
  body.appendChild(counter);

  var units = cfg.units || [];
  var qty = cfg.qty || 0;
  var mode = cfg.mode || "exact";
  var sel = {};
  (cfg.selected || []).forEach(function (id) { sel[String(id)] = true; });

  units.forEach(function (pair) {
    var lab = snEl("label", "sn-row");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "sn-check";
    cb.value = String(pair[0]);
    cb.setAttribute("data-serial", pair[1]);
    if (mode === "auto") { cb.checked = true; cb.disabled = true; }
    else if (sel[cb.value]) { cb.checked = true; }
    lab.appendChild(cb);
    lab.appendChild(snEl("span", null, pair[1]));
    body.appendChild(lab);
  });

  var ok = document.getElementById("sn-picker-ok");
  var fresh = ok.cloneNode(true);   // sin listeners de aperturas anteriores
  ok.parentNode.replaceChild(fresh, ok);
  ok = fresh;

  function checks() { return body.querySelectorAll(".sn-check"); }
  function chosen() {
    return Array.prototype.filter.call(checks(), function (c) { return c.checked; });
  }
  function update() {
    var n = chosen().length;
    if (mode === "auto") {
      counter.textContent = units.length === 1
        ? "Es el único serial disponible en " + origin + ": se usa automáticamente."
        : "Hay tantos seriales como la cantidad: se usan automáticamente los "
          + units.length + " disponibles en " + origin + ".";
      ok.disabled = false;
    } else if (mode === "max") {
      counter.textContent = "Seleccionados " + n + " (hasta " + qty + ") · disponibles en "
        + origin + ": " + units.length;
      ok.disabled = n > qty;
    } else {
      counter.textContent = "Seleccionados " + n + " de " + qty + " · disponibles en "
        + origin + ": " + units.length;
      ok.disabled = (qty < 1 || n !== qty);
    }
  }
  Array.prototype.forEach.call(checks(), function (cb) {
    cb.addEventListener("change", function () {
      // No deja tildar de más: igual que antes en la lista del formulario.
      if (mode !== "auto" && chosen().length > qty) cb.checked = false;
      update();
    });
  });
  ok.addEventListener("click", function () {
    if (ok.disabled) return;
    if (mode !== "auto" && typeof cfg.onConfirm === "function") {
      cfg.onConfirm(chosen().map(function (c) { return c.value; }));
    }
    closeModal(SN_MODAL_ID);
  });
  update();
  openModal(SN_MODAL_ID);
}

function snButtonText(mode, count) {
  if (mode === "auto") return "S/N: " + count + " (automático)";
  if (count > 0) return "S/N: " + count + " elegido" + (count > 1 ? "s" : "");
  return "Elegir S/N";
}

function snAutoStatus(n) {
  return n === 1 ? "se usa el único disponible" : "se usan los " + n + " disponibles";
}

function snAviso(msg) {
  if (typeof window.showFormError === "function") window.showFormError(msg);
  else alert(msg);
}

/* Freno al enviar, como en Egresos: un serializado que pide elegir y no tiene
   los seriales elegidos no se manda. El backend igual lo rechaza; esto evita
   perder la carga por algo que se puede avisar acá. Un solo listener por
   formulario, que corre ANTES que el de confirmación (está en el form y aquel
   en document): si frena, el modal de confirmación ni aparece. */
function snRegisterCheck(form, check) {
  if (!form) return;
  if (!form._snChecks) {
    form._snChecks = [];
    form.addEventListener("submit", function (ev) {
      for (var i = 0; i < form._snChecks.length; i++) {
        var msg = form._snChecks[i]();
        if (msg) { ev.preventDefault(); snAviso(msg); return; }
      }
    });
  }
  form._snChecks.push(check);
}

var SN_FALTA = "Hay un ítem serializado sin seriales elegidos. Usá «Elegir S/N».";

function initSerialPicker(opts) {
  var unitsMap = opts.unitsMap || {};
  var serializedItems = opts.serializedItems || [];
  var itemEl = opts.itemEl, fromSel = opts.fromSel, qtyInput = opts.qtyInput;
  var pickBox = opts.pickBox, pickList = opts.pickList, pickStatus = opts.pickStatus;
  var autoHint = opts.autoHint, autoText = opts.autoText;
  var idsInput = opts.idsInput || null;
  var summaryInput = opts.summaryInput || null;

  var state = { key: null, selected: [], mode: null, units: [], qty: 0 };
  var btn = null, hiddenWrap = null;

  function ensureButton() {
    if (!pickList) return null;
    if (btn && pickList.contains(btn)) return btn;
    pickList.innerHTML = "";
    btn = snEl("button", "btn btn-secondary sn-open-btn", "Elegir S/N");
    btn.type = "button";
    btn.addEventListener("click", function () {
      openSerialModal({
        units: state.units, qty: state.qty, mode: state.mode,
        selected: state.selected, origin: originName(),
        onConfirm: function (ids) {
          state.selected = ids;
          publicar();
        }
      });
    });
    hiddenWrap = snEl("span");
    pickList.appendChild(btn);
    pickList.appendChild(hiddenWrap);
    return btn;
  }

  function originName() {
    if (fromSel && fromSel.options && fromSel.selectedIndex >= 0) {
      var o = fromSel.options[fromSel.selectedIndex];
      if (o && o.value) return o.text.trim();
    }
    // Técnico con una sola camioneta: el origen es un input oculto.
    if (fromSel && fromSel.getAttribute && fromSel.getAttribute("data-label")) {
      return fromSel.getAttribute("data-label");
    }
    return "el origen";
  }

  function syncSummaryBox() {
    if (!summaryInput) return;
    var box = summaryInput.closest(".serial-summary-box, #serial_summary_box");
    if (box) box.style.display = summaryInput.value ? "" : "none";
  }

  /* Publica lo elegido: los ids para el backend, los seriales para el humano. */
  function publicar() {
    var serialOf = {};
    state.units.forEach(function (p) { serialOf[String(p[0])] = p[1]; });
    if (hiddenWrap) hiddenWrap.innerHTML = "";
    if (state.mode === "auto") {
      if (idsInput) idsInput.value = "";          // vacío = que resuelva el backend
      if (summaryInput) summaryInput.value = state.units.map(function (p) { return p[1]; }).join(", ");
    } else {
      var ids = state.selected;
      if (idsInput) {
        idsInput.value = ids.join(",");
      } else if (hiddenWrap) {
        // Movimientos: el backend lee "unit_id" del form, como siempre.
        ids.forEach(function (id) {
          var h = document.createElement("input");
          h.type = "hidden";
          h.name = "unit_id";
          h.value = id;
          hiddenWrap.appendChild(h);
        });
      }
      if (summaryInput) summaryInput.value = ids.map(function (id) { return serialOf[id] || id; }).join(", ");
    }
    if (btn) btn.textContent = snButtonText(state.mode,
      state.mode === "auto" ? state.units.length : state.selected.length);
    if (pickStatus) {
      pickStatus.textContent = state.mode === "auto"
        ? snAutoStatus(state.units.length)
        : "elegí " + state.qty + " · seleccionados " + state.selected.length;
    }
    syncSummaryBox();
  }

  function limpiar() {
    if (pickList) pickList.innerHTML = "";
    btn = null; hiddenWrap = null;
    if (idsInput) idsInput.value = "";
    if (summaryInput) summaryInput.value = "";
    syncSummaryBox();
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
    state.mode = null;
    // Se limpia además de ocultar: un serial que quedó elegido de un ítem
    // anterior se seguiría enviando aunque no se vea.
    limpiar();
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

    // Cambió el ítem, el origen o la cantidad: lo elegido ya no vale (mismo
    // criterio que Egresos, que limpia los seriales al cambiar la cantidad).
    var key = itemId + "|" + fromId + "|" + qty;
    if (key !== state.key) { state.key = key; state.selected = []; }
    state.units = units;
    state.qty = qty;

    if (units.length === 0) {
      if (pickBox) pickBox.style.display = "none";
      state.mode = null;
      limpiar();
      if (autoHint) {
        autoHint.style.display = "";
        if (autoText) autoText.textContent = "No hay seriales cargados en el origen: se mueve por cantidad.";
      }
      return;
    }

    if (autoHint) autoHint.style.display = "none";
    if (pickBox) pickBox.style.display = "";
    ensureButton();
    state.mode = units.length <= qty ? "auto" : "exact";
    if (state.mode === "exact") {
      var valid = {};
      units.forEach(function (p) { valid[String(p[0])] = true; });
      state.selected = state.selected.filter(function (id) { return valid[id]; }).slice(0, qty);
    }
    publicar();
  }

  if (qtyInput) {
    qtyInput.addEventListener("input", refresh);
    snRegisterCheck(qtyInput.form, function () {
      if (state.mode !== "exact") return null;
      if (!pickBox || pickBox.offsetParent === null) return null;   // fila borrada u oculta
      return state.selected.length === state.qty ? null : SN_FALTA;
    });
  }
  return refresh;
}

/* Selectores armados en el template (Pendientes, Reparaciones, Entrega de
   repuestos). El marcado es:

   <div class="field js-sn-picker" data-units='[[id, "SN"], ...]' data-qty="1"
        data-mode="exact|max" data-name="unit_id" data-origin="Camioneta X"
        [data-line="12"]>
     <label>¿Qué serial ...? <span class="muted serial-pick-status"></span></label>
     <div class="serial-pick-list"></div>
     <input type="text" class="serial-summary" name="serials_txt" readonly tabindex="-1">
   </div>

   Lo elegido viaja como inputs ocultos con el name de data-name: es lo mismo
   que mandaban las casillas que había antes, así que el backend no cambia.
   Al confirmar se dispara "sn:change" (burbujea) para la pantalla que lo
   necesite (Entrega de repuestos recalcula cuánto se entrega). */
function initStaticSerialPickers(root) {
  var boxes = (root || document).querySelectorAll(".js-sn-picker");
  Array.prototype.forEach.call(boxes, function (box) {
    if (box._snInit) return;
    box._snInit = true;
    var units = [];
    try { units = JSON.parse(box.getAttribute("data-units") || "[]"); } catch (e) { units = []; }
    var qty = parseInt(box.getAttribute("data-qty"), 10) || 1;
    var max = box.getAttribute("data-mode") === "max";
    var name = box.getAttribute("data-name") || "unit_id";
    var origin = box.getAttribute("data-origin") || "el origen";
    var line = box.getAttribute("data-line");
    var list = box.querySelector(".serial-pick-list");
    var status = box.querySelector(".serial-pick-status");
    var summary = box.querySelector(".serial-summary");
    if (!list) return;

    var mode = max ? "max" : (units.length <= qty ? "auto" : "exact");
    var selected = [];
    var serialOf = {};
    units.forEach(function (p) { serialOf[String(p[0])] = p[1]; });

    list.innerHTML = "";
    var btn = snEl("button", "btn btn-secondary sn-open-btn", "Elegir S/N");
    btn.type = "button";
    var hidden = snEl("span");
    list.appendChild(btn);
    list.appendChild(hidden);

    function publicar() {
      hidden.innerHTML = "";
      var ids = mode === "auto" ? [] : selected;   // auto: lo resuelve el backend
      ids.forEach(function (id) {
        var h = document.createElement("input");
        h.type = "hidden";
        h.name = name;
        h.value = id;
        h.className = "sn-hidden";
        if (line) h.setAttribute("data-line", line);
        hidden.appendChild(h);
      });
      var shown = mode === "auto" ? units.map(function (p) { return p[1]; })
                                  : selected.map(function (id) { return serialOf[id] || id; });
      if (summary) {
        summary.value = shown.join(", ");
        summary.style.display = summary.value ? "" : "none";
      }
      btn.textContent = snButtonText(mode, mode === "auto" ? units.length : selected.length);
      if (status) {
        status.textContent = mode === "auto" ? snAutoStatus(units.length)
          : mode === "max" ? "hasta " + qty + " · seleccionados " + selected.length
          : "elegí " + qty + " · seleccionados " + selected.length;
      }
      box.dispatchEvent(new CustomEvent("sn:change", { bubbles: true }));
    }

    btn.addEventListener("click", function () {
      openSerialModal({
        units: units, qty: qty, mode: mode, selected: selected, origin: origin,
        onConfirm: function (ids) { selected = ids; publicar(); }
      });
    });

    if (mode === "exact") {
      snRegisterCheck(box.closest("form"), function () {
        if (box.offsetParent === null) return null;   // oculto (ej. otro origen)
        return selected.length === qty ? null : SN_FALTA;
      });
    }
    publicar();
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", function () { initStaticSerialPickers(); });
} else {
  initStaticSerialPickers();
}
