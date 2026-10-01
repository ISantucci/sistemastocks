/* reparaciones.html - formularios de la mesa de reparaciones.
 *
 * Antes era un <script> inline POR FILA, enganchado por id. Ahora es un solo
 * archivo que trabaja por delegación y por clases dentro de cada
 * <form class="pending-action">, igual que pending_deliveries.js:
 *
 *   - "Descartado" muestra el selector de motivo.
 *   - "Enviar a proveedor" muestra el selector de proveedor.
 *   - En "En proveedor", "Vuelve otro serial (reemplazo)" muestra los campos
 *     para cargarlo. Mientras está "El mismo", esos campos van deshabilitados
 *     y no viajan: el backend reactiva la unidad que se mandó.
 *
 * Solo muestra y oculta. La validación real (motivo, proveedor, seriales,
 * permisos) la hace el backend.
 */
(function () {
  "use strict";

  function sync(form) {
    var act = form.querySelector('input[name="repair_action"]:checked');
    var v = act ? act.value : "";
    var disc = form.querySelector(".js-disc-opts");
    var prov = form.querySelector(".js-prov-opts");
    if (disc) disc.style.display = v === "descartado" ? "block" : "none";
    if (prov) prov.style.display = v === "enviar_proveedor" ? "block" : "none";

    var mode = form.querySelector('input[name="return_mode"]:checked');
    var other = form.querySelector(".js-ret-other");
    if (other) {
      var es = !!(mode && mode.value === "other");
      other.style.display = es ? "" : "none";
      var ins = other.querySelectorAll('input[name="unit_serial"]');
      for (var i = 0; i < ins.length; i++) {
        ins[i].disabled = !es;
        if (es) ins[i].setAttribute("required", "required");
        else ins[i].removeAttribute("required");
      }
    }
  }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || (t.name !== "repair_action" && t.name !== "return_mode")) return;
    var form = t.closest && t.closest("form.pending-action");
    if (form) sync(form);
  });

  function init() {
    var forms = document.querySelectorAll("form.pending-action");
    for (var i = 0; i < forms.length; i++) sync(forms[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
