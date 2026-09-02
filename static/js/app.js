// Interacción mínima: colapso de sidebar (desktop), drawer mobile, menú de usuario.
document.addEventListener('DOMContentLoaded', function () {
  var shell = document.querySelector('.app-shell');
  var sidebar = document.getElementById('sidebar');
  var overlay = document.getElementById('sidebarOverlay');
  var collapseBtn = document.getElementById('sidebarCollapseBtn');
  var menuBtn = document.getElementById('topbarMenuBtn');
  var userBtn = document.getElementById('userMenuBtn');
  var userDropdown = document.getElementById('userMenuDropdown');

  if (collapseBtn && shell) {
    if (localStorage.getItem('tngstocks.sidebarCollapsed') === '1') {
      shell.classList.add('sidebar-collapsed');
    }
    collapseBtn.addEventListener('click', function () {
      shell.classList.toggle('sidebar-collapsed');
      localStorage.setItem('tngstocks.sidebarCollapsed', shell.classList.contains('sidebar-collapsed') ? '1' : '0');
    });
  }

  function closeMobileNav() {
    sidebar.classList.remove('mobile-open');
    overlay.classList.remove('open');
  }
  if (menuBtn && sidebar && overlay) {
    menuBtn.addEventListener('click', function () {
      sidebar.classList.add('mobile-open');
      overlay.classList.add('open');
    });
    overlay.addEventListener('click', closeMobileNav);
  }

  if (userBtn && userDropdown) {
    userBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      userDropdown.classList.toggle('open');
    });
    document.addEventListener('click', function () {
      userDropdown.classList.remove('open');
    });
  }
});

/* ---------- Modal genérico ---------- */
function openModal(id) {
  document.querySelectorAll('.modal-overlay.open').forEach(function (m) { m.classList.remove('open'); });
  var overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.classList.add('open');
  document.body.classList.add('modal-open');
  var focusable = overlay.querySelector('.modal-body input, .modal-body select, .modal-body textarea');
  if (focusable) { setTimeout(function () { focusable.focus(); }, 0); }
}
function closeModal(id) {
  var overlay = document.getElementById(id);
  if (!overlay) return;
  overlay.classList.remove('open');
  if (!document.querySelector('.modal-overlay.open')) {
    document.body.classList.remove('modal-open');
  }
}
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    var open = document.querySelector('.modal-overlay.open');
    if (open) { closeModal(open.id); }
  }
});
/* Cierre por click en el fondo.
   El evento 'click' del DOM se dispara en el ANCESTRO COMUN de mousedown y
   mouseup. Si el usuario empieza a seleccionar texto DENTRO del modal y suelta
   el boton afuera, ese ancestro comun termina siendo el .modal-overlay y el
   modal se cerraba, perdiendo lo cargado. Para evitarlo se exige que el gesto
   EMPIECE y TERMINE sobre el overlay. */
(function () {
  var pressStartedOnOverlay = null;

  function isOpenOverlay(el) {
    return !!(el && el.classList &&
      el.classList.contains('modal-overlay') &&
      el.classList.contains('open') &&
      // El modal global de detalle (base.html) tiene su propio cierre, que
      // ademas limpia el iframe y sincroniza el historial. No lo tocamos.
      !el.classList.contains('detail-overlay'));
  }

  document.addEventListener('mousedown', function (e) {
    // Solo boton principal; ignoramos click derecho / rueda.
    pressStartedOnOverlay = (e.button === 0 && isOpenOverlay(e.target)) ? e.target : null;
  }, true);

  document.addEventListener('mouseup', function (e) {
    var started = pressStartedOnOverlay;
    pressStartedOnOverlay = null;
    if (e.button !== 0) return;
    if (started && started === e.target && isOpenOverlay(e.target)) {
      closeModal(e.target.id);
    }
  }, true);
})();

/* ---------- Selects buscables (select.js-buscar) ---------- */
window.TS_SINGLE_OPTS = {
  create: false,
  allowEmptyOption: true,
  sortField: { field: 'text', direction: 'asc' },
  placeholder: 'Buscar...',
  closeAfterSelect: true,
  onItemAdd: function () { this.setTextboxValue(''); this.blur(); },
  onChange: function () { this.blur(); }
};
/* Multi-seleccion buscable (select.js-multi-prov, multiple). A diferencia del
   single, NO cierra ni pierde el foco al elegir, para poder cargar varios
   seguidos. */
window.TS_MULTI_OPTS = {
  create: false,
  plugins: ['remove_button'],
  sortField: { field: 'text', direction: 'asc' },
  placeholder: 'Elegí uno o varios...',
  closeAfterSelect: false,
  hideSelected: true
};
/* Selects REMOTOS (select.js-buscar-remoto).
   Se activan solos cuando el catálogo de ítems supera el umbral del backend
   (ITEM_PICKER_MAX_INLINE). En vez de volcar miles de <option> en el HTML,
   piden resultados a data-src a medida que se tipea. Si el select NO tiene la
   clase, se comporta exactamente como antes: no hay cambio de UX para los
   catálogos chicos, que es el caso actual. */
