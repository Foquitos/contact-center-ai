/* Helpers compartidos por las pantallas de consumo de IA:
 *  - uso_ia.js         (Gastos y Logs de IA, grupo "auditorias")
 *  - uso_ia_chatbot.js (Gastos Chatbot, grupo "chatbot")
 * Formateo, fetch con CSRF, paleta y constructores de gráficos Chart.js.
 * Expone todo bajo window.UsoIA (sin módulos: mismo esquema <script> del resto
 * del frontend).
 */
(function () {
    "use strict";

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

    async function apiFetch(url) {
        const r = await fetch(url, { headers: { "X-CSRFToken": csrfToken } });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
        return data;
    }

    async function apiPost(url, body) {
        const r = await fetch(url, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken, "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || data.error || `HTTP ${r.status}`);
        return data;
    }

    const fmtUSD = (n, dec = 2) => "$" + (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
    const fmtNum = (n) => (Number(n) || 0).toLocaleString("es-AR");
    // Porcentaje con 1 decimal, salvo las participaciones ínfimas (<0.1%), que
    // como "0.0%" parecen cero: se muestran como "<0,1%".
    const fmtPct = (n) => {
        const v = Number(n) || 0;
        if (v > 0 && v < 0.1) return "<0,1%";
        return v.toLocaleString("es-AR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
    };
    // Cantidades grandes de tokens abreviadas (1,2 M / 340 k): las columnas de
    // tokens del detalle son de comparación, no de conteo exacto — el número
    // completo va en el title de la celda.
    const fmtTokens = (n) => {
        const v = Number(n) || 0;
        if (v >= 1e6) return (v / 1e6).toLocaleString("es-AR", { maximumFractionDigits: 1 }) + " M";
        if (v >= 1e4) return Math.round(v / 1e3).toLocaleString("es-AR") + " k";
        return fmtNum(v);
    };

    function escapeHtml(s) {
        return String(s ?? "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    const FEATURE_LABEL = {
        auditoria: "Auditorías",
        transcripcion: "Transcripción",
        chatbot: "Chatbot",
        asistente_plantillas: "Asistente plantillas",
        asistente_docs: "Asistente de documentación",
        asistente_manual: "Asistente del manual",
        asistente_dashboard: "Asistente del dashboard",
        vacios_conocimiento: "Vacíos de conocimiento",
    };
    const featLabel = (f) => FEATURE_LABEL[f] || f;

    // Qué es cada uso, para el tooltip del detalle: sin esto los nombres se
    // parecen demasiado entre sí para saber qué se está pagando.
    const FEATURE_DESC = {
        auditoria: "Análisis de cada interacción contra la plantilla de calidad.",
        transcripcion: "Pasaje de audio a texto (cola de transcripciones).",
        chatbot: "Consultas de los operadores al chatbot (tiene página propia).",
        asistente_plantillas: "Ayuda para redactar atributos y plantillas de auditoría.",
        asistente_docs: "Armado y verificación del markdown de conocimiento de los chatbots.",
        asistente_manual: "Respuestas sobre el manual de uso del sistema.",
        asistente_dashboard: "Consultas de calidad sobre las auditorías del dashboard (informes, rankings, coaching).",
        vacios_conocimiento: "Detección de preguntas que el chatbot no supo responder.",
    };
    const featDesc = (f) => FEATURE_DESC[f] || "";

    // Paleta categórica fija (tonos Bootstrap oscurecidos, validada con el
    // validador de dataviz: banda de luminosidad, croma, separación CVD y
    // contraste >= 3:1 sobre fondo claro). El orden es fijo: nunca se cicla.
    const PALETTE = ["#0d6efd", "#ca6510", "#198754", "#d63384", "#0894ad", "#6f42c1", "#b02a37", "#997404"];
    const COLOR_EXTRA = "#6c757d"; // series más allá de la paleta (no debería pasar: <=4 modelos/features)

    // El color sigue a la entidad, no a su posición en un gráfico puntual: el
    // mismo modelo/uso recibe el mismo color en el apilado, el doughnut y la
    // tabla de detalle (asignación por orden de aparición).
    //
    // Cada dimensión (`ns`) tiene su propio recorrido de la paleta: los modelos
    // ya son más de 8 y, con un único mapa compartido, los usos que aparecían
    // después se quedaban todos en gris. Dos entidades de dimensiones distintas
    // pueden repetir color sin confundir: nunca conviven en el mismo gráfico.
    const paletas = {};
    function colorFor(label, ns = "general") {
        const paleta = paletas[ns] || (paletas[ns] = { asignados: {}, proximo: 0 });
        if (!(label in paleta.asignados)) {
            paleta.asignados[label] = paleta.proximo < PALETTE.length ? PALETTE[paleta.proximo++] : COLOR_EXTRA;
        }
        return paleta.asignados[label];
    }

    // -- Defaults globales de Chart.js ----------------------------------------
    // Tipografía y leyendas coherentes con el resto de la página (Inter, texto
    // de ejes en gris tinta-suave, leyendas con puntito en vez de cuadrado).
    if (window.Chart) {
        Chart.defaults.font.family = "'Inter', system-ui, -apple-system, sans-serif";
        Chart.defaults.font.size = 11;
        Chart.defaults.color = "#6c757d";
        Chart.defaults.plugins.legend.labels.usePointStyle = true;
        Chart.defaults.plugins.legend.labels.pointStyle = "circle";
        Chart.defaults.plugins.legend.labels.boxWidth = 7;
        Chart.defaults.plugins.legend.labels.boxHeight = 7;
        Chart.defaults.plugins.legend.labels.padding = 14;
        Chart.defaults.plugins.tooltip.padding = 10;
        Chart.defaults.plugins.tooltip.cornerRadius = 6;
    }

    // Grilla recesiva: apenas visible, solo en el eje de valores.
    const GRID_COLOR = "rgba(33, 37, 41, 0.06)";

    // -- Registro de gráficos (destruir antes de redibujar) -------------------
    const charts = {};
    function destruir(id) {
        if (charts[id]) { charts[id].destroy(); delete charts[id]; }
    }

    // Barras mensuales apiladas: pivotea las filas {anio_mes, <campo>, costo_usd}
    // en 1 dataset por valor del campo. opts.campo elige la dimensión ("modelo"
    // por defecto, "feature" para la vista por uso) y opts.labelFn cómo se
    // muestra cada valor. Si no hay desglose, cae a 1 dataset con el total.
    function renderMesApilado(id, canvasId, porMes, porMesDim, opts = {}) {
        destruir(id);
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;
        const campo = opts.campo || "modelo";
        const labelFn = opts.labelFn || ((v) => v);
        const meses = porMes.map((r) => r.anio_mes);
        let datasets;
        if (porMesDim && porMesDim.length) {
            const valores = [...new Set(porMesDim.map((r) => r[campo]))];
            datasets = valores.map((valor) => ({
                label: labelFn(valor),
                // El color se pide por la ETIQUETA (no por el valor crudo): es
                // la misma clave que usa el doughnut, así un uso/modelo conserva
                // su color en todos los gráficos y tablas de la página.
                backgroundColor: colorFor(labelFn(valor), opts.ns),
                borderColor: "#ffffff", // separador entre segmentos apilados
                borderWidth: 2,
                data: meses.map((mes) => {
                    const fila = porMesDim.find((r) => r.anio_mes === mes && r[campo] === valor);
                    return fila ? Number(fila.costo_usd) || 0 : 0;
                }),
            }));
        } else {
            datasets = [{ label: "Costo USD", backgroundColor: PALETTE[0], data: porMes.map((r) => Number(r.costo_usd) || 0) }];
        }
        charts[id] = new Chart(ctx, {
            type: "bar",
            data: { labels: meses, datasets },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    // La leyenda solo aporta con >1 serie (con 1, el título ya la nombra).
                    legend: { display: datasets.length > 1, position: "bottom" },
                    tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmtUSD(c.parsed.y)}` } },
                },
                scales: {
                    x: { stacked: true, grid: { display: false }, border: { display: false } },
                    y: { stacked: true, grid: { color: GRID_COLOR }, border: { display: false }, ticks: { callback: (v) => "$" + v } },
                },
            },
        });
    }

    // Doughnut genérico. Por defecto grafica costo_usd (compat con las páginas de
    // gasto). opts.valueFn cambia la medida (ej. cantidad de auditorías),
    // opts.formato "num"|"usd" el formato del tooltip, y opts.colors es un mapa
    // etiqueta->color (para categorías con color semántico fijo, ej. estados);
    // sin él, cada etiqueta toma su color estable de la paleta (colorFor).
    function renderDoughnut(id, canvasId, items, labelFn, opts = {}) {
        destruir(id);
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;
        const valueFn = opts.valueFn || ((r) => Number(r.costo_usd) || 0);
        const fmt = opts.formato === "num" ? fmtNum : (v) => fmtUSD(v);
        const labels = items.map(labelFn);
        const backgroundColor = opts.colors
            ? labels.map((l) => opts.colors[l] || COLOR_EXTRA)
            : labels.map((l) => colorFor(l, opts.ns)); // mismo color que en los demás gráficos
        charts[id] = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels,
                datasets: [{ data: items.map(valueFn), backgroundColor, borderColor: "#ffffff", borderWidth: 2 }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                cutout: "62%",
                plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (c) => `${c.label}: ${fmt(c.parsed)}` } } },
            },
        });
    }

    // Línea temporal (1 medida por gráfico, sin doble eje). datasets:
    // [{label, data, color, fill?}]. Con muchos puntos oculta los marcadores para
    // no saturar. opts.formato "num"|"usd" formatea el tooltip.
    function renderLinea(id, canvasId, labels, datasets, opts = {}) {
        destruir(id);
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;
        const fmt = opts.formato === "usd" ? (v) => fmtUSD(v) : fmtNum;
        const muchos = labels.length > 45;
        charts[id] = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: datasets.map((ds) => ({
                    label: ds.label,
                    data: ds.data,
                    borderColor: ds.color,
                    backgroundColor: ds.fill ? ds.color + "22" : ds.color,
                    fill: !!ds.fill,
                    tension: 0.25,
                    borderWidth: 2,
                    pointRadius: muchos ? 0 : 2,
                    pointHoverRadius: 4,
                })),
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: datasets.length > 1, position: "bottom" },
                    tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y)}` } },
                },
                scales: {
                    x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
                    y: { grid: { color: GRID_COLOR }, border: { display: false }, beginAtZero: true, ticks: { precision: 0 } },
                },
            },
        });
    }

    // Barras verticales de 1 serie (ej. actividad por hora / por día de semana).
    // opts.color, opts.formato "num"|"usd".
    function renderBarras(id, canvasId, labels, data, opts = {}) {
        destruir(id);
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;
        const fmt = opts.formato === "usd" ? (v) => fmtUSD(v) : fmtNum;
        charts[id] = new Chart(ctx, {
            type: "bar",
            data: {
                labels,
                datasets: [{
                    label: opts.label || "", data,
                    backgroundColor: opts.color || PALETTE[0],
                    borderRadius: 4, maxBarThickness: 34,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => fmt(c.parsed.y) } } },
                scales: {
                    x: { grid: { display: false }, border: { display: false } },
                    y: { grid: { color: GRID_COLOR }, border: { display: false }, beginAtZero: true, ticks: { precision: 0 } },
                },
            },
        });
    }

    function renderBarrasH(id, canvasId, items, labelFn, valueFn, opts = {}) {
        destruir(id);
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;
        const dec = opts.decimales ?? 2;
        // opts.formato "num"|"usd" (default "usd"): elige el formato de ejes y
        // tooltip. "num" sirve para barras de cantidad (ej. auditorías por campaña).
        const formato = opts.formato || "usd";
        const fmtVal = (v, d) => formato === "num" ? fmtNum(v) : fmtUSD(v, d);
        const fmtTick = (v) => formato === "num" ? fmtNum(v) : "$" + v;
        // opts.series: [{label, valueFn, color}] para barras agrupadas (ej. costo
        // unitario sync vs batch). Sin series, una sola con valueFn/label/color.
        // Un valor null deja hueco (Chart.js no dibuja la barra), para distinguir
        // "no hubo corridas en ese modo" de un costo $0.
        const series = opts.series || [{ label: opts.label || "Costo USD", valueFn, color: opts.color || "#198754" }];

        // La altura la manda la cantidad de filas (por eso el .chart-container
        // delega su altura acá): N categorías apretadas en una altura fija es
        // lo primero que hace ilegible un gráfico de barras horizontales.
        const porFila = series.length > 1 ? 44 : 30;
        const alto = Math.max(200, items.length * porFila + (series.length > 1 ? 84 : 56));
        if (ctx.parentElement) ctx.parentElement.style.height = alto + "px";

        charts[id] = new Chart(ctx, {
            type: "bar",
            data: {
                labels: items.map(labelFn),
                datasets: series.map((s) => ({
                    label: s.label,
                    data: items.map((r) => {
                        const v = s.valueFn(r);
                        return v === null || v === undefined ? null : Number(v) || 0;
                    }),
                    backgroundColor: s.color,
                    borderRadius: 4,          // extremo de dato redondeado
                    maxBarThickness: 18,
                    categoryPercentage: series.length > 1 ? 0.72 : 0.62,
                    barPercentage: 0.9,
                })),
            },
            options: {
                indexAxis: "y", responsive: true, maintainAspectRatio: false,
                plugins: {
                    // Leyenda solo con >1 serie (con 1, el título del card ya la nombra).
                    legend: { display: series.length > 1, position: "bottom" },
                    tooltip: { callbacks: { label: (c) => `${series.length > 1 ? c.dataset.label + ": " : ""}${fmtVal(c.parsed.x, dec)}` } },
                },
                scales: {
                    x: { grid: { color: GRID_COLOR }, border: { display: false }, ticks: { callback: fmtTick } },
                    y: {
                        grid: { display: false }, border: { display: false },
                        ticks: {
                            autoSkip: false,
                            // Nombres largos de campaña truncados en el eje (el
                            // tooltip muestra el nombre completo).
                            callback(value) {
                                const etiqueta = this.getLabelForValue(value);
                                return etiqueta.length > 26 ? etiqueta.slice(0, 25) + "…" : etiqueta;
                            },
                        },
                    },
                },
            },
        });
    }

    // Tabla genérica de 4 columnas (label, costo, tokens, llamadas).
    function renderTablaCostos(tbodyId, items, labelFn) {
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;
        if (!items.length) { tbody.innerHTML = `<tr><td colspan="4" class="text-muted text-center">Sin datos</td></tr>`; return; }
        tbody.innerHTML = items.map((r) => `
            <tr>
                <td>${escapeHtml(labelFn(r))}</td>
                <td class="text-end">${fmtUSD(r.costo_usd)}</td>
                <td class="text-end">${fmtNum(r.total_tokens)}</td>
                <td class="text-end">${fmtNum(r.llamadas)}</td>
            </tr>`).join("");
    }

    // -- Exportar a CSV --------------------------------------------------------
    // Los agregados de la página ya están completos en memoria, así que el CSV
    // se arma en el navegador (no hay endpoint de export). Separador coma y BOM
    // para que Excel lo abra con acentos y sin pegar todo en una columna.
    function descargarCSV(nombreArchivo, encabezados, filas) {
        const celda = (v) => {
            const txt = String(v ?? "");
            return /[",;\n]/.test(txt) ? `"${txt.replace(/"/g, '""')}"` : txt;
        };
        const csv = [encabezados, ...filas].map((f) => f.map(celda).join(",")).join("\r\n");
        const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = nombreArchivo;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    }

    // -- Ordenamiento de tablas ------------------------------------------------
    // Hace clickeables los <th data-sort="clave"> de la tabla que contiene al
    // tbody. Primer click: DESC (lo útil en tablas de costos); un th con
    // data-dir="asc" arranca ascendente (columnas de texto/fecha). Dibuja el
    // caret indicador y llama a onCambio(clave, dir) en cada click.
    function bindOrdenable(tbodyId, onCambio) {
        const tabla = document.getElementById(tbodyId)?.closest("table");
        if (!tabla) return;
        const ths = [...tabla.querySelectorAll("th[data-sort]")];
        let activo = null;
        let dir = "desc";
        ths.forEach((th) => {
            th.classList.add("th-ordenable");
            th.setAttribute("role", "button");
            if (!th.title) th.title = "Ordenar por esta columna";
            th.addEventListener("click", () => {
                const clave = th.dataset.sort;
                if (activo === clave) dir = dir === "asc" ? "desc" : "asc";
                else { activo = clave; dir = th.dataset.dir || "desc"; }
                ths.forEach((otro) => otro.querySelector(".sort-caret")?.remove());
                th.insertAdjacentHTML("beforeend",
                    `<i class="bi ${dir === "asc" ? "bi-caret-up-fill" : "bi-caret-down-fill"} sort-caret ms-1"></i>`);
                onCambio(clave, dir);
            });
        });
    }

    // Copia ordenada de items por accessor (numérico o texto; null/vacío al final).
    function ordenarItems(items, accessor, dir) {
        const mult = dir === "asc" ? 1 : -1;
        return [...items].sort((a, b) => {
            const va = accessor(a), vb = accessor(b);
            const nulA = va === null || va === undefined || va === "";
            const nulB = vb === null || vb === undefined || vb === "";
            if (nulA && nulB) return 0;
            if (nulA) return 1;
            if (nulB) return -1;
            const na = Number(va), nb = Number(vb);
            if (!isNaN(na) && !isNaN(nb)) return (na - nb) * mult;
            return String(va).localeCompare(String(vb), "es", { sensitivity: "base", numeric: true }) * mult;
        });
    }

    // Tabla ordenable con datos en memoria: recuerda los items de la última carga
    // y re-renderiza ordenado al click en los th. accessors: {clave: (item)=>valor}
    // (las claves matchean el data-sort de cada th); renderFn(items) repinta el
    // tbody. La página llama a setItems(items) en cada carga de datos.
    function tablaOrdenable(tbodyId, accessors, renderFn) {
        let items = [];
        let clave = null, dir = "desc";
        function pintar() {
            const acc = clave && accessors[clave];
            renderFn(acc ? ordenarItems(items, acc, dir) : items);
        }
        bindOrdenable(tbodyId, (c, d) => { clave = c; dir = d; pintar(); });
        return { setItems(nuevos) { items = nuevos || []; pintar(); } };
    }

    // -- Filtro de fechas con rangos rápidos ----------------------------------
    function isoLocal(d) { return d.toISOString().slice(0, 10); }

    function setRango(tipo) {
        const hoy = new Date();
        let desde;
        if (tipo === "ytd") desde = new Date(hoy.getFullYear(), 0, 1);
        else if (tipo === "mes") desde = new Date(hoy.getFullYear(), hoy.getMonth(), 1);
        else { desde = new Date(hoy); desde.setMonth(desde.getMonth() - 12); } // 12m
        document.getElementById("f-desde").value = isoLocal(desde);
        document.getElementById("f-hasta").value = isoLocal(hoy);
    }

    function setEstado(msg) {
        const el = document.getElementById("estado-resultado");
        if (el) el.textContent = msg || "";
    }

    // Query base con el rango de fechas del filtro superior.
    function paramsFecha(nombreDesde = "desde", nombreHasta = "hasta") {
        const params = new URLSearchParams();
        const desde = document.getElementById("f-desde").value;
        const hasta = document.getElementById("f-hasta").value;
        if (desde) params.set(nombreDesde, desde);
        if (hasta) params.set(nombreHasta, hasta);
        return params;
    }

    window.UsoIA = {
        apiFetch, apiPost, fmtUSD, fmtNum, fmtPct, fmtTokens, escapeHtml, featLabel, featDesc,
        PALETTE, colorFor, descargarCSV,
        destruir, renderMesApilado, renderDoughnut, renderLinea, renderBarras, renderBarrasH, renderTablaCostos,
        bindOrdenable, ordenarItems, tablaOrdenable,
        setRango, setEstado, paramsFecha,
    };
})();
