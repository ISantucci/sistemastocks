/* repair_requests.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.RR_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.RR_DATA || {};

document.addEventListener("DOMContentLoaded", function () {
  var allRrItems = D.allItems;

  // Un repuesto = una sola fila: el dedupe saca de cada listado lo ya elegido
  // en las otras filas. El backend igual rechaza item_id[] repetidos.
  var rrDedupe = initLineDedupe({
    itemSel: '.rr-line select[name="item_id[]"]',
    optionsFor: function () { return allRrItems; }
  });

  function wireLine(line) {
    var rm = line.querySelector(".remove-line-btn");
    // Al borrar la fila, su repuesto vuelve a estar disponible en las demas.
    if (rm) rm.addEventListener("click", function () { line.remove(); rrDedupe.refresh(); });
    // Selector de repuesto buscable/tipeable (las lineas se clonan, el init
    // global de TomSelect no las alcanza, asi que lo inicializamos por linea).
    var sel = line.querySelector('select[name="item_id[]"]');
    if (sel && window.TomSelect && !sel.tomselect) {
      new TomSelect(sel, window.TS_SINGLE_OPTS);
    }
    if (sel) {
      sel.addEventListener("change", function () { rrDedupe.refresh(); });
    }
  }
  function addLine() {
    var tpl = document.getElementById("rr-line-template");
    var cont = document.getElementById("rr-lines");
    cont.appendChild(tpl.content.cloneNode(true));
    wireLine(cont.lastElementChild);
    rrDedupe.refresh();
  }
  document.getElementById("add-line-btn").addEventListener("click", addLine);
  addLine();
});
})();
