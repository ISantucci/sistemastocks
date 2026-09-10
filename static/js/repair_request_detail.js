/* repair_request_detail.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.RRD_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.RRD_DATA || {};

// Limita la selección de seriales por línea a la cantidad pedida.
document.addEventListener("DOMContentLoaded", function () {
  // Cuánto se está entregando en una línea (cantidad o seriales tildados).
  function deliveredFor(line) {
    var qty = document.querySelector('.rr-qty[data-line="' + line + '"]');
    if (qty) {
      var n = parseInt(qty.value, 10);
      return isNaN(n) || n < 0 ? 0 : n;
    }
    return document.querySelectorAll(
      'input[type="checkbox"][data-line="' + line + '"]:not(.rr-pending-flag):checked'
    ).length;
  }

  /* No se puede pedir que devuelvan más de lo que se entregó. El backend ya lo
     rechaza (repair_request_close); esto es para no dejar cargar algo que iba a
     ser rechazado al enviar. */
  function syncReturnMax(line) {
    var input = document.querySelector('.rr-return-qty[data-line="' + line + '"]');
    if (!input) return;
    var hint = document.querySelector('.rr-return-hint[data-line="' + line + '"]');
    var d = deliveredFor(line);
    if (d > 0) {
      input.max = d;
      input.disabled = false;
      input.placeholder = "Igual a la entregada (" + d + ")";
      var v = parseInt(input.value, 10);
      if (!isNaN(v) && v > d) input.value = d;
      if (hint) hint.textContent = "Máximo " + d + " (lo que se entrega en esta línea).";
    } else {
      input.value = "";
      input.removeAttribute("max");
      input.placeholder = "Sin entrega en esta línea";
      if (hint) hint.textContent = "Esta línea no entrega nada: no genera pendiente.";
    }
  }

  document.querySelectorAll('input[type="checkbox"][data-line]:not(.rr-pending-flag)').forEach(function (cb) {
    cb.addEventListener("change", function () {
      var line = cb.getAttribute("data-line");
      var max = parseInt(cb.getAttribute("data-max"), 10) || 0;
      var checked = document.querySelectorAll('input[data-line="' + line + '"]:not(.rr-pending-flag):checked');
      if (checked.length > max) { cb.checked = false; }
      syncReturnMax(line);
    });
  });

  document.querySelectorAll(".rr-qty").forEach(function (q) {
    var apply = function () { syncReturnMax(q.getAttribute("data-line")); };
    q.addEventListener("input", apply);
    q.addEventListener("change", apply);
  });

  document.querySelectorAll(".rr-return-qty").forEach(function (input) {
    var line = input.getAttribute("data-line");
    var clamp = function () {
      var d = deliveredFor(line);
      var v = parseInt(input.value, 10);
      if (!isNaN(v) && d > 0 && v > d) input.value = d;
    };
    input.addEventListener("input", clamp);
    input.addEventListener("change", clamp);
    syncReturnMax(line);
  });
  // El selector de responsable aparece si alguna línea genera pendiente.
  function refreshRespBox() {
    var box = document.getElementById("rr_pending_resp_box");
    if (!box) return;
    var any = Array.prototype.some.call(
      document.querySelectorAll(".rr-pending-flag"),
      function (f) { return f.checked; }
    );
    box.style.display = any ? "block" : "none";
  }

  // Mostrar/ocultar el detalle del pendiente por línea.
  document.querySelectorAll(".rr-pending-flag").forEach(function (flag) {
    flag.addEventListener("change", function () {
      var line = flag.getAttribute("data-line");
      var box = document.querySelector('.rr-pending-box[data-line="' + line + '"]');
      if (box) box.style.display = flag.checked ? "block" : "none";
      refreshRespBox();
    });
  });
  refreshRespBox();
});
})();
