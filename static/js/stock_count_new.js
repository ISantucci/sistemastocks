/* stock_count_new.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.SC_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.SC_DATA || {};

(function () {
  var SERIAL_SRC = D.serialSrc;
  var ITEM_SRC = D.itemSrc;
  var LOCATION_ID = D.locationId;

  // unit_id -> { serial, location, ajeno }. Se llena con lo precargado en el
  // HTML y con lo que devuelve la busqueda remota, para saber si hay que
  // preguntar antes de aceptar un serial que hoy figura en otra camioneta.
  var serialMeta = D.serialMeta || {};

  function serialOpts(itemId) {
    return {
      plugins: ['remove_button'],
      valueField: 'unit_id',
      labelField: 'label',
      searchField: ['serial'],
      create: false,
      persist: false,
      loadThrottle: 250,
      placeholder: 'Elegí los números de serie...',
      load: function (query, callback) {
        fetch(SERIAL_SRC + '?item_id=' + encodeURIComponent(itemId) +
              '&location_id=' + encodeURIComponent(LOCATION_ID) +
              '&q=' + encodeURIComponent(query),
              { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.ok ? r.json() : []; })
          .then(function (rows) {
            (rows || []).forEach(function (r) { serialMeta[String(r.unit_id)] = r; });
            callback(rows || []);
          })
          .catch(function () { callback(); });
      },
      onItemAdd: function (value) {
        // Un serial que hoy figura en la camioneta de otro técnico se puede
        // declarar —es justo la inconsistencia que el conteo busca— pero no en
        // silencio: si fue un error de tipeo, acá se corta.
        var meta = serialMeta[String(value)];
        var self = this;
        if (!meta || !meta.ajeno) return;
        setTimeout(function () {
          var ok = window.confirm(
            'Ese serial se encuentra en "' + meta.location + '". ¿Estás seguro?');
          if (!ok) { self.removeItem(value, true); }
          self.refreshOptions(false);
        }, 0);
      }
    };
  }

  function initSerial(el, itemId) {
    if (!el || el.tomselect || !window.TomSelect) return;
    new TomSelect(el, serialOpts(itemId));
  }

  document.addEventListener("DOMContentLoaded", function () {
    // El globito nativo de Chrome dice "Completa este campo", que en una pantalla
    // llena de renglones no le dice nada a nadie. Se reemplaza SOLO el texto: la
    // validacion sigue siendo la del navegador, y el backend valida igual.
    document.querySelectorAll('input[name^="contado_"]').forEach(function (el) {
      el.addEventListener("invalid", function () {
        this.setCustomValidity(this.validity.valueMissing ? "Completá el conteo" : "");
      });
      el.addEventListener("input", function () { this.setCustomValidity(""); });
    });

    document.querySelectorAll('select.js-sc-serial').forEach(function (el) {
      initSerial(el, el.getAttribute('data-item-id'));
    });

    var tpl = document.getElementById("sc-extra-template");
    var cont = document.getElementById("sc-extra-lines");

    function wireLine(line) {
      var rm = line.querySelector(".sc-remove-line-btn");
      if (rm) rm.addEventListener("click", function () { line.remove(); });

      var sel = line.querySelector('select[name="extra_item_id[]"]');
      if (!sel || !window.TomSelect || sel.tomselect) return;
      var ts = new TomSelect(sel, Object.assign({}, window.TS_SINGLE_OPTS, {
        valueField: 'id',
        labelField: 'label',
        searchField: ['code', 'name'],
        preload: 'focus',
        loadThrottle: 250,
        sortField: null,
        load: function (query, callback) {
          fetch(ITEM_SRC + '?q=' + encodeURIComponent(query),
                { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (rows) { callback(rows || []); })
            .catch(function () { callback(); });
        },
        onChange: function (value) {
          var data = this.options[value] || {};
          var qtyBox = line.querySelector('.sc-extra-qty');
          var serBox = line.querySelector('.sc-extra-serial');
          var serSel = line.querySelector('select.js-sc-serial-extra');
          var qtyInput = line.querySelector('input[name="extra_qty[]"]');
          if (data.serialized) {
            // Un serializado se cuenta por serial, no por cantidad. El campo de
            // cantidad se oculta pero SIGUE enviándose: el backend alinea
            // extra_item_id[] con extra_qty[] por posición.
            qtyBox.style.display = 'none';
            if (qtyInput) qtyInput.value = '0';
            serBox.style.display = '';
            if (serSel) {
              if (serSel.tomselect) { serSel.tomselect.destroy(); }
              serSel.name = 'serial_unit_ids_' + value;
              initSerial(serSel, value);
            }
          } else {
            qtyBox.style.display = '';
            if (qtyInput && qtyInput.value === '0') qtyInput.value = '1';
            serBox.style.display = 'none';
            if (serSel) { serSel.name = ''; }
          }
        }
      }));
      if (ts) { /* referencia para que no la recolecte el GC */ }
    }

    document.getElementById("sc-add-line-btn").addEventListener("click", function () {
      cont.appendChild(tpl.content.cloneNode(true));
      wireLine(cont.lastElementChild);
    });
  });
})();
})();
