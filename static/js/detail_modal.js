/* base.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.DETAIL_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.DETAIL_DATA || {};

  function openDetail(url) {
    var f = document.getElementById('detail-frame');
    var ov = document.getElementById('detail-modal');
    if (!f || !ov) return;
    f.src = url + (url.indexOf('?') > -1 ? '&' : '?') + 'embed=1';
    ov.classList.add('open');
    document.body.classList.add('modal-open');
    history.pushState({ detail: true }, '', '#detalle');
  }
  function closeDetail() {
    var f = document.getElementById('detail-frame');
    var ov = document.getElementById('detail-modal');
    if (!ov) return;
    ov.classList.remove('open');
    document.body.classList.remove('modal-open');
    if (f) f.src = 'about:blank';
  }
  // Cierre manual (X, clic afuera, Escape).
  //
  // ANTES esto hacía `history.back()`, y ahí estaba el bug: el contenido del
  // popup vive en un IFRAME, y cada navegación adentro del iframe (cargar un
  // serial, editar, paginar) empuja su propia entrada en el historial de la
  // pestaña. Entonces el back() no sacaba la entrada '#detalle': retrocedía UNA
  // OPERACIÓN DENTRO del popup. El síntoma era que tocabas afuera y en vez de
  // cerrarse, el popup "se deshacía" un paso — y hacía falta un clic por cada
  // cosa que hubieras hecho adentro. Se veía sólo en la ficha de Seriales,
  // que es la única donde se opera; remitos y solicitudes sólo se leen.
  //
  // Ahora se cierra primero —siempre, al primer clic— y el '#detalle' se limpia
  // con replaceState, que NO navega. El botón "atrás" del navegador sigue
  // cerrando el popup mientras está abierto: eso lo hace el popstate de abajo,
  // que no se tocó.
  function dismissDetail() {
    closeDetail();
    if (location.hash === '#detalle') {
      history.replaceState(null, '', location.pathname + location.search);
    }
  }
  // Cerrar con el botón "atrás" del navegador.
  window.addEventListener('popstate', function () {
    var ov = document.getElementById('detail-modal');
    if (ov && ov.classList.contains('open')) closeDetail();
  });
  // Cerrar con Escape.
  document.addEventListener('keydown', function (e) {
    var ov = document.getElementById('detail-modal');
    if (e.key === 'Escape' && ov && ov.classList.contains('open')) dismissDetail();
  });
  // Cierre por clic en el fondo. Se exige que el gesto EMPIECE y TERMINE sobre
  // el fondo: si se arrastra desde adentro (por ejemplo seleccionando texto) y
  // se suelta afuera, el modal NO se cierra.
  (function () {
    var startedOnBackdrop = false;
    var ov = document.getElementById('detail-modal');
    if (!ov) return;
    ov.addEventListener('mousedown', function (e) {
      startedOnBackdrop = (e.button === 0 && e.target === ov);
    });
    ov.addEventListener('mouseup', function (e) {
      var started = startedOnBackdrop;
      startedOnBackdrop = false;
      if (e.button === 0 && started && e.target === ov) dismissDetail();
    });
  })();

  /* Estas funciones se llaman desde atributos onclick="..." del HTML, que se
     resuelven contra el ambito global. Al pasar el codigo a un archivo quedo
     todo dentro de este IIFE, asi que hay que exponerlas a mano: sin esto, los
     botones de la pantalla tiran "no esta definida" y no hacen nada.
     Cuando esos onclick se reemplacen por addEventListener (el paso que falta
     para poder cerrar la CSP), estas lineas se borran. */
  window.openDetail = openDetail;
  window.dismissDetail = dismissDetail;
})();
