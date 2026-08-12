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
document.addEventListener('click', function (e) {
  if (e.target.classList && e.target.classList.contains('modal-overlay') && e.target.classList.contains('open')) {
    closeModal(e.target.id);
  }
});

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
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('select.js-buscar').forEach(function (el) {
    if (el.tomselect) return;
    new TomSelect(el, window.TS_SINGLE_OPTS);
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
