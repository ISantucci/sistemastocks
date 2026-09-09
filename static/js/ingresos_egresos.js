/* Ingresos / Egresos - logica de la pantalla.
 *
 * Estaba inline dentro de ingresos_egresos.html (336 lineas dentro del HTML).
 * Se movio aca por tres motivos: el HTML de la pantalla baja de golpe, el
 * navegador puede cachear este archivo entre pantallas, y sobre todo es lo que
 * permite cerrar la CSP mas adelante (una politica sin 'unsafe-inline' no
 * puede permitir <script> escritos dentro del HTML).
 *
 * Los DATOS siguen viniendo del servidor, inline en el template, porque
 * dependen de la base: window.IO_DATA. El codigo de abajo es exactamente el
 * mismo que estaba en el template, sin una sola linea cambiada; lo unico que
 * se agrega son estas cuatro variables, que antes las escribia Jinja.
 */
(function () {
  var IO_DATA = window.IO_DATA || {};
  var IO_SERIALIZED = new Set(IO_DATA.serialized || []);
  var IO_JAULA_UNITS = IO_DATA.jaulaUnits || {};   // { item_id: [[unit_id, serial], ...] }
  var IO_JAULA_STOCK = IO_DATA.jaulaStock || {};   // { item_id: cantidad en Jaula }
  var IO_ALL_ITEMS = IO_DATA.allItems || [];
  var snCurrentLine = null;

  function isSerial(itemId){ return IO_SERIALIZED.has(parseInt(itemId, 10)); }
  function currentTipo(){ return document.getElementById('io-tipo').value; }

  // En EGRESO solo se pueden mover ítems que la Jaula tiene en stock.
  function allowedItems(){
    if(currentTipo() === 'EGRESO'){
      return IO_ALL_ITEMS.filter(function(o){ return (IO_JAULA_STOCK[o.value] || 0) > 0; });
    }
    return IO_ALL_ITEMS;
  }

  // Un item = una sola fila: el dedupe saca de cada listado lo ya elegido en
  // las otras filas. El backend igual rechaza item_id[] repetidos.
  // Perezoso a proposito: este <script> inline corre ANTES que los <script src>
  // de base.html (van al final del body), asi que initLineDedupe todavia no
  // existe en el top-level. Llamarlo aca arriba rompe toda la pantalla.
  var _ioDedupe = null;
  function ioDedupe() {
    if (!_ioDedupe) { _ioDedupe = initLineDedupe({ itemSel: '.io-item', optionsFor: allowedItems }); }
    return _ioDedupe;
  }

  // Recalcula los listados de TODAS las filas. Primero limpia lo que dejo de
  // aplicar al tipo actual (ej. un item sin stock en Jaula al pasar a EGRESO).
  function applyItemsToAllLines(){
    var ok = {};
    allowedItems().forEach(function(o){ ok[o.value] = true; });
    document.querySelectorAll('.io-item').forEach(function(sel){
      var ts = sel.tomselect;
      if(!ts) return;
      var cur = String(ts.getValue() || '');
      if(cur && !ok[cur]){ ts.clear(true); }   // ya no aplica al tipo actual
    });
    ioDedupe().refresh();
  }

  // Tope de cantidad: en EGRESO, la cantidad no puede superar el stock de la Jaula.
  function maxForLine(line){
    if(currentTipo() !== 'EGRESO') return null;   // ingreso: sin tope
    var id = line.querySelector('.io-item').value;
    if(!id) return null;
    var m = IO_JAULA_STOCK[id] || 0;
    return m > 0 ? m : null;
  }

  function clampQty(line){
    var qty = line.querySelector('.io-qty');
    var mx = maxForLine(line);
    if(mx == null){ qty.removeAttribute('max'); return; }
    qty.setAttribute('max', mx);
    var v = parseInt(qty.value, 10);
    if(!isNaN(v) && v > mx){ qty.value = mx; }   // si tipeó de más, al máximo posible
  }

  // ---- COSTOS: precio unitario (solo en INGRESO) ----
  // El precio se pide únicamente cuando entra mercadería nueva. En egreso los
  // campos se ocultan y se les saca el required, mismo criterio que el Motivo
  // (que es al revés: solo en egreso).

  // Acepta lo que la gente tipea: "1234,56", "1.234,56", "1234.56".
  // Espeja parse_money_to_cents() del backend, que es quien valida de verdad.
  function parseMoney(txt){
    if(!txt) return null;
    var t = String(txt).replace(/\$/g,'').replace(/\s/g,'');
    if(!t) return null;
    if(t.indexOf(',') !== -1){
      t = t.replace(/\./g,'').replace(',', '.');
    } else if(t.indexOf('.') !== -1){
      var parts = t.split('.');
      var dec = parts[parts.length-1];
      if(dec.length === 1 || dec.length === 2){ t = parts.slice(0,-1).join('') + '.' + dec; }
      else { t = parts.join(''); }
    }
    var v = parseFloat(t);
    if(isNaN(v) || v <= 0) return null;
    return v;
  }

  function fmtMoney(v){
    if(v === null || isNaN(v)) return '—';
    var s = v.toFixed(2).split('.');
    return '$ ' + s[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.') + ',' + s[1];
  }

  function esIngreso(){ return currentTipo() === 'INGRESO'; }

  // Se carga el TOTAL de la línea (lo que dice el remito) y el sistema muestra
  // el unitario. Es al revés que antes, y es a propósito: evita que la persona
  // tenga que dividir a mano, sobre todo con ítems medidos en metros.
  function refreshLineTotal(line){
    var out = line.querySelector('.io-line-total');
    if(!out) return;
    var tot = parseMoney(line.querySelector('.io-price').value);
    var q = parseInt(line.querySelector('.io-qty').value, 10);
    out.value = (tot === null || isNaN(q) || q < 1) ? '—' : fmtMoney(tot / q);
  }

  function refreshTotal(){
    var wrap = document.getElementById('io-total-wrap');
    if(!esIngreso()){ wrap.style.display = 'none'; return; }
    wrap.style.display = '';
    var total = 0, hayAlguno = false;
    document.querySelectorAll('.io-line').forEach(function(line){
      if(!line.querySelector('.io-item').value) return;
      var tot = parseMoney(line.querySelector('.io-price').value);
      if(tot !== null){ total += tot; hayAlguno = true; }
    });
    document.getElementById('io-total').textContent = hayAlguno ? fmtMoney(total) : '—';
  }

  function refreshPriceFields(line){
    var ing = esIngreso();
    line.querySelectorAll('.io-price-field').forEach(function(f){
      f.style.display = ing ? '' : 'none';
    });
    var inp = line.querySelector('.io-price');
    // El `required` va SOLO si la fila tiene un ítem elegido. El formulario
    // arranca con tres filas y las que sobran se mandan vacías (el backend las
    // ignora): si el precio fuera required en todas, el navegador bloquearía el
    // submit pidiendo completar un campo de una fila que nadie usó.
    var tieneItem = !!line.querySelector('.io-item').value;
    if(ing && tieneItem){ inp.setAttribute('required','required'); }
    else { inp.removeAttribute('required'); }
    if(!ing){ inp.value = ''; }
    refreshLineTotal(line);
  }

  function serialsOf(line){
    return line.querySelector('.io-serials').value.split('\n').map(function(s){return s.trim();}).filter(Boolean);
  }

  function refreshLine(line){
    var itemSel = line.querySelector('.io-item');
    var snWrap = line.querySelector('.io-sn-btn-wrap');
    // El botón "Elegir S/N" aparece solo en EGRESO de un serializado (opcional).
    // La cantidad queda SIEMPRE editable: podés egresar por cantidad, o elegir
    // seriales puntuales (en cuyo caso la cantidad se ajusta a lo elegido).
    var needSn = isSerial(itemSel.value) && currentTipo() === 'EGRESO';
    // OJO: usar 'block' y no '' — con '' vuelve a la regla CSS display:none.
    snWrap.style.display = needSn ? 'block' : 'none';
    if(!needSn){ line.querySelector('.io-serials').value = ''; }
    line.querySelector('.io-qty').readOnly = false;
    clampQty(line);
    updateSnBtn(line);
    refreshPriceFields(line);
    refreshTotal();
  }

  function updateSnBtn(line){
    var btn = line.querySelector('.io-sn-btn');
    if(!btn) return;
    var n = serialsOf(line).length;
    btn.textContent = n > 0 ? ('S/N: ' + n + ' elegido' + (n>1?'s':'')) : 'Elegir S/N';
  }

  function snUpdateCounter(){
    if(!snCurrentLine) return;
    var body = document.getElementById('sn-body');
    var declared = parseInt(snCurrentLine.querySelector('.io-qty').value, 10) || 0;
    var n = body.querySelectorAll('.sn-check:checked').length;
    var avail = body.querySelectorAll('.sn-check').length;
    var counter = document.getElementById('sn-counter');
    if(counter){ counter.textContent = 'Seleccionados ' + n + ' de ' + declared + ' · disponibles en Jaula: ' + avail; }
    document.getElementById('sn-confirm').disabled = (declared < 1 || n !== declared);
  }

  function openSn(line){
    snCurrentLine = line;
    var itemId = line.querySelector('.io-item').value;
    var body = document.getElementById('sn-body');
    body.innerHTML = '';
    if(!itemId){ avisar('Elegí primero el ítem.'); return; }
    var declared = parseInt(line.querySelector('.io-qty').value, 10) || 0;
    if(declared < 1){ avisar('Poné primero la cantidad a egresar.'); return; }

    var counter = document.createElement('p');
    counter.className = 'muted'; counter.id = 'sn-counter';
    counter.style.marginTop = '0';
    body.appendChild(counter);

    var current = serialsOf(line);
    var units = IO_JAULA_UNITS[itemId] || [];
    if(units.length === 0){
      var p = document.createElement('p'); p.className = 'muted';
      p.textContent = 'No hay seriales de este ítem en la Jaula. Etiquetalos primero desde la ficha del ítem.';
      body.appendChild(p);
    } else {
      units.forEach(function(u){
        var lab = document.createElement('label'); lab.className='sn-row';
        var cb = document.createElement('input'); cb.type='checkbox'; cb.className='sn-check'; cb.value = u[1];
        if(current.indexOf(u[1]) !== -1) cb.checked = true;
        cb.addEventListener('change', snUpdateCounter);
        var span = document.createElement('span'); span.textContent = u[1];
        lab.appendChild(cb); lab.appendChild(span); body.appendChild(lab);
      });
    }
    snUpdateCounter();
    openModal('modal-sn');
  }

  function confirmSn(){
    if(!snCurrentLine) return;
    var body = document.getElementById('sn-body');
    var declared = parseInt(snCurrentLine.querySelector('.io-qty').value, 10) || 0;
    var vals = [];
    body.querySelectorAll('.sn-check:checked').forEach(function(cb){ vals.push(cb.value); });
    if(declared < 1 || vals.length !== declared){
      avisar('Elegí exactamente ' + declared + ' serial(es), acorde a la cantidad declarada.');
      return;
    }
    snCurrentLine.querySelector('.io-serials').value = vals.join('\n');
    updateSnBtn(snCurrentLine);
    closeModal('modal-sn');
  }

  function wireLine(line){
    var sel = line.querySelector('.io-item');
    if(window.TomSelect && !sel.tomselect){
      new TomSelect(sel, { create:false, sortField:{field:'text',direction:'asc'}, maxOptions:null,
                           placeholder:'Buscá un ítem...', onChange:function(){ refreshLine(line); } });
    }
    sel.addEventListener('change', function(){
      refreshLine(line);
      ioDedupe().refresh();   // liberar/tomar el item en las demas filas
    });
    // Si cambia la cantidad: se limpian los seriales elegidos y se aplica el tope.
    line.querySelector('.io-qty').addEventListener('input', function(){
      line.querySelector('.io-serials').value = '';
      clampQty(line);
      updateSnBtn(line);
      refreshLineTotal(line);
      refreshTotal();
    });
    line.querySelector('.io-price').addEventListener('input', function(){
      refreshLineTotal(line);
      refreshTotal();
    });
    line.querySelector('.io-sn-btn').addEventListener('click', function(){ openSn(line); });
    // Al borrar la fila, su item vuelve a estar disponible en las demas.
    line.querySelector('.io-remove').addEventListener('click', function(){
      line.remove();
      ioDedupe().refresh();
    });
    applyItemsToAllLines();   // filtra por tipo actual + saca los ya elegidos
    refreshLine(line);
  }

  function addLine(){
    var tpl = document.getElementById('io-line-template');
    var cont = document.getElementById('io-lines');
    cont.appendChild(tpl.content.cloneNode(true));
    wireLine(cont.lastElementChild);
  }

  // Al enviar: si hay una fila de egreso serializado sin seriales (o que no
  // coinciden con la cantidad), se frena y se avisa.
  // Todos los avisos de esta pantalla salen por el mismo popup rojo que el
  // resto del sistema (static/js/form_errors.js). Si por algo no cargó, se cae
  // al alert del navegador: un aviso feo es mejor que ningún aviso.
  function avisar(msg){
    if (typeof window.showFormError === 'function') window.showFormError(msg);
    else alert(msg);
  }

  function validateBeforeSubmit(ev){
    var tipo = currentTipo();
    var msg = null;
    document.querySelectorAll('.io-line').forEach(function(line){
      var itemSel = line.querySelector('.io-item');
      if(!itemSel.value || msg) return;
      // Precio: obligatorio en ingreso. El backend igual lo rechaza; esto evita
      // perder toda la carga por un campo que se puede avisar acá.
      if(tipo === 'INGRESO' && parseMoney(line.querySelector('.io-price').value) === null){
        msg = 'Falta el precio total en alguna fila (o no es un número válido). En un ingreso el precio es obligatorio.';
        return;
      }
      if(isSerial(itemSel.value) && tipo === 'EGRESO'){
        var n = serialsOf(line).length;
        var q = parseInt(line.querySelector('.io-qty').value, 10) || 0;
        if(n === 0){ msg = 'Hay un ítem serializado sin seriales elegidos. Usá «Elegir S/N».'; }
        else if(n !== q){ msg = 'Los seriales elegidos no coinciden con la cantidad. Revisá «Elegir S/N».'; }
      }
    });
    if(msg){ ev.preventDefault(); avisar(msg); }
  }

  // El motivo es solo del egreso. En ingreso se oculta y se saca el required
  // para no bloquear el submit con un campo invisible.
  function refreshMotivoHint(){
    var sel = document.getElementById('io-motivo');
    var hint = document.getElementById('io-motivo-hint');
    if(currentTipo() === 'EGRESO' && sel.value === 'REPARACION'){
      hint.textContent = 'Queda esperando devolución en Reparaciones.';
    } else {
      hint.innerHTML = '&nbsp;';
    }
  }

  function refreshMotivo(){
    var esEgreso = (currentTipo() === 'EGRESO');
    var sel = document.getElementById('io-motivo');
    document.getElementById('io-motivo-field').style.display = esEgreso ? '' : 'none';
    if(esEgreso){ sel.setAttribute('required', 'required'); }
    else { sel.removeAttribute('required'); sel.value = ''; }
    refreshMotivoHint();
  }

  document.addEventListener('DOMContentLoaded', function(){
    document.getElementById('io-add-line').addEventListener('click', addLine);
    document.getElementById('sn-confirm').addEventListener('click', confirmSn);
    document.getElementById('io-form').addEventListener('submit', validateBeforeSubmit);
    // Al cambiar ingreso/egreso: se re-filtran los ítems (egreso = solo Jaula) y
    // se re-evalúan las filas (S/N + tope de cantidad).
    document.getElementById('io-tipo').addEventListener('change', function(){
      applyItemsToAllLines();
      document.querySelectorAll('.io-line').forEach(function(l){ refreshLine(l); });
      refreshMotivo();
      refreshTotal();
    });
    document.getElementById('io-motivo').addEventListener('change', refreshMotivoHint);
    refreshMotivo();
    // Una sola fila al entrar, igual que Utilizados, Descartes y Carga
    // multiple. Arrancar con tres dejaba dos filas vacias que el usuario
    // no habia pedido, y eran las que hacian fallar el envio en silencio.
    addLine();
  });
})();
