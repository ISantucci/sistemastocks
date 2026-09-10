/* items.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.ITEMS_FORM_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.ITEMS_FORM_DATA || {};

  function updateItemCodePreview() {
    var sel = document.getElementById('item_new_category');
    var out = document.getElementById('item_new_code_preview');
    if (!sel || !out) return;
    var opt = sel.options[sel.selectedIndex];
    var next = opt ? (opt.getAttribute('data-next') || '') : '';
    out.value = next || '(esta categoría no tiene prefijo asignado)';
  }

  // Alta de ítem por AJAX: si falla, el error se muestra dentro del modal
  // sin recargar la página, así no se pierde lo cargado.
  (function () {
    var form = document.getElementById('form-item-new');
    var errBox = document.getElementById('item-new-error');
    if (!form) return;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
      var btn = form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
      fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin'
      }).then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      }).then(function (res) {
        if (res.ok && res.data && res.data.ok) {
          window.location = res.data.redirect || D.itemsUrl;
          return;
        }
        if (btn) btn.disabled = false;
        var msg = (res.data && res.data.error) || 'No se pudo crear el ítem.';
        if (errBox) { errBox.textContent = msg; errBox.style.display = ''; }
      }).catch(function () {
        // Si algo falla con el fetch, caemos al envío tradicional del form.
        if (btn) btn.disabled = false;
        form.submit();
      });
    });
  })();

  /* Estas funciones se llaman desde atributos onclick="..." del HTML, que se
     resuelven contra el ambito global. Al pasar el codigo a un archivo quedo
     todo dentro de este IIFE, asi que hay que exponerlas a mano: sin esto, los
     botones de la pantalla tiran "no esta definida" y no hacen nada.
     Cuando esos onclick se reemplacen por addEventListener (el paso que falta
     para poder cerrar la CSP), estas lineas se borran. */
  window.updateItemCodePreview = updateItemCodePreview;
})();
