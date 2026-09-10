/* remitos.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.REMITOS_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.REMITOS_DATA || {};

  var REMITO_MOVS_URL = D.movsUrl;
  var REMITO_RESP_URL = D.respUrl;

  function openRemitoModal() { openModal('modal-remito-new'); }

  // "Hacia" no ofrece la ubicación elegida en "Desde", igual que en Movimientos
  // y Carga múltiple. El backend ya rechaza la relación consigo misma y el
  // fragmento AJAX ya avisaba; esto la saca directamente del desplegable.
  //
  // Va dentro de DOMContentLoaded a propósito: este <script> inline corre ANTES
  // que los <script src> de base.html (están al final del body), así que acá
  // arriba initFromToExclusion todavía no existe.
  document.addEventListener('DOMContentLoaded', function () {
    initFromToExclusion({
      from: document.getElementById('rm-from'),
      to: document.getElementById('rm-to')
    });
  });

  function onLocChange(which) {
    loadResponsables(which);
    loadRemitoMovs();
  }

  function loadResponsables(which) {
    var locSel = document.getElementById(which === 'from' ? 'rm-from' : 'rm-to');
    var respSel = document.getElementById(which === 'from' ? 'rm-resp-from' : 'rm-resp-to');
    var loc = locSel.value;
    respSel.disabled = false;
    respSel.innerHTML = '<option value="">Cargando...</option>';
    if (!loc) { respSel.innerHTML = '<option value="" disabled selected>Elegí la ubicación...</option>'; return; }
    fetch(REMITO_RESP_URL + '?location_id=' + encodeURIComponent(loc))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.external) {
          respSel.innerHTML = '<option value="">Proveedor — se completa a mano</option>';
          respSel.disabled = true;
          return;
        }
        var opts = data.responsibles || [];
        if (opts.length === 0) {
          respSel.innerHTML = '<option value="" disabled selected>Sin responsables cargados</option>';
          return;
        }
        var html = '';
        if (opts.length > 1) { html += '<option value="" disabled selected>Elegí responsable...</option>'; }
        opts.forEach(function (o) { html += '<option value="' + o.id + '">' + o.name + '</option>'; });
        respSel.innerHTML = html;
        if (opts.length === 1) { respSel.value = String(opts[0].id); }
      })
      .catch(function () { respSel.innerHTML = '<option value="" disabled selected>Error al cargar</option>'; });
  }

  function loadRemitoMovs() {
    var from = document.getElementById('rm-from').value;
    var to = document.getElementById('rm-to').value;
    var dFrom = document.getElementById('rm-date-from').value;
    var dTo = document.getElementById('rm-date-to').value;
    var body = document.getElementById('rm-mov-body');
    var chkAll = document.getElementById('rm-check-all');
    if (chkAll) chkAll.checked = false;
    if (!from || !to) {
      // Sin la relación completa no puede quedar en pantalla la lista anterior:
      // si "Hacia" se limpió (porque pasó a ser igual a "Desde"), esos
      // movimientos ya no corresponden y están tildados. El backend igual los
      // descarta por no pertenecer a la relación, pero no deben verse.
      body.innerHTML = '<tr><td colspan="6" class="muted">Elegí Desde y Hacia para ver los movimientos.</td></tr>';
      return;
    }
    body.innerHTML = '<tr><td colspan="6" class="muted">Cargando...</td></tr>';
    fetch(REMITO_MOVS_URL + '?from_location_id=' + encodeURIComponent(from) + '&to_location_id=' + encodeURIComponent(to)
          + '&date_from=' + encodeURIComponent(dFrom) + '&date_to=' + encodeURIComponent(dTo))
      .then(function (r) { return r.text(); })
      .then(function (html) { body.innerHTML = html; })
      .catch(function () { body.innerHTML = '<tr><td colspan="6" class="muted">No se pudieron cargar los movimientos.</td></tr>'; });
  }

  function toggleAllRemitoMovs(master) {
    document.querySelectorAll('#rm-mov-body input[name="movement_id"]').forEach(function (c) {
      c.checked = master.checked;
    });
  }

  /* Estas funciones se llaman desde atributos onclick="..." del HTML, que se
     resuelven contra el ambito global. Al pasar el codigo a un archivo quedo
     todo dentro de este IIFE, asi que hay que exponerlas a mano: sin esto, los
     botones de la pantalla tiran "no esta definida" y no hacen nada.
     Cuando esos onclick se reemplacen por addEventListener (el paso que falta
     para poder cerrar la CSP), estas lineas se borran. */
  window.openRemitoModal = openRemitoModal;
  window.loadRemitoMovs = loadRemitoMovs;
  window.onLocChange = onLocChange;
  window.toggleAllRemitoMovs = toggleAllRemitoMovs;
})();
