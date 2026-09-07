/* ---------- Errores del formulario: un solo cartel, siempre visible ----------

   Dos cosas que antes no se veían, ahora salen por el mismo popup rojo:

   1. **La validación del navegador en las pantallas de carga múltiple.** El
      <select> del ítem está oculto detrás de TomSelect, y un `required` sobre un
      control oculto hace que Chrome BLOQUEE el envío sin poder mostrar el
      globito: el usuario apretaba "Registrar" y no pasaba absolutamente nada,
      sin ningún mensaje. Por eso acá se apaga la validación nativa
      (`form.noValidate`) y se valida a mano, leyendo igual el motivo real del
      navegador (`validationMessage`), que ya viene traducido.

   2. **Los errores del servidor**, que aparecían como una franja arriba de la
      pantalla y en una pantalla larga quedaban fuera de vista.

   Reglas de las filas repetibles: una fila SIN ítem elegido no existe. No se
   valida, no se envía y no aparece en el resumen de confirmación. Dejar filas
   de más en blanco es lo normal, no un error.

   API pública: window.showFormError("texto")
*/
(function () {
  "use strict";

  var OVERLAY_ID = "modal-form-error";

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  /* Un control cuenta como visible si su contenedor lo está. No se mira el
     input directo: TomSelect oculta el <select> original, así que el select
     "visible" tiene offsetParent null. Mismo criterio que confirm_move.js. */
  function isVisible(node) {
    var box = node.closest(".field, [data-confirm-row]") || node.parentElement;
    if (!box) return true;
    return box.offsetParent !== null;
  }

  function labelFor(node) {
    var field = node.closest(".field");
    var lab = field ? field.querySelector("label") : null;
    if (!lab) lab = node.closest("label");
    var txt = lab ? lab.textContent.trim() : "";
    return txt.replace(/\s+/g, " ").replace(/[:*]\s*$/, "");
  }

  function buildOverlay() {
    var overlay = document.getElementById(OVERLAY_ID);
    if (overlay) return overlay;

    overlay = el("div", "modal-overlay");
    overlay.id = OVERLAY_ID;

    var modal = el("div", "modal");
    var header = el("div", "modal-header");
    var title = el("span", "modal-title", "Error");
    title.style.color = "var(--danger, #b42318)";
    header.appendChild(title);
    var close = el("button", "modal-close", "\u00d7");
    close.type = "button";
    close.setAttribute("aria-label", "Cerrar");
    header.appendChild(close);

    var body = el("div", "modal-body");
    body.id = "form-error-body";

    var footer = el("div", "modal-footer");
    var ok = el("button", "btn btn-primary", "Entendido");
    ok.type = "button";
    footer.appendChild(ok);

    modal.appendChild(header);
    modal.appendChild(body);
    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    function cerrar() { closeModal(OVERLAY_ID); }
    close.addEventListener("click", cerrar);
    ok.addEventListener("click", cerrar);
    return overlay;
  }

  function showFormError(mensaje) {
    var texto = (mensaje || "").trim() || "No se pudo completar la operación.";
    buildOverlay();
    var body = document.getElementById("form-error-body");
    body.textContent = "";
    var p = el("p", null, "Error: " + texto);
    p.style.margin = "0";
    p.style.color = "var(--danger, #b42318)";
    p.style.fontWeight = "600";
    body.appendChild(p);
    openModal(OVERLAY_ID);
  }
  window.showFormError = showFormError;

  /* Una fila repetible sin ítem elegido es una fila que no existe. */
  function filaVacia(fila) {
    var item = fila.querySelector('select[name^="item_id"]');
    return !!item && !item.value;
  }

  /* Devuelve el PRIMER problema en palabras, o null si el formulario está bien. */
  function primerError(form) {
    var filas = [];
    form.querySelectorAll("[data-confirm-row]").forEach(function (f) {
      if (f.offsetParent !== null) filas.push(f);
    });
    if (filas.length) {
      var conDatos = filas.filter(function (f) { return !filaVacia(f); });
      if (!conDatos.length) {
        return "cargá al menos un ítem. Todas las filas están vacías.";
      }
    }

    var nodos = form.querySelectorAll("input, select, textarea");
    for (var i = 0; i < nodos.length; i++) {
      var n = nodos[i];
      if (n.disabled) continue;
      if (n.type === "hidden" || n.type === "submit" || n.type === "button") continue;
      var fila = n.closest("[data-confirm-row]");
      if (fila && filaVacia(fila)) continue;   // fila vacía: no se valida
      if (!isVisible(n)) continue;             // campo que la pantalla oculta
      if (n.checkValidity()) continue;

      var etiqueta = labelFor(n);
      // validationMessage lo escribe el navegador y ya viene en el idioma del usuario.
      var detalle = n.validationMessage || "el valor no es válido";
      return etiqueta ? etiqueta + " \u2014 " + detalle : detalle;
    }
    return null;
  }

  function onSubmit(ev) {
    var form = ev.target;
    if (!form || form.tagName !== "FORM") return;
    if (ev.defaultPrevented) return;              // ya lo frenó otra validación
    if (form.hasAttribute("data-no-error-modal")) return;

    var problema = primerError(form);
    if (problema) {
      ev.preventDefault();
      ev.stopImmediatePropagation();              // no abrir el modal de confirmación
      showFormError(problema);
    }
  }

  /* Se apaga la validación nativa: es la que bloqueaba en silencio. La
     validación real la sigue haciendo el backend; esto solo cambia quién
     muestra el mensaje. */
  function apagarValidacionNativa() {
    document.querySelectorAll("form").forEach(function (f) { f.noValidate = true; });
  }

  /* Los errores que ya mandó el servidor pasan al mismo popup. Los "ok" se
     dejan como estaban: un cartel verde arriba no molesta a nadie. */
  function mostrarErroresDelServidor() {
    var errores = document.querySelectorAll(".flash-stack .flash-error");
    if (!errores.length) return;
    var textos = [];
    errores.forEach(function (n) {
      var t = (n.textContent || "").trim();
      if (t) textos.push(t);
      n.remove();
    });
    var stack = document.querySelector(".flash-stack");
    if (stack && !stack.children.length) stack.remove();
    if (textos.length) showFormError(textos.join(" \u00b7 "));
  }

  document.addEventListener("submit", onSubmit);
  apagarValidacionNativa();   // el script va al final del body: el DOM ya está
  document.addEventListener("DOMContentLoaded", function () {
    apagarValidacionNativa();
    mostrarErroresDelServidor();
  });
  if (document.readyState !== "loading") mostrarErroresDelServidor();
})();
