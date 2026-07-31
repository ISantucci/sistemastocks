/* ================================================================
   metricas.js  -  Helpers de gráficos para la sección Métricas.
   Requiere Chart.js (cargado por CDN en cada plantilla de métricas).
   Aislado: no interfiere con app.js. Si Chart no está disponible,
   las funciones no hacen nada y las tablas siguen visibles.
   ================================================================ */
(function () {
  var MX = {};
  window.MX = MX;

  MX.colors = {
    primary:  '#3f6fd6',
    primarySoft: 'rgba(63,111,214,0.14)',
    ok:       '#2fa36b',
    okSoft:   'rgba(47,163,107,0.16)',
    warn:     '#d9971f',
    warnSoft: 'rgba(217,151,31,0.18)',
    danger:   '#d1483c',
    dangerSoft:'rgba(209,72,60,0.16)',
    grid:     'rgba(20,24,40,0.06)',
    text:     '#5a6270'
  };
  // Paleta cíclica para dona/categorías
  MX.palette = ['#3f6fd6','#2fa36b','#d9971f','#8b5cd6','#25a7b8','#d1483c',
                '#e07b39','#5aa5f0','#6bbf59','#c85b9e','#7a8794','#4b56a8'];

  function toneColor(tone) {
    if (tone === 'danger') return { line: MX.colors.danger, fill: MX.colors.dangerSoft };
    if (tone === 'warn')   return { line: MX.colors.warn,   fill: MX.colors.warnSoft };
    if (tone === 'ok')     return { line: MX.colors.ok,     fill: MX.colors.okSoft };
    return { line: MX.colors.primary, fill: MX.colors.primarySoft };
  }

  function ready(id) {
    if (!window.Chart) return null;
    var el = document.getElementById(id);
    if (!el) return null;
    Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = MX.colors.text;
    return el.getContext('2d');
  }

  function hasData(data) { return data && data.some(function (v) { return v && v > 0; }); }

  function emptyMsg(id) {
    var el = document.getElementById(id);
    if (el && el.parentNode) el.parentNode.innerHTML = '<div class="mx-empty">Sin datos en el período seleccionado.</div>';
  }

  MX.line = function (id, labels, data, opts) {
    opts = opts || {};
    var ctx = ready(id); if (!ctx) return;
    if (!hasData(data)) { emptyMsg(id); return; }
    var t = toneColor(opts.tone);
    new Chart(ctx, {
      type: 'line',
      data: { labels: labels, datasets: [{
        label: opts.label || 'Valor', data: data,
        borderColor: t.line, backgroundColor: t.fill,
        fill: true, tension: 0.35, pointRadius: 3, pointBackgroundColor: t.line, borderWidth: 2
      }]},
      options: baseOpts(false)
    });
  };

  MX.barV = function (id, labels, data, opts) {
    opts = opts || {};
    var ctx = ready(id); if (!ctx) return;
    if (!hasData(data)) { emptyMsg(id); return; }
    var t = toneColor(opts.tone);
    new Chart(ctx, {
      type: 'bar',
      data: { labels: labels, datasets: [{ label: opts.label || '', data: data,
        backgroundColor: t.line, borderRadius: 6, maxBarThickness: 46 }]},
      options: baseOpts(false)
    });
  };

  MX.barH = function (id, labels, data, opts) {
    opts = opts || {};
    var ctx = ready(id); if (!ctx) return;
    if (!hasData(data)) { emptyMsg(id); return; }
    var t = toneColor(opts.tone);
    new Chart(ctx, {
      type: 'bar',
      data: { labels: labels, datasets: [{ label: opts.label || '', data: data,
        backgroundColor: t.line, borderRadius: 6, maxBarThickness: 26 }]},
      options: baseOpts(true)
    });
  };

  MX.doughnut = function (id, labels, data, opts) {
    opts = opts || {};
    var ctx = ready(id); if (!ctx) return;
    if (!hasData(data)) { emptyMsg(id); return; }
    new Chart(ctx, {
      type: 'doughnut',
      data: { labels: labels, datasets: [{ data: data, backgroundColor: MX.palette,
        borderColor: '#fff', borderWidth: 2, hoverOffset: 6 }]},
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '58%',
        plugins: { legend: { position: 'right', labels: { boxWidth: 12, padding: 10, font: { size: 11 } } } }
      }
    });
  };

  MX.grouped = function (id, labels, datasets) {
    var ctx = ready(id); if (!ctx) return;
    var any = datasets.some(function (d) { return hasData(d.data); });
    if (!any) { emptyMsg(id); return; }
    var ds = datasets.map(function (d, i) {
      var c = [MX.colors.primary, MX.colors.warn, MX.colors.danger, MX.colors.ok][i % 4];
      return { label: d.label, data: d.data, backgroundColor: c, borderRadius: 5, maxBarThickness: 22 };
    });
    new Chart(ctx, {
      type: 'bar', data: { labels: labels, datasets: ds },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'top', labels: { boxWidth: 12, padding: 12 } } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, grid: { color: MX.colors.grid }, ticks: { precision: 0 } }
        }
      }
    });
  };

  function baseOpts(horizontal) {
    var o = {
      responsive: true, maintainAspectRatio: false,
      indexAxis: horizontal ? 'y' : 'x',
      plugins: { legend: { display: false },
        tooltip: { padding: 10, cornerRadius: 8 } },
      scales: {}
    };
    if (horizontal) {
      o.scales = {
        x: { beginAtZero: true, grid: { color: MX.colors.grid }, ticks: { precision: 0 } },
        y: { grid: { display: false } }
      };
    } else {
      o.scales = {
        x: { grid: { display: false } },
        y: { beginAtZero: true, grid: { color: MX.colors.grid }, ticks: { precision: 0 } }
      };
    }
    return o;
  }
})();
