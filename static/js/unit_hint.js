/* Aclara la unidad de medida al lado del campo de cantidad.
 *
 * El problema
 * -----------
 * Un ítem puede medirse en unidades o en metros (Item.unit). Al LEER el stock
 * eso ya se mostraba, porque las tablas usan fmt_qty(). Pero al CARGAR o
 * CONSUMIR no aparecía en ningún lado: el selector decía "CAB-001 - Cable UTP"
 * y el campo de al lado decía "Cantidad", igual para un cable que para una
 * cámara. Quien cargaba 300 metros escribía "300" en un campo que no le decía
 * qué estaba contando, y después eso se leía como 300 unidades.
 *
 * Cómo funciona
 * -------------
 * El servidor manda en window.TNG_ITEMS_METROS los ids de los ítems que se
 * miden en metros (son pocos: cables). Cada cartel se declara en el HTML con
 * data-unit-hint="<name del select de ítem>", y este archivo lo completa cuando
 * ese select cambia.
 *
 * Por qué por id y no leyendo el <option>: el selector tiene dos modos. Con
 * catálogo chico las opciones las pinta Jinja; pasado ITEM_PICKER_MAX_INLINE
 * las trae /api/items/search y las crea el navegador. Además movements.html
 * repuebla el select entero cada vez que cambia la ubicación de origen.
 * Resolviendo por id funciona igual en los tres casos.
 *
 * Por qué delegación de eventos: ingresos/egresos y carga múltiple clonan filas
 * desde un <template>. Con delegación, una fila recién agregada anda sin tener
 * que registrarle nada.
 *
 * IMPORTANTE: esto sólo escribe texto en un <span> aparte. No toca el value de
 * ningún input ni el texto de ningún <option>, justamente porque hay JS que
 * parsea esos valores para los topes de cantidad y los seriales (serial_picker,
 * line_dedupe, confirm_move). Meter "metros" ahí adentro rompería esos topes.
 */
(function () {
  "use strict";

  var METROS = null;

  function esMetros(valor) {
    if (METROS === null) {
      METROS = {};
      var ids = window.TNG_ITEMS_METROS || [];
      for (var i = 0; i < ids.length; i++) METROS[String(ids[i])] = true;
    }
    return !!(valor && METROS[String(valor)]);
  }

  /* El contenedor donde buscar el select: la fila si el formulario tiene filas
     repetidas, y si no el formulario. closest() devuelve el ancestro MÁS
     CERCANO de la lista, así que en una fila gana la fila y nunca se lee el
     ítem de la fila de al lado. */
  var SCOPE = ".io-line, .bulk-line, .usage-line, .rr-line, .sc-extra-line, tr, form";

  function scopeDe(el) {
    return (el.closest && el.closest(SCOPE)) || document;
  }

  /* data-unit-hint puede listar varios names separados por coma: gana el primero
     que tenga algo elegido. Sirve para "cantidad a devolver", donde el select de
     ítem puede estar en "Devuelve el mismo ítem" (vacío) y entonces la unidad es
     la del ítem principal del movimiento. */
  function valorDeItem(hint) {
    var scope = scopeDe(hint);
    var names = (hint.getAttribute("data-unit-hint") || "").split(",");
    for (var i = 0; i < names.length; i++) {
      var name = names[i].trim();
      if (!name) continue;
      var campo = scope.querySelector('[name="' + name + '"]');
      if (campo && campo.value) return campo.value;
    }
    return "";
  }

  /* data-unit-default es para los formularios donde el item ya lo fijo el
     servidor y el select es solo para cambiarlo: si no hay nada elegido, vale
     la unidad del item de la linea. Lo usa la entrega de repuestos, donde
     "Devuelve el mismo item" deja el select vacio. */
  function refrescar(hint) {
    var valor = valorDeItem(hint);
    var metros = valor
      ? esMetros(valor)
      : hint.getAttribute("data-unit-default") === "metros";
    hint.textContent = metros ? "(en metros)" : "";
  }

  function refrescarScope(el) {
    var scope = scopeDe(el);
    var hints = scope.querySelectorAll("[data-unit-hint]");
    for (var i = 0; i < hints.length; i++) refrescar(hints[i]);
  }

  function refrescarTodo() {
    var hints = document.querySelectorAll("[data-unit-hint]");
    for (var i = 0; i < hints.length; i++) refrescar(hints[i]);
  }

  /* 'change' burbujea, así que un solo listener alcanza para toda la página,
     incluidas las filas que todavía no existen. Tom Select sincroniza el
     <select> original y dispara 'change' sobre él, que es de lo que ya dependen
     serial_picker.js y los topes de cantidad. */
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || !t.name) return;
    if (t.tagName !== "SELECT" && t.tagName !== "INPUT") return;
    if (t.name.indexOf("item_id") === -1) return;
    refrescarScope(t);
  });

  /* Al agregar una fila el cartel arranca vacío, que es lo correcto: todavía no
     hay ítem elegido. Sólo hace falta repasar lo que ya viene con valor puesto
     (una pantalla que vuelve con datos, o un select de una sola opción). */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refrescarTodo);
  } else {
    refrescarTodo();
  }

  /* Por si algún formulario repuebla su select por JS sin disparar 'change'
     (movements.html lo hace al cambiar la ubicación de origen). */
  window.refrescarUnidades = refrescarTodo;
})();
