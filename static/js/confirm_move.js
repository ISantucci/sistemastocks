/* ---------- Confirmación de movimientos ----------
   Intercepta el envío de los formularios que generan movimientos de stock y
   muestra un resumen de lo que se va a hacer, con opción de cancelar.

   Por qué: un movimiento mal cargado ensucia stock e historial, y revertirlo
   siempre cuesta más que no hacerlo. Este paso es una barrera contra el error
   humano, NO un control de permisos: la autorización real la sigue haciendo el
   backend (ver los @role_required y las validaciones de cada ruta en app.py).

   Cómo se activa: `data-confirm-move="Título"` en el <form>. Nada más.
   Atributos opcionales:
     data-confirm-danger   -> marca la acción como irreversible (aviso + botón rojo)
     data-confirm-context  -> texto fijo de contexto (útil en forms por fila)
     data-confirm-row      -> en el contenedor de una fila repetible (name="campo[]")
     data-confirm-row-title-> título de esa fila en el resumen. Sirve cuando lo que
                              identifica a la fila no es un campo del formulario
                              (ej. el repuesto en el cierre de una solicitud).
     data-confirm-skip     -> en un campo que no debe aparecer en el resumen

   El resumen se arma leyendo el propio formulario, así ningún campo queda
   afuera por olvido. Todo el texto se inserta con textContent: los nombres de
   ítems y las observaciones los escribe el usuario, nunca se interpretan como
   HTML.
*/
(function () {
  "use strict";

  var OVERLAY_ID = "modal-confirm-move";

  /* Un control cuenta como visible si su contenedor lo está. No se puede mirar
     el input directo: TomSelect oculta el <select> original y lo reemplaza por
     su propio widget, así que el select "visible" tiene offsetParent null. */
  function isVisible(el) {
    var box = el.closest(".field, [data-confirm-row]") || el.parentElement;
    if (!box) return true;
    return box.offsetParent !== null;
  }

  /* Nombre legible del campo: la <label> de su .field, o la que lo envuelve
     (caso de los checkbox/radio inline), o el atributo name como último recurso. */
  function labelFor(el) {
    var field = el.closest(".field");
    var lab = field ? field.querySelector("label") : null;
    if (!lab) lab = el.closest("label");
    var txt = lab ? lab.textContent.trim() : "";
    txt = txt.replace(/\s+/g, " ").replace(/[:*]\s*$/, "");
    return txt || el.getAttribute("name") || "";
  }

  /* Valor legible. En los <select> interesa el texto de la opción elegida, no
     el id: el resumen tiene que decir "Jaula TNG", no "3". */
  function valueFor(el) {
    if (el.tagName === "SELECT") {
      var opt = el.options[el.selectedIndex];
      if (!opt || !opt.value) return "";
      return opt.textContent.trim();
    }
    if (el.type === "checkbox") return el.checked ? "Sí" : "";
    if (el.type === "radio") return el.checked ? labelFor(el) : "";
    return (el.value || "").trim();
  }

  function isReportable(el) {
    if (!el.name) return false;
    if (el.disabled) return false;
    if (el.hasAttribute("data-confirm-skip")) return false;
    if (el.name === "csrf_token") return false;
    if (el.type === "hidden" || el.type === "submit" || el.type === "button") return false;
    return true;
  }

  /* Los radios se agrupan: del grupo sólo interesa el que quedó marcado. */
  function collectFields(root, seenRadios) {
    var out = [];
    root.querySelectorAll("input, select, textarea").forEach(function (el) {
      if (!isReportable(el) || !isVisible(el)) return;
      if (el.type === "radio") {
        if (!el.checked) return;
        if (seenRadios[el.name]) return;
        seenRadios[el.name] = true;
        out.push({ label: "Acción", value: labelFor(el) });
        return;
      }
      var val = valueFor(el);
      if (!val) return;
      out.push({ label: labelFor(el), value: val });
    });
    return out;
  }

  /* Filas repetibles (carga múltiple, ingresos/egresos, utilizados, descartes).
     Se ignoran las filas vacías: el usuario suele dejar líneas de más sin usar. */
  function collectRows(form) {
    var rows = [];
    form.querySelectorAll("[data-confirm-row]").forEach(function (rowEl) {
      if (rowEl.offsetParent === null) return;
      var itemSel = rowEl.querySelector('select[name^="item_id"]');
      if (itemSel && !itemSel.value) return;
      var fields = collectFields(rowEl, {});
      if (fields.length) {
        rows.push({
          title: (rowEl.getAttribute("data-confirm-row-title") || "").trim(),
          fields: fields,
        });
      }
    });
    return rows;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildOverlay() {
    var overlay = document.getElementById(OVERLAY_ID);
    if (overlay) return overlay;

    overlay = el("div", "modal-overlay");
    overlay.id = OVERLAY_ID;

    var modal = el("div", "modal");
    var header = el("div", "modal-header");
    header.appendChild(el("span", "modal-title", "Confirmar"));
    var close = el("button", "modal-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Cerrar");
    header.appendChild(close);

    var body = el("div", "modal-body");
    body.id = "confirm-move-body";

    var footer = el("div", "modal-footer");
    var cancel = el("button", "btn btn-secondary", "Cancelar");
    cancel.type = "button";
    var ok = el("button", "btn btn-primary", "Confirmar");
    ok.type = "button";
    ok.id = "confirm-move-ok";
    footer.appendChild(cancel);
    footer.appendChild(ok);

    modal.appendChild(header);
    modal.appendChild(body);
    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    close.addEventListener("click", function () { closeModal(OVERLAY_ID); });
    cancel.addEventListener("click", function () { closeModal(OVERLAY_ID); });
    return overlay;
  }

  function renderSummary(body, form) {
    body.textContent = "";

    var context = form.getAttribute("data-confirm-context");
    if (context) {
      var ctx = el("p", null, context);
      ctx.style.margin = "0";
      ctx.style.fontWeight = "600";
      body.appendChild(ctx);
    }

    var seenRadios = {};
    var general = [];
    form.querySelectorAll("input, select, textarea").forEach(function (node) {
      if (node.closest("[data-confirm-row]")) return;
      if (!isReportable(node) || !isVisible(node)) return;
      if (node.type === "radio") {
        if (!node.checked || seenRadios[node.name]) return;
        seenRadios[node.name] = true;
        general.push({ label: "Acción", value: labelFor(node) });
        return;
      }
      var val = valueFor(node);
      if (val) general.push({ label: labelFor(node), value: val });
    });

    if (general.length) body.appendChild(buildTable(general));

    var rows = collectRows(form);
    if (rows.length) {
      var title = el("p", "muted", rows.length === 1 ? "1 ítem" : rows.length + " ítems");
      title.style.margin = "4px 0 0";
      body.appendChild(title);
      rows.forEach(function (row) {
        /* El título identifica la fila cuando el dato no es un campo del form
           (por ejemplo el repuesto en el cierre de una solicitud). */
        if (row.title) {
          var head = el("p", null, row.title);
          head.style.margin = "8px 0 2px";
          head.style.fontWeight = "600";
          body.appendChild(head);
        }
        body.appendChild(buildTable(row.fields));
      });
    }

    if (!general.length && !rows.length) {
      body.appendChild(el("p", "muted", "No hay datos cargados para confirmar."));
    }

    if (form.hasAttribute("data-confirm-danger")) {
      var warn = el("p", null, "Esta operación no se puede deshacer.");
      warn.style.margin = "4px 0 0";
      warn.style.fontWeight = "600";
      warn.style.color = "var(--danger, #b42318)";
      body.appendChild(warn);
    }
  }

  function buildTable(fields) {
    var table = el("table", "data-table");
    var tbody = el("tbody");
    fields.forEach(function (f) {
      var tr = el("tr");
      var th = el("td", "muted", f.label);
      th.style.width = "40%";
      tr.appendChild(th);
      var td = el("td", null, f.value);
      td.style.fontWeight = "600";
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var wrap = el("div", "table-card");
    wrap.style.margin = "0";
    wrap.appendChild(table);
    return wrap;
  }

  function handleSubmit(ev) {
    var form = ev.target;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-confirm-move")) return;
    /* Otra validación de la propia pantalla ya frenó el envío (por ejemplo la
       de seriales en Ingresos/Egresos). No hay nada que confirmar. */
    if (ev.defaultPrevented) return;
    if (form.dataset.confirmed === "1") return;

    ev.preventDefault();

    var overlay = buildOverlay();
    overlay.querySelector(".modal-title").textContent =
      form.getAttribute("data-confirm-move") || "Confirmar";
    renderSummary(document.getElementById("confirm-move-body"), form);

    var ok = document.getElementById("confirm-move-ok");
    ok.className = form.hasAttribute("data-confirm-danger")
      ? "btn btn-danger" : "btn btn-primary";
    ok.disabled = false;

    /* Se reemplaza el botón para no acumular listeners entre aperturas. */
    var fresh = ok.cloneNode(true);
    ok.parentNode.replaceChild(fresh, ok);
    fresh.addEventListener("click", function () {
      fresh.disabled = true;          // evita el doble envío por doble clic
      form.dataset.confirmed = "1";
      closeModal(OVERLAY_ID);
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });

    openModal(OVERLAY_ID);
  }

  /* Se escucha en captura sobre document para cubrir también los formularios
     que se agregan al DOM después de cargar la página. */
  document.addEventListener("submit", handleSubmit);
})();
