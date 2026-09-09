/* movements.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.MOV_PEND_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.MOV_PEND_DATA || {};

    document.addEventListener("DOMContentLoaded", function () {
        const checkbox = document.getElementById("generate_pending");
        const box = document.getElementById("pending_box");
        function togglePending() {
            box.style.display = checkbox.checked ? "block" : "none";
        }
        checkbox.addEventListener("change", togglePending);
        togglePending();

        // Responsables de la ubicación destino.
        var respMap = D.respMap;
        var toSel = document.getElementById("to_location_id");
        var respSel = document.getElementById("pending_responsible_id");
        var respHint = document.getElementById("pending_responsible_hint");
        function refreshResponsibles() {
            if (!respSel) return;
            var prev = respSel.value;
            var list = respMap[String(toSel ? toSel.value : "")] || [];
            respSel.innerHTML = "";
            if (!list.length) {
                var o = document.createElement("option");
                o.value = ""; o.textContent = "La ubicación destino no tiene responsable";
                respSel.appendChild(o);
                respSel.disabled = true;
                respHint.textContent = "Asignale un responsable a la ubicación antes de generar el pendiente.";
                return;
            }
            respSel.disabled = false;
            if (list.length > 1) {
                var ph = document.createElement("option");
                ph.value = ""; ph.textContent = "Elegí el responsable...";
                respSel.appendChild(ph);
            }
            list.forEach(function (u) {
                var op = document.createElement("option");
                op.value = String(u.id); op.textContent = u.name;
                respSel.appendChild(op);
            });
            if (prev && respSel.querySelector('option[value="' + prev + '"]')) respSel.value = prev;
            respHint.textContent = list.length > 1
                ? "Esta ubicación tiene más de un responsable: elegí a quién se le entrega."
                : "";
        }
        if (toSel) toSel.addEventListener("change", refreshResponsibles);
        refreshResponsibles();

        // Tope de "cantidad a devolver": nunca más de lo que se entrega. El
        // backend ya lo rechaza; esto evita cargar algo que iba a ser rechazado.
        var qtyEl = document.getElementById("qty_input");
        var retEl = document.getElementById("pending_return_qty");
        var retHint = document.getElementById("pending_return_hint");
        function clampReturnQty() {
            if (!retEl || !qtyEl) return;
            var d = parseInt(qtyEl.value, 10);
            if (isNaN(d) || d <= 0) {
                retEl.removeAttribute("max");
                retHint.textContent = "";
                return;
            }
            retEl.setAttribute("max", d);
            retEl.placeholder = "Igual a la entregada (" + d + ")";
            retHint.textContent = "Máximo " + d + ".";
            var v = parseInt(retEl.value, 10);
            if (!isNaN(v) && v > d) retEl.value = d;
        }
        if (qtyEl) {
            qtyEl.addEventListener("input", clampReturnQty);
            qtyEl.addEventListener("change", clampReturnQty);
        }
        if (retEl) {
            retEl.addEventListener("input", clampReturnQty);
            retEl.addEventListener("change", clampReturnQty);
        }
        clampReturnQty();
        // La cantidad también la ajusta el tope de stock más abajo, que escribe
        // el valor sin disparar eventos: se recalcula al enviar, antes del modal.
        var pendForm = checkbox.closest("form");
        if (pendForm) pendForm.addEventListener("submit", clampReturnQty);
    });
})();