window.TS_REMOTE_OPTS = function (el) {
  var src = el.getAttribute('data-src');
  var valueField = el.getAttribute('data-value-field') || 'code';
  return Object.assign({}, window.TS_SINGLE_OPTS, {
    valueField: valueField,
    labelField: 'label',
    searchField: ['code', 'name'],
    preload: 'focus',
    loadThrottle: 250,
    load: function (query, callback) {
      fetch(src + '?q=' + encodeURIComponent(query), {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' }
      })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (rows) { callback(rows || []); })
        .catch(function () { callback(); });
    },
    // Sin resultados cargados todavía, no reordenar por texto: el backend ya
    // devuelve ordenado por código.
    sortField: null
  });
};

/* Buscador con TEXTO LIBRE (select.js-buscar-libre).
   Mantiene el selector de siempre —misma estética— pero deja de obligar a
   elegir una opción: escribiendo "NEO" el desplegable ofrece los ítems que
   coinciden y, además, una entrada «Buscar NEO» que manda esa palabra como
   filtro de texto y trae todos los que coincidan.
   `createOnBlur` es lo que evita el caso molesto: escribir la palabra y tocar
   "Filtrar" sin haber apretado Enter. */
window.TS_FREE_OPTS = {
  create: function (input) { return { value: input, text: input }; },
  createOnBlur: true,
  persist: false,
  /* Con la lista desplegada, TomSelect resalta por defecto el PRIMER ítem, no
     la opción de búsqueda: escribir "NEO" y apretar Enter terminaba eligiendo
     NEO-001, que es justo lo que molestaba. Con addPrecedence el Enter manda
     la palabra como filtro de texto; para elegir un ítem puntual se lo clickea
     (o se baja con las flechas). */
  addPrecedence: true,
  render: {
    option_create: function (data, escape) {
      return '<div class="create">Buscar <strong>' + escape(data.input) + '</strong>' +
             '<div class="muted" style="font-size:12px;">todos los que coincidan</div></div>';
    }
  }
};

document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('select.js-buscar').forEach(function (el) {
    if (el.tomselect) return;
    var opts;
    if (el.classList.contains('js-buscar-remoto') && el.getAttribute('data-src')) {
      opts = window.TS_REMOTE_OPTS(el);
    } else {
      opts = Object.assign({}, window.TS_SINGLE_OPTS);
    }
    if (el.classList.contains('js-buscar-libre')) {
      opts = Object.assign({}, opts, window.TS_FREE_OPTS);
    }
    new TomSelect(el, opts);
  });
  document.querySelectorAll('select.js-multi-prov').forEach(function (el) {
    if (el.tomselect) return;
    new TomSelect(el, window.TS_MULTI_OPTS);
  });
});

/* ---------- Volver a la vista filtrada después de guardar ----------
   Casi todas las pantallas de listado terminan sus POST con un redirect al
   listado "pelado": sin filtros, sin orden y en la página 1. Acá se le agrega
   a cada formulario POST de una pantalla CON filtros la URL vigente, y el
   backend la repone al redirigir (ver `_volver_a_la_vista_filtrada` en app.py).

   Se activa solo si la pantalla tiene formulario de filtros y la URL ya trae
   alguno: sin filtros vigentes no hay nada que conservar. Degrada abierto —
   si el JS no carga, todo se comporta exactamente como antes. */
document.addEventListener('DOMContentLoaded', function () {
  if (!window.location.search) return;
  if (!document.querySelector('form.filters-grid, form.filters-row')) return;
  var actual = window.location.pathname + window.location.search;
  document.querySelectorAll('form').forEach(function (f) {
    if ((f.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
    if (f.querySelector('input[name="_filtros"]')) return;  // ya lo pone el template
    var i = document.createElement('input');
    i.type = 'hidden';
    i.name = '_filtros';
    i.value = actual;
    f.appendChild(i);
  });
});

/* ---------- Filtros colapsables (details.filters-collapse) ----------
   En desktop (>=768px) los filtros siempre se muestran abiertos; en mobile
   quedan colapsados detrás del botón "Filtros". Genérico: aplica a cualquier
   pantalla que use .filters-collapse, sin necesidad de script por template.
   Es idempotente, así que convive con los scripts inline que ya hacen esto. */
document.addEventListener('DOMContentLoaded', function () {
  var blocks = document.querySelectorAll('details.filters-collapse');
  if (!blocks.length) return;
  function syncFilters() {
    if (window.matchMedia('(min-width:768px)').matches) {
      blocks.forEach(function (el) { el.open = true; });
    }
  }
  syncFilters();
  window.addEventListener('resize', syncFilters);
});

/* ---------- Mostrar/ocultar contraseña ---------- */
function togglePw(inputId, btn) {
  var el = document.getElementById(inputId);
  if (!el) return;
  el.type = (el.type === 'password') ? 'text' : 'password';
  if (btn) btn.style.color = (el.type === 'text') ? 'var(--accent)' : 'var(--text-muted)';
}
