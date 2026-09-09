/* items.html - logica de la pantalla.
 *
 * Estaba inline dentro del template. Se movio a un archivo para que el HTML de
 * la pantalla no la cargue en cada request, para que el navegador la cachee, y
 * sobre todo porque una CSP sin 'unsafe-inline' no puede permitir <script>
 * escritos dentro del HTML.
 *
 * Los DATOS siguen viniendo del servidor (window.ITEMS_UI_DATA), porque dependen
 * de la base. El codigo es exactamente el que estaba en el template: lo unico
 * que cambio son las lineas que antes escribia Jinja y ahora leen de ese objeto.
 */
(function () {
  var D = window.ITEMS_UI_DATA || {};

  // El contenido se inserta con textContent / createElement, nunca con innerHTML
  // sobre datos: los nombres de ítems y proveedores los escribe el usuario y no
  // se pueden interpretar como HTML.
  function verHistorialPrecios(itemId){
    var body = document.getElementById('precios-body');
    body.textContent = 'Cargando...';
    openModal('modal-precios');
    fetch('/costos/item/' + itemId + '/historial', {credentials:'same-origin'})
      .then(function(r){ if(!r.ok) throw new Error('http'); return r.json(); })
      .then(function(d){
        body.textContent = '';

        var h = document.createElement('p');
        h.appendChild(document.createElement('strong')).textContent = d.item;
        body.appendChild(h);

        var res = document.createElement('p');
        res.className = 'muted';
        res.textContent = 'Promedio simple: ' + d.promedio_simple
                        + ' · Ponderado por cantidad: ' + d.promedio_ponderado;
        body.appendChild(res);

        if(!d.ingresos.length){
          var p = document.createElement('p');
          p.className = 'muted';
          p.textContent = 'Este ítem todavía no tuvo ningún ingreso con precio.';
          body.appendChild(p);
          return;
        }

        var tbl = document.createElement('table');
        tbl.className = 'data-table';
        var thead = document.createElement('thead');
        var htr = document.createElement('tr');
        ['Fecha','Proveedor','Cantidad','Precio unit.','Total',''].forEach(function(t){
          var th = document.createElement('th'); th.textContent = t; htr.appendChild(th);
        });
        thead.appendChild(htr); tbl.appendChild(thead);

        var tb = document.createElement('tbody');
        d.ingresos.forEach(function(r){
          var tr = document.createElement('tr');
          [r.fecha, r.proveedor, r.cantidad, r.precio, r.total,
           r.editado ? 'corregido' : ''].forEach(function(v){
            var td = document.createElement('td'); td.textContent = v; tr.appendChild(td);
          });
          tb.appendChild(tr);
        });
        tbl.appendChild(tb);
        body.appendChild(tbl);
      })
      .catch(function(){ body.textContent = 'No se pudo cargar el historial.'; });
  }

  /* Estas funciones se llaman desde atributos onclick="..." del HTML, que se
     resuelven contra el ambito global. Al pasar el codigo a un archivo quedo
     todo dentro de este IIFE, asi que hay que exponerlas a mano: sin esto, los
     botones de la pantalla tiran "no esta definida" y no hacen nada.
     Cuando esos onclick se reemplacen por addEventListener (el paso que falta
     para poder cerrar la CSP), estas lineas se borran. */
  window.verHistorialPrecios = verHistorialPrecios;
})();
