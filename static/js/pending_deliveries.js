/* pending_deliveries.html - formularios de cierre de pendientes.
 *
 * Antes era un <script> inline POR FILA, que se enganchaba por id. Con dos
 * listados en la pantalla (pendientes con entrega y sin entrega) los ids de
 * fila se repiten entre tablas, así que ahora es un solo archivo que trabaja
 * por delegación y por clases dentro de cada <form class="pending-action">.
 * El comportamiento es el mismo que tenía el inline:
 *
 *   - "Enviar a Descartes" muestra el selector de motivo.
 *   - El origen de la devolución muestra el selector de serial que corresponde:
 *     elegir de la camioneta (stock) y cargar un serial nuevo (campo) son cosas
 *     distintas, y que se vean las dos a la vez confunde.
 *
 * Solo muestra y oculta. La validación real (motivo obligatorio, seriales,
 * permisos) la hace el backend.
 */
(function () {
  "use strict";

  function sync(form) {
    var scrap = form.querySelector('input[name="return_action"][value="scrap"]');
    var opts = form.querySelector(".js-scrap-opts");
    if (opts) opts.style.display = scrap && scrap.checked ? "block" : "none";

    var origin = form.querySelector('input[name="return_origin"]:checked');
    var v = origin ? origin.value : "";
    var st = form.querySelector(".js-ser-stock");
    var cp = form.querySelector(".js-ser-campo");
    if (st) st.style.display = v === "stock" ? "" : "none";
    if (cp) cp.style.display = v === "campo" ? "" : "none";
  }

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || (t.name !== "return_action" && t.name !== "return_origin")) return;
    var form = t.closest && t.closest("form.pending-action");
    if (form) sync(form);
  });

  /* Transferencia de pendientes. Las casillas de las filas pertenecen al
     formulario de arriba (atributo form=). Acá solo se cuenta lo marcado, se
     habilita el botón y se arma el texto del modal de confirmación (que no ve
     las casillas: están fuera del <form>). Quién puede transferir qué lo
     valida el backend. */
  function syncTransfer() {
    var form = document.getElementById("pending-transfer-form");
    if (!form) return;
    var picks = document.querySelectorAll(".js-transfer-pick:checked");
    var n = picks.length;
    var btn = form.querySelector(".js-transfer-submit");
    var cnt = form.querySelector(".js-transfer-count");
    if (btn) btn.disabled = n === 0;
    if (cnt) cnt.textContent = n === 0 ? "Ningún pendiente marcado."
      : (n === 1 ? "1 pendiente marcado." : n + " pendientes marcados.");
    var porItem = {}, orden = [];
    for (var i = 0; i < picks.length; i++) {
      var l = picks[i].getAttribute("data-label") || "pendiente";
      if (!porItem[l]) { porItem[l] = 0; orden.push(l); }
      porItem[l] += 1;
    }
    var partes = orden.map(function (l) { return porItem[l] > 1 ? l + " x" + porItem[l] : l; });
    form.setAttribute("data-confirm-context",
      (n === 1 ? "1 pendiente: " : n + " pendientes: ") + partes.join(", ")
      + " · solo cambia a nombre de quién quedan, no mueve stock");
  }

  document.addEventListener("change", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("js-transfer-pick")) syncTransfer();
  });

  function init() {
    var forms = document.querySelectorAll("form.pending-action");
    for (var i = 0; i < forms.length; i++) sync(forms[i]);
    syncTransfer();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
