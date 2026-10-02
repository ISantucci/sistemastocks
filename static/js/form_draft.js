/* ---------- Borrador: que un rechazo del servidor no borre lo cargado ----------

   El problema (reportado 2026-10-01): cuando el servidor rechaza un envío
   (falta un dato, el stock cambió en el medio, un serial ya no está) responde
   con un aviso y vuelve a abrir la pantalla VACÍA. En Carga múltiple un
   compañero perdió todo lo que había cargado por un solo dato, y no entendió
   por qué.

   Qué hace: en los formularios marcados con data-draft, al enviar se guarda
   una copia de lo cargado en la sesión de ESA pestaña (sessionStorage). Si la
   pantalla vuelve con un error del servidor, se repone todo y el cartel de
   error lo avisa. Si vuelve bien, la copia se descarta.

   Qué NO hace, a propósito:
   - No toca el servidor ni la base: el backend sigue validando todo igual y
     sigue siendo todo-o-nada. Esto solo cambia qué ve el usuario al volver.
   - No repone si el envío salió bien (solo con un error en pantalla):
     reponer un formulario ya guardado invita a mandarlo dos veces.
   - No viaja a otra pestaña ni a otra compu, y se descarta en la siguiente
     carga de la página aunque no se use. No guarda contraseñas ni archivos.
   - Si el navegador no deja usar sessionStorage, la pantalla anda igual que
     antes (sin borrador).

   Uso en el template:
     <form data-draft="carga-multiple" data-draft-add="#add-line-btn">
       data-draft      nombre del borrador. Dos formularios con el mismo nombre
                       comparten borrador: lo guardado en uno se repone en el
                       otro por name (lo usa Conteo: se confirma en un form y,
                       si falla, se vuelve al de carga).
       data-draft-add  (opcional) botón que agrega una fila: se agregan las
                       filas que hagan falta antes de reponerlas.
   Filas: los [data-confirm-row] del formulario (el mismo marcado que ya usan
   confirm_move.js y form_errors.js) o, donde ese marcado no corresponde, los
   [data-draft-row].
   Ocultos: no se guardan, salvo los marcados con data-draft-keep (los que
   guardan algo que eligió el usuario, como los seriales de Ingresos/Egresos).
   Seriales de los selectores: _snGet/_snRestore (serial_picker.js).
   Si el formulario vive en un modal o en un <details> cerrado, se abre.
*/
(function () {
  "use strict";

  var PREFIX = "tng.borrador:";
  var MAX_EDAD_MS = 30 * 60 * 1000;   // un borrador más viejo no se repone

  // Hay que mirarlo AHORA, mientras se arma la página: form_errors.js saca los
  // .flash-error del DOM en DOMContentLoaded para mostrarlos en su popup (y
  // deja la lista en window.TNG_SERVER_ERRORS, que se mira también).
  var huboErrorAlCargar = !!document.querySelector(".flash-stack .flash-error");

  function storage() {
    try {
      var s = window.sessionStorage;
      s.getItem(PREFIX);          // con datos de sitio bloqueados, esto tira
      return s;
    } catch (e) { return null; }
  }

  function clave(form) {
    return PREFIX + window.location.pathname + "#" + form.getAttribute("data-draft");
  }

  var FILA = "[data-confirm-row], [data-draft-row]";

  function esFila(el) { return !!el.closest(FILA); }

  /* Qué se guarda: lo que tipea o elige el usuario. Los ocultos no (los arma
     la pantalla, y los seriales elegidos van por su gancho), ni los resúmenes
     de solo lectura, ni contraseñas ni archivos. */
  function guardable(el) {
    if (!el.name || el.name === "csrf_token") return false;
    var t = (el.type || "").toLowerCase();
    if (t === "hidden") return el.hasAttribute("data-draft-keep");
    if (t === "submit" || t === "button" || t === "reset" ||
        t === "file" || t === "password" || t === "image") return false;
    if (el.readOnly && el.tagName !== "SELECT") return false;
    if (el.hasAttribute("data-draft-skip")) return false;
    return true;
  }

  function controles(scope, dentroDeFila) {
    return Array.prototype.filter.call(
      scope.querySelectorAll("input, select, textarea"),
      function (el) { return guardable(el) && (dentroDeFila || !esFila(el)); }
    );
  }

  function ganchosSerial(scope, dentroDeFila) {
    return Array.prototype.filter.call(
      scope.querySelectorAll("[data-sn-hook]"),
      function (b) { return dentroDeFila || !esFila(b); }
    );
  }

  function leer(el) {
    var t = (el.type || "").toLowerCase();
    if (t === "checkbox" || t === "radio") return el.checked;
    if (el.tomselect) return el.tomselect.getValue();
    if (el.tagName === "SELECT" && el.multiple) {
      return Array.prototype.filter.call(el.options, function (o) { return o.selected; })
        .map(function (o) { return o.value; });
    }
    return el.value;
  }

  function esMarcable(el) {
    var t = (el.type || "").toLowerCase();
    return t === "checkbox" || t === "radio";
  }

  /* Selectores con búsqueda remota (TomSelect con `load`, ej. el conteo de
     camioneta): sus opciones llegan del servidor al tipear, así que al volver
     no están. Se guarda la opción elegida para poder volver a agregarla. En
     los demás NO: si una opción ya no se ofrece (un ítem sin stock en el
     origen) es a propósito, y no hay que meterla por la ventana. */
  function opcionesRemotas(el) {
    var ts = el.tomselect;
    if (!ts || !ts.settings || !ts.settings.load) return null;
    var vals = ts.getValue();
    vals = Array.isArray(vals) ? vals : (vals ? [vals] : []);
    var out = {};
    vals.forEach(function (x) {
      var o = ts.options[x], copia = {};
      if (!o) return;
      Object.keys(o).forEach(function (k) {
        var v = o[k];
        if (k.charAt(0) === "$") return;
        if (v === null || ["string", "number", "boolean"].indexOf(typeof v) !== -1) copia[k] = v;
      });
      out[x] = copia;
    });
    return out;
  }

  function foto(scope, dentroDeFila) {
    return {
      c: controles(scope, dentroDeFila).map(function (el) {
        var g = { n: el.name, r: esMarcable(el) ? el.value : null, v: leer(el) };
        var o = opcionesRemotas(el);
        if (o) g.o = o;
        return g;
      }),
      s: ganchosSerial(scope, dentroDeFila).map(function (b) {
        return b._snGet ? b._snGet() : [];
      })
    };
  }

  function filas(form) {
    return Array.prototype.slice.call(form.querySelectorAll(FILA));
  }

  function filaVacia(fila) {
    var item = fila.querySelector('select[name^="item_id"]');
    return !!item && !item.value;
  }

  /* Se guarda en CAPTURA, antes que cualquier validación de la pantalla: si el
     envío se frena en el navegador no pasa nada (el borrador se descarta en la
     próxima carga), y así también queda guardado cuando el envío sale por
     form.submit(), que no dispara este evento. */
  function guardar(ev) {
    var form = ev.target;
    if (!form || form.tagName !== "FORM" || !form.hasAttribute("data-draft")) return;
    var st = storage();
    if (!st) return;
    var dinamico = form.hasAttribute("data-draft-add");
    var data = {
      t: Date.now(),
      cab: foto(form, false),
      // Las filas en blanco de las pantallas con "+ Agregar" no se guardan:
      // dejar filas de más sin usar es lo normal.
      filas: filas(form)
        .filter(function (f) { return !(dinamico && filaVacia(f)); })
        .map(function (f) { return foto(f, true); })
    };
    try { st.setItem(clave(form), JSON.stringify(data)); } catch (e) { /* sin lugar: sin borrador */ }
  }

  function disparar(el) {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function igual(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function tieneValor(v) {
    if (Array.isArray(v)) return v.length > 0;
    return v !== "" && v !== false && v != null;
  }

  /* Pone el valor y avisa a la pantalla (eventos input/change, o el onChange
     de TomSelect), igual que si lo hubiera hecho el usuario. Si el valor ya
     está, no hace nada: volver a disparar "change" en Desde, por ejemplo,
     limpia las filas. Devuelve false si el valor ya no se puede poner (un ítem
     que la pantalla dejó de ofrecer porque no queda stock en el origen). */
  function poner(el, v, opciones) {
    if (esMarcable(el)) {
      if (el.checked !== !!v) { el.checked = !!v; disparar(el); }
      return true;
    }
    if (el.tomselect) {
      var ts = el.tomselect;
      if (igual(ts.getValue(), v)) return true;
      var lista = Array.isArray(v) ? v : (tieneValor(v) ? [String(v)] : []);
      if (!lista.length) { ts.clear(); return true; }
      if (opciones && ts.settings.load) {
        lista.forEach(function (x) {
          if (!Object.prototype.hasOwnProperty.call(ts.options, x) && opciones[x]) {
            ts.addOption(opciones[x]);
          }
        });
      }
      var hay = lista.filter(function (x) {
        return Object.prototype.hasOwnProperty.call(ts.options, x);
      });
      if (!hay.length) return false;
      ts.setValue(Array.isArray(v) ? hay : hay[0]);
      return hay.length === lista.length;
    }
    if (el.tagName === "SELECT") {
      if (el.multiple) {
        var quiero = {};
        (v || []).forEach(function (x) { quiero[x] = true; });
        Array.prototype.forEach.call(el.options, function (o) { o.selected = !!quiero[o.value]; });
        disparar(el);
        return true;
      }
      if (el.value === v) return true;
      var existe = Array.prototype.some.call(el.options, function (o) {
        return o.value === v && !o.disabled;
      });
      if (!existe) return false;
      el.value = v;
      disparar(el);
      return true;
    }
    if (el.value === v) return true;
    el.value = v;
    disparar(el);
    return true;
  }

  /* Repone una foto sobre `scope` (el formulario o una fila). Cada valor va al
     N-ésimo control con ese name; casillas y radios, al de ese mismo value.
     Devuelve cuántos datos con contenido no se pudieron reponer. */
  function reponer(scope, f, dentroDeFila, conSeriales) {
    var perdidos = 0;
    var vistos = {};
    (f.c || []).forEach(function (g) {
      var k = g.r != null ? g.n + "\u0000" + g.r : g.n;
      vistos[k] = (vistos[k] || 0) + 1;
      // Se vuelve a buscar cada vez: reponer un valor puede cambiar la pantalla.
      var candidatos = controles(scope, dentroDeFila).filter(function (el) {
        return el.name === g.n && (g.r == null || el.value === g.r);
      });
      var el = candidatos[vistos[k] - 1];
      if (!el) { if (tieneValor(g.v)) perdidos++; return; }
      if (!poner(el, g.v, g.o) && tieneValor(g.v)) perdidos++;
    });
    if (conSeriales) {
      var ganchos = ganchosSerial(scope, dentroDeFila);
      (f.s || []).forEach(function (ids, i) {
        if (!ids || !ids.length) return;
        var b = ganchos[i];
        if (b && b._snRestore) b._snRestore(ids);
        else perdidos++;
      });
    }
    return perdidos;
  }

  /* Si el formulario está en un modal o en un <details> cerrado, se abre: si
     no, lo repuesto queda escondido y parece que se perdió. No se usa
     openModal(): cierra los demás modales, y el cartel de error tiene que
     seguir abierto. Ese cartel se armó antes, más abajo en el DOM, así que
     queda arriba; al cerrarlo se ve el formulario (closeModal ya maneja dos
     modales abiertos). */
  function mostrar(form) {
    var det = form.closest("details");
    if (det && !det.open) det.open = true;
    var modal = form.closest(".modal-overlay");
    if (modal && !modal.classList.contains("open")) {
      modal.classList.add("open");
      document.body.classList.add("modal-open");
    }
  }

  function restaurar(form, data) {
    // Lo que el usuario ya confirmó al cargar (por ejemplo "ese serial está en
    // otra camioneta, ¿seguro?") no se le vuelve a preguntar al reponerlo. Solo
    // mientras se repone: se devuelve en cuanto corren los avisos pendientes.
    var confirmar = window.confirm;
    window.confirm = function () { return true; };
    try {
      return restaurarCampos(form, data);
    } finally {
      setTimeout(function () { window.confirm = confirmar; }, 0);
    }
  }

  function restaurarCampos(form, data) {
    // 1) Cabecera (Desde, Hacia...): de ella dependen qué ítems ofrece cada fila.
    reponer(form, data.cab, false, false);

    // 2) Filas: se agregan las que falten y se repone cada una en orden.
    var perdidos = 0;
    var sel = form.getAttribute("data-draft-add");
    var boton = sel ? document.querySelector(sel) : null;
    var guardas = 0;
    while (boton && filas(form).length < data.filas.length && guardas++ < 500) {
      boton.click();
    }
    var actuales = filas(form);
    data.filas.forEach(function (f, i) {
      if (!actuales[i]) { perdidos++; return; }
      perdidos += reponer(actuales[i], f, true, true);
    });

    // 3) Cabecera otra vez: lo que depende de las filas (por ejemplo "a nombre
    //    de quién" aparece cuando una fila pasa a "Pendiente: Sí") y los
    //    seriales del movimiento simple. Lo que ya está, no se toca.
    perdidos += reponer(form, data.cab, false, true);
    return perdidos;
  }

  function avisar(perdidos) {
    var texto;
    if (perdidos < 0) {
      texto = "Intentamos recuperar lo que estabas cargando, pero no se pudo " +
              "completo. Revisá el formulario antes de volver a guardar.";
    } else {
      texto = "Recuperamos lo que estabas cargando: corregí lo que dice arriba " +
              "y volvé a guardar.";
      if (perdidos > 0) {
        texto += " Ojo: " + perdidos + (perdidos === 1 ? " dato no se pudo" : " datos no se pudieron") +
                 " reponer (por ejemplo, un ítem que ya no tiene stock en el origen). " +
                 "Revisalo antes de guardar.";
      }
    }
    var body = document.getElementById("form-error-body");
    if (body) {
      var p = document.createElement("p");
      p.className = "form-draft-aviso";
      p.style.margin = "10px 0 0";
      p.textContent = texto;
      body.appendChild(p);
    } else if (window.showFormError) {
      window.showFormError(texto);
    }
  }

  function alCargar() {
    var st = storage();
    if (!st) return;
    var huboError = huboErrorAlCargar ||
      !!(window.TNG_SERVER_ERRORS && window.TNG_SERVER_ERRORS.length);
    document.querySelectorAll("form[data-draft]").forEach(function (form) {
      var k = clave(form), raw = null;
      try { raw = st.getItem(k); st.removeItem(k); } catch (e) { return; }
      if (!raw || !huboError) return;      // salió bien, o es otra visita: se descarta
      var data = null;
      try { data = JSON.parse(raw); } catch (e) { return; }
      if (!data || !data.t || Date.now() - data.t > MAX_EDAD_MS) return;
      var perdidos;
      try { perdidos = restaurar(form, data); } catch (e) { perdidos = -1; }
      mostrar(form);
      avisar(perdidos);
    });
  }

  document.addEventListener("submit", guardar, true);

  // En "load" y no en DOMContentLoaded: cada pantalla arma sus filas y sus
  // selectores en DOMContentLoaded, y hay que reponer DESPUÉS de eso.
  if (document.readyState === "complete") setTimeout(alCargar, 0);
  else window.addEventListener("load", function () { setTimeout(alCargar, 0); });

  // Para los tests y para depurar a mano desde la consola.
  window.TNG_FORM_DRAFT = { guardar: guardar, restaurar: restaurar, alCargar: alCargar };
})();
