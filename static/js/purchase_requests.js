/* purchase_requests.html - filas de "Otros ítems" en la nueva solicitud de compra.
 *
 * Desde 2026-09-30 una solicitud de compra puede llevar cualquier ítem activo,
 * no solo los que están en alerta. Los de alerta siguen en su tabla con tilde;
 * el resto se agrega acá, fila por fila.
 *
 * Mismo esquema que repair_requests.js: los DATOS vienen del servidor
 * (window.PR_DATA) y cada fila se clona desde un <template>.
 *
 * Un ítem = una sola fila: el dedupe saca de cada listado lo ya elegido en las
 * otras filas. Es solo UX: el backend (purchase_request_new) rechaza el POST
 * con un ítem repetido. Una fila sin ítem se ignora, así que dejar una fila de
 * más vacía no bloquea el envío (por eso el select no lleva `required`: un
 * control oculto detrás de TomSelect y obligatorio frena el envío sin avisar).
 */
(function () {
  var D = window.PR_DATA || {};

  document.addEventListener("DOMContentLoaded", function () {
    var cont = document.getElementById("pr-extra-lines");
    var tpl = document.getElementById("pr-extra-line-template");
    var addBtn = document.getElementById("pr-add-line-btn");
    if (!cont || !tpl || !addBtn) return;

    var otherItems = D.otherItems || [];

    var dedupe = initLineDedupe({
      scope: cont,
      itemSel: 'select[name="extra_item_id[]"]',
      optionsFor: function () { return otherItems; }
    });

    function wireLine(line) {
      var rm = line.querySelector(".pr-remove-line-btn");
      // Al borrar la fila, su ítem vuelve a estar disponible en las demás.
      if (rm) rm.addEventListener("click", function () { line.remove(); dedupe.refresh(); });
      var sel = line.querySelector('select[name="extra_item_id[]"]');
      if (sel && window.TomSelect && !sel.tomselect) {
        new TomSelect(sel, window.TS_SINGLE_OPTS);
      }
      if (sel) sel.addEventListener("change", function () { dedupe.refresh(); });
    }

    function addLine() {
      cont.appendChild(tpl.content.cloneNode(true));
      wireLine(cont.lastElementChild);
      dedupe.refresh();
    }

    addBtn.addEventListener("click", addLine);
    addLine();
  });
})();
