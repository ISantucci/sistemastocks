// Carga de seriales en tanda: escaneo con camara o tipeado/pegado.
// Los dos modos escriben en la MISMA lista; el textarea oculto "serials" es lo
// unico que viaja al backend (un serial por linea).
//
// Decision de diseno: la camara NUNCA guarda sola. Solo agrega a la lista, que
// el operador ve y puede corregir antes de enviar. Es lo que mantiene el riesgo
// bajo cuando la etiqueta trae varios codigos de barra pegados.
//
// El filtro de formato de aca es un ESPEJO del backend (serial_looks_wrong en
// app.py). Sirve para avisar en el momento del escaneo; la validacion que vale
// es siempre la del servidor.

function initSerialScan(opts) {
  var listEl = document.getElementById("bulk-list");
  var hidden = document.getElementById("bulk-serials");
  var taEl = document.getElementById("bulk-textarea");
  var countEl = document.getElementById("bulk-count");
  var warnEl = document.getElementById("bulk-warn");
  var saveEl = document.getElementById("bulk-save");
  var forceEl = document.getElementById("bulk-force");
  var readerEl = document.getElementById("bulk-reader");
  var camBtn = document.getElementById("bulk-cam-toggle");
  var camMsg = document.getElementById("bulk-cam-msg");
  var locEl = document.getElementById("bulk-loc");

  if (!listEl || !hidden) return;

  var rooms = opts.rooms || {};      // { location_id: cupo_disponible }
  var existing = opts.existing || []; // seriales ya cargados en el item (minusculas)
  var items = [];                     // [{ s: "ABC", bad: "motivo"|null, dup: bool }]
  var scanner = null;
  var scanning = false;

  function cupo() {
    var v = locEl ? rooms[String(locEl.value)] : undefined;
    return (typeof v === "number") ? v : null;
  }

  // Espejo de serial_looks_wrong() en app.py.
  function looksWrong(s) {
    if (/^\d+$/.test(s)) return "todo números: parece el EAN de la caja";
    if (s.indexOf(".") !== -1) return "tiene puntos: parece el part number";
    if (s.length < 6) return "demasiado corto para ser un serial";
    return null;
  }

  function indexOfSerial(low) {
    for (var i = 0; i < items.length; i++) {
      if (items[i].s.toLowerCase() === low) return i;
    }
    return -1;
  }

  // silent = true cuando viene de la camara: un codigo leido dos veces es lo
  // normal (el lector dispara varias veces sobre la misma etiqueta) y no tiene
  // que ensuciar la lista.
  function add(raw, silent) {
    var s = (raw || "").trim();
    if (!s) return false;
    if (s.length > 120) s = s.substring(0, 120);
    var low = s.toLowerCase();

    if (indexOfSerial(low) !== -1) {
      if (!silent) { items.push({ s: s, bad: null, dup: true }); render(); }
      return false;
    }
    var bad = null;
    if (existing.indexOf(low) !== -1) bad = "ya está cargado en este ítem";
    else if (!forceEl || !forceEl.checked) bad = looksWrong(s);

    items.push({ s: s, bad: bad, dup: false });
    render();
    return bad === null;
  }

  function addFromText() {
    if (!taEl) return;
    var raw = taEl.value.replace(/[,;\t]/g, "\n").split("\n");
    for (var i = 0; i < raw.length; i++) add(raw[i], false);
    taEl.value = "";
  }

  function remove(i) { items.splice(i, 1); render(); }

  function feedback() {
    // Beep corto + vibracion: escaneando de a 20 con el telefono en la mano no
    // se puede estar mirando la pantalla en cada lectura.
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        var ctx = new Ctx();
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.frequency.value = 880;
        gain.gain.value = 0.08;
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start();
        setTimeout(function () { osc.stop(); ctx.close(); }, 90);
      }
    } catch (e) { /* sin audio no pasa nada */ }
    if (navigator.vibrate) { try { navigator.vibrate(60); } catch (e) {} }
  }

  function render() {
    var ok = 0, mal = 0, i;
    for (i = 0; i < items.length; i++) {
      if (items[i].bad || items[i].dup) mal++; else ok++;
    }

    if (!items.length) {
      listEl.innerHTML = '<div class="bulk-empty">Todavía no cargaste ninguno.</div>';
    } else {
      var html = "";
      for (i = 0; i < items.length; i++) {
        var it = items[i];
        var cls = it.dup ? "dup" : (it.bad ? "bad" : "");
        var badge = it.dup
          ? '<span class="badge badge-warn">repetido</span>'
          : (it.bad ? '<span class="badge badge-danger">revisar</span>'
                    : '<span class="badge badge-ok">ok</span>');
        var why = it.dup ? "Ya está en esta misma tanda." : (it.bad || "");
        html += '<div class="bulk-row ' + cls + '">' +
                  '<span class="bulk-n">' + (i + 1) + '</span>' +
                  '<span class="bulk-sn"></span>' + badge +
                  '<button type="button" class="btn btn-danger btn-sm" data-rm="' + i + '">Quitar</button>' +
                  (why ? '<div class="bulk-why"></div>' : '') +
                '</div>';
      }
      listEl.innerHTML = html;
      // El texto se asigna por textContent (nunca por innerHTML): un serial es
      // entrada del usuario y no tiene que poder inyectar markup.
      var rows = listEl.querySelectorAll(".bulk-row");
      for (i = 0; i < rows.length; i++) {
        rows[i].querySelector(".bulk-sn").textContent = items[i].s;
        var w = rows[i].querySelector(".bulk-why");
        if (w) w.textContent = items[i].dup ? "Ya está en esta misma tanda." : (items[i].bad || "");
      }
      var btns = listEl.querySelectorAll("[data-rm]");
      for (i = 0; i < btns.length; i++) {
        btns[i].addEventListener("click", function () {
          remove(parseInt(this.getAttribute("data-rm"), 10));
        });
      }
    }

    var lines = [];
    for (i = 0; i < items.length; i++) {
      if (!items[i].dup) lines.push(items[i].s);
    }
    hidden.value = lines.join("\n");

    var room = cupo();
    var over = (room !== null && ok > room);
    if (countEl) {
      countEl.textContent = ok + " en la tanda" +
        (room !== null ? " · cupo " + room : "") +
        (mal ? " · " + mal + " a revisar" : "");
    }
    if (warnEl) {
      if (over) {
        warnEl.textContent = "Te pasaste del cupo: en esa ubicación hay lugar para " +
          room + " y cargaste " + ok + ". Ingresá el stock primero o sacá seriales de la tanda.";
        warnEl.style.display = "";
      } else if (mal) {
        warnEl.textContent = "Hay " + mal + " para revisar. No se guarda nada hasta resolverlos: " +
          "es todo o nada, para que no te queden 19 de 20 sin saber cuál faltó.";
        warnEl.style.display = "";
      } else {
        warnEl.style.display = "none";
      }
    }
    // Texto tipeado que todavia no se volco a la lista. Como este formulario es
    // el UNICO camino para cargar seriales, el boton no puede quedar apagado
    // mientras hay algo escrito: se volcaria a la nada al enviar.
    var pend = (taEl && taEl.value.trim()) ? true : false;

    if (saveEl) {
      saveEl.disabled = pend ? false : (ok === 0 || mal > 0 || over);
      saveEl.textContent = pend
        ? "Agregar y guardar"
        : "Guardar " + ok + (ok === 1 ? " serial" : " seriales");
    }
  }

  // ---------------- camara ----------------

  function stopCam() {
    scanning = false;
    if (camBtn) camBtn.textContent = "Encender cámara";
    if (readerEl) readerEl.style.display = "none";
    if (!scanner) return;
    try { scanner.stop().then(function () { scanner.clear(); }); } catch (e) {}
  }

  function startCam() {
    if (typeof Html5Qrcode === "undefined") {
      if (camMsg) {
        camMsg.textContent = "No se pudo cargar el lector de códigos. Usá «Escribir / pegar».";
        camMsg.style.display = "";
      }
      return;
    }
    if (!window.isSecureContext) {
      if (camMsg) {
        camMsg.textContent = "La cámara solo funciona por HTTPS. Usá «Escribir / pegar».";
        camMsg.style.display = "";
      }
      return;
    }
    if (readerEl) readerEl.style.display = "";
    scanner = new Html5Qrcode("bulk-reader", {
      // Solo Code128/Code39: la etiqueta trae ademas un EAN-13 del modelo, que
      // es identico en todas las unidades. Restringiendo el decodificador el
      // EAN directamente no se lee, y no hay que filtrarlo despues.
      formatsToSupport: [
        Html5QrcodeSupportedFormats.CODE_128,
        Html5QrcodeSupportedFormats.CODE_39
      ],
      verbose: false
    });
    scanning = true;
    if (camBtn) camBtn.textContent = "Apagar cámara";
    scanner.start(
      { facingMode: "environment" },
      {
        fps: 10,
        // qrbox como funcion, no fijo: en un telefono angosto un recuadro mas
        // ancho que el video hace que html5-qrcode lo rechace y no arranque.
        // Se calcula sobre el tamano real del visor. Ancho y bajo, porque lo
        // que se lee es un codigo de barras 1D, no un QR.
        qrbox: function (vw, vh) {
          var w = Math.floor(Math.min(vw * 0.9, 320));
          var h = Math.floor(Math.min(vh * 0.6, Math.max(80, w * 0.45)));
          return { width: w, height: h };
        }
      },
      function (text) { if (add(text, true)) feedback(); },
      function () { /* frame sin codigo: normal, no es error */ }
    ).catch(function (err) {
      scanning = false;
      if (camBtn) camBtn.textContent = "Encender cámara";
      if (readerEl) readerEl.style.display = "none";
      if (camMsg) {
        camMsg.textContent = "No se pudo abrir la cámara (" + err + "). Usá «Escribir / pegar».";
        camMsg.style.display = "";
      }
    });
  }

  // ---------------- cableado ----------------

  if (camBtn) {
    camBtn.addEventListener("click", function () {
      if (scanning) stopCam(); else startCam();
    });
  }
  var addBtn = document.getElementById("bulk-add-text");
  if (addBtn) addBtn.addEventListener("click", addFromText);
  if (taEl) {
    // Una pistola lectora Bluetooth se comporta como teclado y manda Enter:
    // con Ctrl+Enter se vuelca todo, con Enter solo sigue en la linea de abajo.
    taEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); addFromText(); }
    });
    taEl.addEventListener("input", render);
  }

  // Red de seguridad: si alguien escribe y va derecho a Guardar sin apretar
  // "Agregar a la lista", se vuelca solo. Si lo volcado resulta invalido, se
  // frena el envio y el motivo queda visible en la lista.
  if (hidden.form) {
    hidden.form.addEventListener("submit", function (e) {
      if (taEl && taEl.value.trim()) {
        addFromText();
        if (saveEl && saveEl.disabled) e.preventDefault();
      }
    });
  }
  if (locEl) locEl.addEventListener("change", render);
  if (forceEl) {
    forceEl.addEventListener("change", function () {
      // Revalidar lo ya cargado con el filtro prendido o apagado.
      for (var i = 0; i < items.length; i++) {
        if (items[i].dup) continue;
        var low = items[i].s.toLowerCase();
        if (existing.indexOf(low) !== -1) items[i].bad = "ya está cargado en este ítem";
        else items[i].bad = forceEl.checked ? null : looksWrong(items[i].s);
      }
      render();
    });
  }
  var tabs = document.querySelectorAll("[data-bulk-tab]");
  for (var t = 0; t < tabs.length; t++) {
    tabs[t].addEventListener("click", function () {
      var mode = this.getAttribute("data-bulk-tab");
      var all = document.querySelectorAll("[data-bulk-tab]");
      for (var k = 0; k < all.length; k++) {
        all[k].classList.toggle("on", all[k] === this);
      }
      var cam = document.getElementById("bulk-mode-cam");
      var txt = document.getElementById("bulk-mode-txt");
      if (cam) cam.style.display = (mode === "cam") ? "" : "none";
      if (txt) txt.style.display = (mode === "txt") ? "" : "none";
      if (mode !== "cam" && scanning) stopCam();
    });
  }

  render();
}
