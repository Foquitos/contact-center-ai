/* Gastos y Logs de IA (página unificada, grupo "auditorias").
 *
 * Tres pestañas sobre un mismo rango de fechas (filtro superior):
 *  - Resumen:      KPIs + gráficos de /api/uso-ia/dashboard?grupo=auditorias
 *                  (libro pagina_web.vw_IA_Uso_Costos; excluye chatbot, que
 *                  tiene su propia página).
 *  - Solicitudes:  corrida por corrida de /api/uso-ia/logs
 *                  (calidad.AuditExecutionLog), con modelo y costo por fila.
 *  - Análisis:     costo unitario por modelo/campaña + top usuarios (mismo
 *                  payload del dashboard, bloque `analisis`).
 * Helpers compartidos en uso_ia_common.js (window.UsoIA).
 */
(function () {
    "use strict";

    const U = window.UsoIA;
    const LIMIT = 50;
    let logsOffset = 0;
    let logsTotal = 0;
    let logsOrden = null;   // clave de _LOGS_ORDEN (backend); null = default (inicio desc)
    let logsDir = "desc";

    // Tablas ordenables (U.tablaOrdenable): se registran en init() y cada carga
    // del dashboard les pasa los items con setItems (re-render ordenado al
    // click en los th con data-sort).
    const tablas = {};

    // Última respuesta del dashboard: la reusan el toggle del gráfico mensual
    // (redibuja sin volver al backend) y la exportación a CSV del detalle.
    let ultimoDashboard = {};

    // ======================================================================
    //  Resumen + Análisis (dashboard)
    // ======================================================================

    function renderKPIs(d) {
        const total = d.total || {};
        const analisis = d.analisis || {};
        const costo = Number(total.costo_usd) || 0;
        document.getElementById("kpi-costo").textContent = U.fmtUSD(costo);
        document.getElementById("kpi-tokens").textContent = U.fmtNum(total.total_tokens);
        document.getElementById("kpi-llamadas").textContent = U.fmtNum(total.llamadas);

        // Costo promedio por auditoría (solo feature 'auditoria'), POR MODO:
        // el batch cuesta la mitad por token, así que un promedio mezclado no
        // es el precio de nada. El valor grande es el sync (precio "de lista");
        // el batch va en la línea chica.
        const unit = analisis.unitario_modelo || [];
        const suma = (campo) => unit.reduce((acc, r) => acc + (Number(r[campo]) || 0), 0);
        const llamadasSync = suma("llamadas_sync");
        const llamadasBatch = suma("llamadas_batch");
        const promSync = llamadasSync ? suma("costo_sync_usd") / llamadasSync : null;
        const promBatch = llamadasBatch ? suma("costo_batch_usd") / llamadasBatch : null;
        const kpiValor = document.getElementById("kpi-costo-auditoria");
        const kpiDetalle = document.getElementById("kpi-costo-auditoria-detalle");
        if (promSync !== null) {
            kpiValor.textContent = U.fmtUSD(promSync, 4);
            kpiDetalle.textContent = promBatch !== null ? `sync · batch: ${U.fmtUSD(promBatch, 4)}` : "sync";
        } else if (promBatch !== null) {
            kpiValor.textContent = U.fmtUSD(promBatch, 4);
            kpiDetalle.textContent = "batch (-50%)";
        } else {
            kpiValor.textContent = "—";
            kpiDetalle.textContent = "";
        }

        // El batch ya viene con -50% aplicado: lo pagado en batch = lo ahorrado.
        document.getElementById("kpi-ahorro-batch").textContent = U.fmtUSD(analisis.ahorro_batch_usd);
    }

    // ======================================================================
    //  Detalle de costo por uso (feature)
    // ======================================================================
    // El doughnut "Costo por uso" contesta el reparto pero nada más: esta tabla
    // es la lectura completa (participación, costo por llamada, sync/batch y
    // tokens por tipo) y cada uso se abre en los modelos que lo componen
    // (por_feature_modelo del dashboard).

    let porFeatureModelo = {};        // feature -> filas por modelo (para las hijas)
    const usosAbiertos = new Set();   // qué usos quedaron desplegados

    function agruparPorFeature(filas) {
        const mapa = {};
        (filas || []).forEach((r) => (mapa[r.feature] = mapa[r.feature] || []).push(r));
        return mapa;
    }

    const numero = (v) => Number(v) || 0;

    // Celda de tokens: abreviada para poder comparar de un vistazo, exacta en el
    // title (son cientos de millones; el número entero no se lee).
    function celdaTokens(valor, extraClase = "") {
        return `<td class="text-end ${extraClase}" title="${U.fmtNum(valor)} tokens">${U.fmtTokens(valor)}</td>`;
    }

    // Participación sobre el costo total del rango: barrita + porcentaje. La
    // barra usa el color del uso (el mismo del doughnut) para poder saltar de
    // un gráfico a la fila sin leer la etiqueta.
    function celdaShare(costo, total, color) {
        const pct = total > 0 ? (costo / total) * 100 : 0;
        return `<td class="text-end">
            <div class="uso-share" title="${U.fmtPct(pct)} del costo del rango">
                <span class="uso-share-bar"><span style="width:${Math.min(pct, 100).toFixed(1)}%;background:${color}"></span></span>
                <span class="uso-share-txt">${U.fmtPct(pct)}</span>
            </div></td>`;
    }

    // Reparto sync/batch de las llamadas. El batch cuesta la mitad por token, así
    // que el promedio de la fila se lee distinto según cuánto batch tenga; el
    // title da el promedio de cada modo por separado.
    function celdaModo(r) {
        const sync = numero(r.llamadas_sync), batch = numero(r.llamadas_batch);
        const tot = sync + batch;
        if (!tot) return '<td class="text-end text-muted">—</td>';
        const p = (n) => Math.round((n / tot) * 100) + "%";
        const detalle = [
            r.costo_prom_sync_usd != null ? `Sincrónico: ${U.fmtNum(sync)} × ${U.fmtUSD(r.costo_prom_sync_usd, 4)}` : null,
            r.costo_prom_batch_usd != null ? `Batch: ${U.fmtNum(batch)} × ${U.fmtUSD(r.costo_prom_batch_usd, 4)}` : null,
        ].filter(Boolean).join(" · ");
        return `<td class="text-end text-nowrap" title="${U.escapeHtml(detalle)}">
            <span class="${sync ? "" : "text-muted"}">${p(sync)}</span>
            <span class="text-muted">/</span>
            <span class="${batch ? "fw-semibold text-success" : "text-muted"}">${p(batch)}</span></td>`;
    }

    function celdaCostoProm(costo, llamadas) {
        if (!llamadas) return '<td class="text-end text-muted">—</td>';
        return `<td class="text-end">${U.fmtUSD(costo / llamadas, 4)}</td>`;
    }

    function filaUso(r, total) {
        const label = U.featLabel(r.feature);
        const color = U.colorFor(label, "uso");
        const abierto = usosAbiertos.has(r.feature);
        const modelos = porFeatureModelo[r.feature] || [];
        const desc = U.featDesc(r.feature);
        const costo = numero(r.costo_usd);
        // Sin modelos no hay nada que desplegar (no debería pasar, pero la fila
        // no puede quedar invitando a un clic que no hace nada).
        const desplegable = modelos.length > 0;
        return `
            <tr class="uso-fila${abierto ? " uso-fila-abierta" : ""}${desplegable ? "" : " uso-fila-hoja"}"
                data-feature="${U.escapeHtml(r.feature)}" title="${U.escapeHtml(desc)}">
                <td class="text-nowrap">
                    ${desplegable ? `<i class="bi ${abierto ? "bi-caret-down-fill" : "bi-caret-right-fill"} uso-caret"></i>` : ""}
                    <span class="uso-punto" style="background:${color}"></span>
                    ${U.escapeHtml(label)}
                    ${desplegable ? `<span class="text-muted small ms-1">${modelos.length} ${modelos.length === 1 ? "modelo" : "modelos"}</span>` : ""}
                </td>
                <td class="text-end fw-semibold">${U.fmtUSD(costo)}</td>
                ${celdaShare(costo, total, color)}
                ${celdaCostoProm(costo, numero(r.llamadas))}
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                ${celdaModo(r)}
                ${celdaTokens(r.input_tokens)}
                ${celdaTokens(r.output_tokens)}
                ${celdaTokens(r.thoughts_tokens)}
                ${celdaTokens(r.total_tokens, "text-muted")}
            </tr>` + modelos.map((m) => filaUsoModelo(m, total, abierto)).join("");
    }

    // Fila hija: un modelo dentro de un uso. El % sigue siendo sobre el total
    // general (no sobre el uso), para poder comparar cualquier fila con cualquiera.
    function filaUsoModelo(r, total, visible) {
        const costo = numero(r.costo_usd);
        return `
            <tr class="uso-fila-hija${visible ? "" : " d-none"}" data-hija-de="${U.escapeHtml(r.feature)}">
                <td class="text-nowrap ps-4"><span class="uso-punto" style="background:${U.colorFor(r.modelo, "modelo")}"></span>${U.escapeHtml(r.modelo || "sin modelo")}</td>
                <td class="text-end">${U.fmtUSD(costo)}</td>
                ${celdaShare(costo, total, "#adb5bd")}
                ${celdaCostoProm(costo, numero(r.llamadas))}
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                ${celdaModo(r)}
                ${celdaTokens(r.input_tokens)}
                ${celdaTokens(r.output_tokens)}
                ${celdaTokens(r.thoughts_tokens)}
                ${celdaTokens(r.total_tokens, "text-muted")}
            </tr>`;
    }

    function renderTablaUso(items) {
        const tbody = document.getElementById("tabla-feature");
        const tfoot = document.getElementById("tabla-feature-total");
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="10" class="text-muted text-center">Sin datos</td></tr>`;
            tfoot.innerHTML = "";
            return;
        }
        const suma = (campo) => items.reduce((acc, r) => acc + numero(r[campo]), 0);
        const total = suma("costo_usd");
        tbody.innerHTML = items.map((r) => filaUso(r, total)).join("");

        const llamadas = suma("llamadas");
        const syncTot = suma("llamadas_sync"), batchTot = suma("llamadas_batch");
        tfoot.innerHTML = `
            <tr class="uso-total">
                <td>Total</td>
                <td class="text-end">${U.fmtUSD(total)}</td>
                <td class="text-end">100%</td>
                ${celdaCostoProm(total, llamadas)}
                <td class="text-end">${U.fmtNum(llamadas)}</td>
                ${celdaModo({ llamadas_sync: syncTot, llamadas_batch: batchTot,
                              costo_prom_sync_usd: syncTot ? suma("costo_sync_usd") / syncTot : null,
                              costo_prom_batch_usd: batchTot ? suma("costo_batch_usd") / batchTot : null })}
                ${celdaTokens(suma("input_tokens"))}
                ${celdaTokens(suma("output_tokens"))}
                ${celdaTokens(suma("thoughts_tokens"))}
                ${celdaTokens(suma("total_tokens"))}
            </tr>`;
    }

    // Abrir/cerrar un uso: las hijas ya están en el DOM (así el estado sobrevive
    // al reordenar la tabla), solo se muestran u ocultan.
    function toggleUso(fila) {
        const feature = fila.dataset.feature;
        const abierto = usosAbiertos.has(feature);
        if (abierto) usosAbiertos.delete(feature); else usosAbiertos.add(feature);
        fila.classList.toggle("uso-fila-abierta", !abierto);
        const caret = fila.querySelector(".uso-caret");
        if (caret) caret.className = `bi ${abierto ? "bi-caret-right-fill" : "bi-caret-down-fill"} uso-caret`;
        document.querySelectorAll(`#tabla-feature tr[data-hija-de="${CSS.escape(feature)}"]`)
            .forEach((tr) => tr.classList.toggle("d-none", abierto));
    }

    // CSV del detalle: una fila por uso y una por (uso, modelo), con los mismos
    // números que muestra la tabla (el rango filtrado va en el nombre del archivo).
    function exportarUsoCSV() {
        const filas = [];
        const total = (ultimoDashboard.por_feature || []).reduce((a, r) => a + numero(r.costo_usd), 0);
        const linea = (uso, modelo, r) => {
            const costo = numero(r.costo_usd), llamadas = numero(r.llamadas);
            return [uso, modelo, costo.toFixed(6), total ? ((costo / total) * 100).toFixed(2) : "0",
                    llamadas ? (costo / llamadas).toFixed(6) : "", llamadas,
                    numero(r.llamadas_sync), numero(r.costo_sync_usd).toFixed(6),
                    numero(r.llamadas_batch), numero(r.costo_batch_usd).toFixed(6),
                    numero(r.input_tokens), numero(r.output_tokens), numero(r.thoughts_tokens), numero(r.total_tokens)];
        };
        (ultimoDashboard.por_feature || []).forEach((r) => {
            filas.push(linea(U.featLabel(r.feature), "", r));
            (porFeatureModelo[r.feature] || []).forEach((m) => filas.push(linea(U.featLabel(r.feature), m.modelo || "sin modelo", m)));
        });
        const rango = ultimoDashboard.rango || {};
        U.descargarCSV(`costo_por_uso_${rango.desde || ""}_${rango.hasta || ""}.csv`,
            ["Uso", "Modelo", "Costo USD", "% del total", "Costo prom. USD", "Llamadas",
             "Llamadas sync", "Costo sync USD", "Llamadas batch", "Costo batch USD",
             "Tokens entrada", "Tokens salida", "Tokens razonamiento", "Tokens total"],
            filas);
    }

    // Celda de costo unitario de UN modo: "—" cuando ese modo no tuvo corridas
    // (distinto de $0); el tooltip dice cuántas auditorías promedia.
    function celdaProm(valor, llamadas, modoTxt) {
        if (valor === null || valor === undefined) return '<td class="text-end text-muted">—</td>';
        return `<td class="text-end fw-bold" title="Promedio de ${U.fmtNum(llamadas)} auditorías en ${modoTxt}">${U.fmtUSD(valor, 4)}</td>`;
    }

    function renderUnitarioModelo(items) {
        document.getElementById("tabla-unitario-modelo").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td><span class="badge rounded-pill" style="background:${U.colorFor(r.modelo, "modelo")}">&nbsp;</span> ${U.escapeHtml(r.modelo)}</td>
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
                ${celdaProm(r.costo_prom_sync_usd, r.llamadas_sync, "sync")}
                ${celdaProm(r.costo_prom_batch_usd, r.llamadas_batch, "batch")}
                <td class="text-end">${U.fmtNum(r.tokens_prom)}</td>
            </tr>`).join("")
            : `<tr><td colspan="6" class="text-muted text-center">Sin datos</td></tr>`;
    }

    // Costo por auditoría según nivel de razonamiento. A diferencia de la tabla por
    // modelo NO se abre en sync/batch: el nivel no cambia con el modo, y lo que se
    // quiere leer de un vistazo es cuánto pensamiento gasta cada nivel.
    function renderUnitarioNivel(items) {
        document.getElementById("tabla-unitario-nivel").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${r.nivel_razonamiento
                        ? (NIVEL_BADGE[r.nivel_razonamiento] || U.escapeHtml(r.nivel_razonamiento))
                        : '<span class="text-muted">sin nivel</span>'}</td>
                <td class="text-end">${U.fmtNum(r.auditorias)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
                <td class="text-end fw-bold">${U.fmtUSD(r.costo_unitario_usd, 4)}</td>
                <td class="text-end">${U.fmtNum(r.thoughts_prom)}</td>
                <td class="text-end text-muted">${U.fmtNum(r.output_prom)}</td>
            </tr>`).join("")
            : `<tr><td colspan="6" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function renderUnitarioCampana(items) {
        document.getElementById("tabla-unitario-campana").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.campana)}</td>
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
                ${celdaProm(r.costo_prom_sync_usd, r.llamadas_sync, "sync")}
                ${celdaProm(r.costo_prom_batch_usd, r.llamadas_batch, "batch")}
                <td class="text-end">${U.fmtNum(r.tokens_prom)}</td>
            </tr>`).join("")
            : `<tr><td colspan="6" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function renderUsuarios(items) {
        document.getElementById("tabla-usuarios").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.user_nombre || r.user_id)}${r.user_nombre ? ` <span class="text-muted small">(${U.escapeHtml(r.user_id)})</span>` : ""}</td>
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
            </tr>`).join("")
            : `<tr><td colspan="3" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function renderAnalisis(d) {
        const analisis = d.analisis || {};
        const unitCampana = analisis.unitario_campana || [];

        renderUnitarioNivel(analisis.unitario_nivel || []);
        tablas.unitModelo.setItems(analisis.unitario_modelo || []);
        tablas.unitCampana.setItems(unitCampana);
        tablas.usuarios.setItems(d.por_usuario || []);

        // Barras agrupadas: el costo unitario de cada modo por separado (el
        // batch es ~la mitad por token). Los modos usan un par FIJO de la
        // paleta (violeta/teal, validado con el validador de dataviz: CVD y
        // contraste OK) en vez de colorFor: si entraran al mapa de entidades
        // les tocarían los últimos colores libres (rojo/mostaza) y el rojo
        // lee como "error". El gráfico muestra solo el top 10 (la tabla de
        // abajo tiene todas); la altura la ajusta renderBarrasH según filas.
        U.renderBarrasH("unitCampana", "chart-unitario-campana", unitCampana.slice(0, 10),
            (r) => r.campana, null,
            {
                decimales: 4,
                series: [
                    { label: "Sincrónico", valueFn: (r) => r.costo_prom_sync_usd, color: "#6f42c1" },
                    { label: "Batch (-50%)", valueFn: (r) => r.costo_prom_batch_usd, color: "#0894ad" },
                ],
            });
    }

    // -- Segmentadores (barra superior): acotan Resumen + Actividad ----------
    // El valor de empresa/campaña es el ID; el dashboard lo espera como
    // empresa_id/campana_id y actividad como empresa/campana (mismo valor).
    function segToParams(params, empresaKey, campanaKey) {
        const empresa = document.getElementById("seg-empresa").value;
        const campana = document.getElementById("seg-campana").value;
        const modelo = document.getElementById("seg-modelo").value;
        const modo = document.getElementById("seg-modo").value;
        if (empresa) params.set(empresaKey, empresa);
        if (campana) params.set(campanaKey, campana);
        if (modelo) params.set("modelo", modelo);
        if (modo) params.set("modo", modo);
    }

    async function cargarSegmentos() {
        try {
            const d = await U.apiFetch("/api/uso-ia/segmentos");
            const opt = (val, txt) => `<option value="${U.escapeHtml(String(val))}">${U.escapeHtml(txt)}</option>`;
            document.getElementById("seg-empresa").innerHTML =
                '<option value="">Toda empresa</option>' + (d.empresas || []).map((e) => opt(e.id, e.nombre)).join("");
            document.getElementById("seg-campana").innerHTML =
                '<option value="">Toda campaña</option>' + (d.campanas || []).map((c) => opt(c.id, c.nombre)).join("");
            document.getElementById("seg-modelo").innerHTML =
                '<option value="">Todo modelo</option>' + (d.modelos || []).map((m) => opt(m, m)).join("");
        } catch (e) {
            // Best-effort: si falla, los segmentadores quedan con "Todo/a".
        }
    }

    function recargarSegmentado() {
        cargarDashboard();
        cargarActividad();
    }

    // Gráfico mensual: mismo apilado, dos dimensiones (modelo o uso). Se redibuja
    // con los datos ya cargados, sin volver a pedir nada al backend.
    function dimensionMes() {
        return document.querySelector('input[name="mes-dim"]:checked')?.value || "modelo";
    }

    function renderMesSegunDimension() {
        const porUso = dimensionMes() === "feature";
        U.renderMesApilado("mes", "chart-mes", ultimoDashboard.por_mes || [],
            (porUso ? ultimoDashboard.por_mes_feature : ultimoDashboard.por_mes_modelo) || [],
            porUso ? { campo: "feature", labelFn: U.featLabel, ns: "uso" } : { campo: "modelo", ns: "modelo" });
    }

    async function cargarDashboard() {
        const params = U.paramsFecha();
        params.set("grupo", "auditorias");
        segToParams(params, "empresa_id", "campana_id");
        U.setEstado("Cargando…");
        try {
            const d = await U.apiFetch(`/api/uso-ia/dashboard?${params.toString()}`);
            ultimoDashboard = d;
            porFeatureModelo = agruparPorFeature(d.por_feature_modelo);
            renderKPIs(d);
            renderMesSegunDimension();
            U.renderDoughnut("feature", "chart-feature", d.por_feature || [], (r) => U.featLabel(r.feature), { ns: "uso" });
            U.renderDoughnut("modelo", "chart-modelo", d.por_modelo || [], (r) => r.modelo, { ns: "modelo" });
            // Top 12 en el gráfico (más se vuelve ilegible al lado del doughnut).
            U.renderBarrasH("campana", "chart-campana", (d.por_campana || []).slice(0, 12), (r) => r.campana, (r) => r.costo_usd);
            tablas.feature.setItems(d.por_feature || []);
            tablas.modelo.setItems(d.por_modelo || []);
            renderAnalisis(d);
            U.setEstado(`Rango ${d.rango?.desde || ""} → ${d.rango?.hasta || ""}`);
        } catch (e) {
            U.setEstado("Error: " + e.message);
        }
    }

    // ======================================================================
    //  Presupuesto mensual (siempre del mes en curso, no sigue el rango)
    // ======================================================================

    function renderPresupuesto(p) {
        document.getElementById("pres-mes").textContent = p.mes ? `(${p.mes})` : "";
        const detalle = document.getElementById("pres-detalle");
        const barra = document.getElementById("pres-barra");
        const umbrales = document.getElementById("pres-umbrales");

        if (!p.configurado) {
            detalle.textContent = "Sin presupuesto configurado";
            barra.style.width = "0%";
            barra.className = "progress-bar";
            umbrales.textContent = "Configuralo para recibir avisos por mail al superar cada umbral.";
        } else {
            const pct = Number(p.pct) || 0;
            detalle.innerHTML = `<strong>${U.fmtUSD(p.gasto_usd)}</strong> de ${U.fmtUSD(p.monto_usd)} (${pct.toFixed(1)}%)`;
            barra.style.width = Math.min(pct, 100) + "%";
            // Colores de estado (semáforo), reservados para esto: no son la paleta de series.
            barra.className = "progress-bar " + (pct >= 90 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-success");
            const avisados = (p.alertas || []).map((a) => a.umbral + "%").join(", ");
            umbrales.textContent = `Avisos al ${(p.umbrales || []).join("/")}%` +
                (avisados ? ` · ya avisados este mes: ${avisados}` : "");
        }

        // Prefill del form de configuración.
        if (p.monto_usd) document.getElementById("pres-monto").value = p.monto_usd;
        if (p.umbrales?.length) document.getElementById("pres-umbrales-input").value = p.umbrales.join(",");
        if (p.destinatarios) document.getElementById("pres-destinatarios").value = p.destinatarios;
    }

    async function cargarPresupuesto() {
        try {
            renderPresupuesto(await U.apiFetch("/api/uso-ia/presupuesto"));
        } catch (e) {
            document.getElementById("pres-detalle").textContent = "Error: " + e.message;
        }
    }

    async function guardarPresupuesto(e) {
        e.preventDefault();
        const estado = document.getElementById("pres-estado");
        estado.textContent = "Guardando…";
        try {
            const p = await U.apiPost("/api/uso-ia/presupuesto", {
                monto_usd: Number(document.getElementById("pres-monto").value),
                umbrales: document.getElementById("pres-umbrales-input").value,
                destinatarios: document.getElementById("pres-destinatarios").value,
            });
            renderPresupuesto(p);
            estado.textContent = "Guardado ✓ — el chequeo corre cada hora; cada umbral avisa una sola vez por mes.";
        } catch (err) {
            estado.textContent = "Error: " + err.message;
        }
    }

    // ======================================================================
    //  Solicitudes (calidad.AuditExecutionLog vía /api/uso-ia/logs)
    // ======================================================================

    const MODO_BADGE = {
        sync: '<span class="badge bg-info-subtle text-info-emphasis border border-info-subtle">Sincrónico</span>',
        batch: '<span class="badge bg-primary-subtle text-primary-emphasis border border-primary-subtle">Batch</span>',
    };

    const STATUS_BADGE = {
        EN_CURSO: '<span class="badge bg-secondary">En curso</span>',
        EXITO: '<span class="badge bg-success">Éxito</span>',
        PARCIAL: '<span class="badge bg-warning text-dark">Parcial</span>',
        ERROR: '<span class="badge bg-danger">Error</span>',
        SIN_DATOS: '<span class="badge bg-light text-dark border">Sin datos</span>',
    };

    function origenTxt(r) {
        if (r.trigger_source === "scheduler") {
            return `<i class="bi bi-clock-history me-1"></i>${U.escapeHtml(r.scheduler_name || "Tarea programada")}`;
        }
        return '<i class="bi bi-person me-1"></i>Manual';
    }

    // El backend manda started_at/finished_at con offset UTC explícito (+00:00);
    // forzamos el timeZone de Argentina para no depender de la zona horaria del
    // navegador de quien mire la pantalla.
    function fmtFecha(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleString("es-AR", {
            timeZone: "America/Argentina/Buenos_Aires",
            day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit",
            hour12: false,
        });
    }

    function fmtDuracion(segundos) {
        if (segundos === null || segundos === undefined) return "—";
        const s = Number(segundos);
        if (isNaN(s) || s < 0) return "—";
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        return h > 0 ? `${h}h ${m}min` : `${m}min`;
    }

    function fmtEnvios(r) {
        const partes = [];
        partes.push(r.mail_enviado
            ? '<i class="bi bi-envelope-check-fill text-success" title="Mail enviado"></i>'
            : '<i class="bi bi-envelope-slash text-muted" title="Sin mail"></i>');
        partes.push(r.gsheet_enviado
            ? '<i class="bi bi-file-earmark-spreadsheet-fill text-success ms-1" title="Subido a Sheets"></i>'
            : '<i class="bi bi-file-earmark-spreadsheet text-muted ms-1" title="No subido a Sheets"></i>');
        return partes.join("");
    }

    function fmtTokens(r) {
        const total = (Number(r.input_tokens) || 0) + (Number(r.output_tokens) || 0) + (Number(r.thoughts_tokens) || 0);
        if (!total) return "—";
        return U.fmtNum(total);
    }

    function fmtCosto(r) {
        if (r.costo_usd === null || r.costo_usd === undefined) return "—";
        return U.fmtUSD(r.costo_usd, 4);
    }

    // AuditExecutionLog solo guarda IDs; el backend (/uso-ia/logs) suma los
    // campos *_nombre resolviéndolos contra Empresas/Campanas/Plantillas/nómina.
    // Si no se pudo resolver (dato viejo, ID inválido), mostramos el ID solo.
    function fmtUsuario(r) {
        if (!r.user_id) return "—";
        return r.user_nombre
            ? `${U.escapeHtml(r.user_nombre)} <span class="text-muted small">(${U.escapeHtml(r.user_id)})</span>`
            : U.escapeHtml(r.user_id);
    }

    function fmtContexto(r) {
        const empresa = r.empresa_nombre || r.empresa || "—";
        const campana = r.campana_nombre || r.campana || "—";
        const plantilla = r.plantilla_nombre || r.plantilla_id || "—";
        return `${U.escapeHtml(empresa)} / ${U.escapeHtml(campana)} / ${U.escapeHtml(plantilla)}`;
    }

    // Modelo REAL de la corrida (columna `modelo`; filas viejas caen al modelo
    // configurado hoy en la plantilla — el backend ya resolvió ese fallback).
    // El puntito usa el mismo color que ese modelo tiene en los gráficos.
    function fmtModelo(r) {
        if (!r.modelo) return "—";
        return `<span class="badge rounded-pill" style="background:${U.colorFor(r.modelo, "modelo")}">&nbsp;</span> <small>${U.escapeHtml(r.modelo)}</small>`;
    }

    // Nivel de razonamiento con el que corrió la auditoría (columna
    // `nivel_razonamiento`). Las corridas anteriores a la migración 2026-08-19b no
    // tienen nivel y se muestran como "—": NO se asumen MEDIUM, porque justo
    // corrieron con el razonamiento sin tope y mezclarlas taparía la comparación.
    const NIVEL_BADGE = {
        LOW: '<span class="badge bg-success-subtle text-success-emphasis" title="Piensa lo mínimo antes de responder">Bajo</span>',
        MEDIUM: '<span class="badge bg-primary-subtle text-primary-emphasis" title="Balance costo/calidad (nivel por defecto)">Medio</span>',
        HIGH: '<span class="badge bg-warning-subtle text-warning-emphasis" title="Razona en profundidad: ~4x el pensamiento del nivel Bajo">Alto</span>',
    };
    function fmtNivel(r) {
        if (!r.nivel_razonamiento) {
            return '<span class="text-muted" title="Corrida anterior al nivel de razonamiento configurable">—</span>';
        }
        return NIVEL_BADGE[r.nivel_razonamiento] || U.escapeHtml(r.nivel_razonamiento);
    }

    // Desglose (value_counts por operador/tipificación) como tooltip nativo:
    // "Juan: 5\nPedro: 3\n...". Se corta a 20 líneas para no armar un tooltip gigante.
    function fmtDesgloseTooltip(desglose) {
        if (!desglose) return "";
        const entries = Object.entries(desglose).sort((a, b) => b[1] - a[1]);
        const visibles = entries.slice(0, 20).map(([k, v]) => `${k}: ${v}`).join("\n");
        const resto = entries.length > 20 ? `\n… y ${entries.length - 20} más` : "";
        return visibles + resto;
    }

    // Cuando la corrida usó muestreo por operador y/o tipificación, `cantidad`
    // deja de ser el total y pasa a ser "por cada grupo" (ver
    // AuditorIA/SQL_query.py::construir_query_muestreo); "Solicitadas" ya
    // muestra el total real, y acá se marca con qué criterio se armó y el
    // desglose por grupo (tooltip).
    function fmtMuestreo(r) {
        const iconos = [];
        const desglose = r.desglose_muestreo || {};
        if (r.por_operador) {
            const tip = fmtDesgloseTooltip(desglose.operadores);
            iconos.push(`<i class="bi bi-person-badge text-primary" title="${U.escapeHtml('Por operador' + (tip ? ':\n' + tip : ''))}"></i>`);
        }
        if (r.por_tipificacion) {
            const tip = fmtDesgloseTooltip(desglose.tipificaciones);
            iconos.push(`<i class="bi bi-tags-fill text-warning" title="${U.escapeHtml('Por tipificación' + (tip ? ':\n' + tip : ''))}"></i>`);
        }
        if (r.upload_group_id) {
            iconos.push('<i class="bi bi-collection-fill text-secondary" title="Subida CSV en varias tandas, agrupadas en esta fila"></i>');
        }
        return iconos.length ? iconos.join(" ") : "—";
    }

    // Cada fila del listado es una CORRIDA, no un job de Gemini: en Batch los
    // audios se mandan en lotes de 15 MB y cada uno abre su propia fila en la
    // tabla (ver _SQL_CORRIDAS en el backend). El botón abre el detalle por lote,
    // que es donde se ve cuál falló; con un solo lote no hay nada que desplegar.
    function fmtLotes(r) {
        if (!r.run_id || !(Number(r.lotes) > 1)) return "";
        return `<button type="button" class="btn btn-sm btn-link p-0 text-decoration-none js-lotes"
                        data-run="${U.escapeHtml(r.run_id)}"
                        title="Esta corrida se mandó a Gemini en ${r.lotes} lotes. Ver el detalle de cada uno.">
                    <i class="bi bi-chevron-right"></i><span class="badge bg-secondary ms-1">${r.lotes}</span>
                </button>`;
    }

    // El motivo de una corrida fallida se ve al pasar el mouse por el estado, sin
    // tener que desplegar los lotes. Desde 2026-08-18 el backend guarda ahí el error
    // que devolvió Gemini y no solo "no se pudo procesar" (Auditor.py::_resumen_motivos).
    function fmtEstado(r) {
        const badge = STATUS_BADGE[r.status] || U.escapeHtml(r.status || "");
        if (!r.error_message) return badge;
        return `<span title="${U.escapeHtml(r.error_message)}" style="cursor: help">${badge}</span>`;
    }

    function renderTablaLogs(items) {
        const tbody = document.getElementById("tabla-logs");
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="17" class="text-center text-muted py-4">Sin corridas para estos filtros.</td></tr>';
            return;
        }
        tbody.innerHTML = items.map((r) => `
            <tr>
                <td class="text-nowrap">${fmtLotes(r)}</td>
                <td class="text-nowrap">${fmtFecha(r.started_at)}</td>
                <td>${MODO_BADGE[r.modo] || r.modo}</td>
                <td>${origenTxt(r)}</td>
                <td>${fmtUsuario(r)}</td>
                <td>${fmtContexto(r)}</td>
                <td class="text-nowrap">${fmtModelo(r)}</td>
                <td class="text-nowrap">${fmtNivel(r)}</td>
                <td class="text-end">${r.cantidad_solicitada ?? "—"}</td>
                <td>${fmtMuestreo(r)}</td>
                <td class="text-end">${r.filas_auditadas ?? "—"}</td>
                <td class="text-end ${r.filas_error ? "text-danger fw-bold" : ""}">${r.filas_error ?? 0}</td>
                <td class="text-nowrap">${fmtDuracion(r.duration_seconds)}</td>
                <td class="text-end">${fmtTokens(r)}</td>
                <td class="text-end">${fmtCosto(r)}</td>
                <td class="text-nowrap">${fmtEnvios(r)}</td>
                <td>${fmtEstado(r)}</td>
            </tr>`).join("");
    }

    // Detalle por lote de una corrida (GET /uso-ia/logs/{run_id}/lotes). Se pide
    // al desplegar y se deja cacheado en el DOM: mientras no se recargue la tabla,
    // volver a abrir la misma corrida no vuelve a pegarle al backend.
    function renderLotes(lotes) {
        const filas = lotes.map((l, i) => `
            <tr>
                <td class="text-muted">${i + 1}</td>
                <td>${STATUS_BADGE[l.status] || U.escapeHtml(l.status || "")}</td>
                <td class="text-end">${l.cantidad_solicitada ?? "—"}</td>
                <td class="text-end">${l.filas_auditadas ?? "—"}</td>
                <td class="text-end ${l.filas_error ? "text-danger fw-bold" : ""}">${l.filas_error ?? 0}</td>
                <td class="text-nowrap">${fmtFecha(l.started_at)}</td>
                <td class="text-nowrap">${fmtDuracion(l.duration_seconds)}</td>
                <td class="text-end">${fmtTokens(l)}</td>
                <td class="text-end">${fmtCosto(l)}</td>
                <td class="text-break small text-muted">
                    ${l.error_message ? `<span class="text-danger">${U.escapeHtml(l.error_message)}</span>`
                                      : U.escapeHtml(l.batch_id || "—")}
                </td>
            </tr>`).join("");
        return `
            <div class="p-3 bg-body-tertiary">
                <div class="small text-muted mb-2">
                    <i class="bi bi-box-seam me-1"></i>Lotes enviados a Gemini en esta corrida
                    (el audio no entra en un solo pedido: se parte cada 15 MB y cada lote se procesa por separado).
                </div>
                <table class="table table-sm table-borderless mb-0 align-middle">
                    <thead><tr class="text-muted small">
                        <th>#</th><th>Estado</th><th class="text-end">Solicitadas</th>
                        <th class="text-end">Auditadas</th><th class="text-end">Errores</th>
                        <th>Inicio (ARG)</th><th>Duración</th><th class="text-end">Tokens</th>
                        <th class="text-end">Costo (USD)</th><th>Lote / error</th>
                    </tr></thead>
                    <tbody>${filas}</tbody>
                </table>
            </div>`;
    }

    async function toggleLotes(boton) {
        const fila = boton.closest("tr");
        const icono = boton.querySelector("i");
        const abierta = fila.nextElementSibling?.classList.contains("fila-lotes");
        if (abierta) {
            fila.nextElementSibling.remove();
            icono.className = "bi bi-chevron-right";
            return;
        }
        const detalleTr = document.createElement("tr");
        detalleTr.className = "fila-lotes";
        detalleTr.innerHTML = '<td colspan="16" class="p-0"><div class="p-3 text-muted small">Cargando lotes…</div></td>';
        fila.after(detalleTr);
        icono.className = "bi bi-chevron-down";
        try {
            const data = await U.apiFetch(`/api/uso-ia/logs/${encodeURIComponent(boton.dataset.run)}/lotes`);
            detalleTr.innerHTML = `<td colspan="16" class="p-0">${renderLotes(data.data || [])}</td>`;
        } catch (e) {
            detalleTr.innerHTML = `<td colspan="16" class="p-0"><div class="p-3 text-danger small">`
                + `No se pudo cargar el detalle: ${U.escapeHtml(e.message)}</div></td>`;
        }
    }

    function buildLogsParams() {
        // Las fechas salen del filtro superior (compartido con el dashboard).
        const params = U.paramsFecha("fecha_desde", "fecha_hasta");
        const modo = document.getElementById("fl-modo").value;
        const nivel = document.getElementById("fl-nivel").value;
        const origen = document.getElementById("fl-origen").value;
        const estado = document.getElementById("fl-estado").value;
        const empresa = document.getElementById("fl-empresa").value.trim();
        const campana = document.getElementById("fl-campana").value.trim();
        const usuario = document.getElementById("fl-usuario").value.trim();
        if (modo) params.set("modo", modo);
        if (nivel) params.set("nivel_razonamiento", nivel);
        if (origen) params.set("trigger_source", origen);
        if (estado) params.set("status", estado);
        if (empresa) params.set("empresa", empresa);
        if (campana) params.set("campana", campana);
        if (usuario) params.set("usuario", usuario);
        // Orden server-side (claves de _LOGS_ORDEN en el backend): ordena sobre
        // TODAS las corridas que matchean los filtros, no solo la página visible.
        if (logsOrden) {
            params.set("orden", logsOrden);
            params.set("dir", logsDir);
        }
        params.set("limit", LIMIT);
        params.set("offset", logsOffset);
        return params;
    }

    function actualizarPaginacion() {
        const desde = logsTotal === 0 ? 0 : logsOffset + 1;
        const hasta = Math.min(logsOffset + LIMIT, logsTotal);
        document.getElementById("paginacion-info").textContent = `${desde}-${hasta} de ${logsTotal}`;
        document.getElementById("btn-anterior").disabled = logsOffset <= 0;
        document.getElementById("btn-siguiente").disabled = logsOffset + LIMIT >= logsTotal;
    }

    async function cargarLogs() {
        try {
            const params = buildLogsParams();
            const data = await U.apiFetch(`/api/uso-ia/logs?${params.toString()}`);
            logsTotal = Number(data.total) || 0;
            renderTablaLogs(data.data || []);
            actualizarPaginacion();
        } catch (e) {
            document.getElementById("tabla-logs").innerHTML =
                `<tr><td colspan="17" class="text-center text-danger py-4">Error cargando solicitudes: ${U.escapeHtml(e.message)}</td></tr>`;
        }
    }

    // ======================================================================
    //  Actividad (calidad.AuditExecutionLog vía /api/uso-ia/actividad)
    //  Operación, no gasto: volumen de auditorías por dimensión y en el tiempo.
    // ======================================================================

    // Estados con color semántico FIJO (reservado, no es la paleta categórica):
    // cada uno va con su etiqueta en leyenda/tooltip, nunca color solo.
    const ESTADO_INFO = {
        EXITO: { label: "Éxito", color: "#198754" },
        PARCIAL: { label: "Parcial", color: "#e0a800" },
        ERROR: { label: "Error", color: "#dc3545" },
        EN_CURSO: { label: "En curso", color: "#6c757d" },
        SIN_DATOS: { label: "Sin datos", color: "#adb5bd" },
    };
    const estadoLabel = (s) => (ESTADO_INFO[s]?.label) || s;

    // Origen (manual/programada) y modo (sync/batch): categorías chicas con color
    // explícito para no consumir slots del colorFor global (compartido con
    // modelos/features del Resumen). El par sync/batch reusa violeta/teal, igual
    // que el Análisis, para que "batch" lea igual en toda la página.
    const ORIGEN_LABEL = { manual: "Manual", scheduler: "Programada" };
    const ORIGEN_COLORS = { Manual: "#0d6efd", Programada: "#ca6510" };
    const MODO_LABEL = { sync: "Sincrónico", batch: "Batch" };
    const MODO_COLORS = { Sincrónico: "#6f42c1", Batch: "#0894ad" };

    function fmtDurProm(seg) {
        if (seg === null || seg === undefined) return "—";
        const s = Math.round(Number(seg));
        if (isNaN(s) || s < 0) return "—";
        if (s < 60) return s + "s";
        const m = Math.floor(s / 60), r = s % 60;
        return r ? `${m}m ${r}s` : `${m}m`;
    }

    function renderActKPIs(t) {
        document.getElementById("act-kpi-corridas").textContent = U.fmtNum(t.corridas);
        document.getElementById("act-kpi-auditorias").textContent = U.fmtNum(t.auditorias);
        document.getElementById("act-kpi-exito").textContent =
            t.tasa_exito === null || t.tasa_exito === undefined ? "—" : (t.tasa_exito * 100).toFixed(1) + "%";
        const errEl = document.getElementById("act-kpi-errores");
        errEl.textContent = U.fmtNum(t.errores);
        errEl.classList.toggle("text-danger", (Number(t.errores) || 0) > 0);
        document.getElementById("act-kpi-duracion").textContent = fmtDurProm(t.duracion_prom_sync_seg);
    }

    // Perfil de 24 horas (rellena las horas sin actividad con 0 para que el eje
    // muestre siempre 0h..23h y no solo las horas con corridas).
    function perfilHoras(porHora, campo) {
        return Array.from({ length: 24 }, (_, h) => {
            const f = porHora.find((r) => Number(r.hora) === h);
            return f ? Number(f[campo]) || 0 : 0;
        });
    }

    function renderActTablaCampana(items) {
        document.getElementById("tabla-act-campana").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.campana_nombre || r.campana)}</td>
                <td class="text-end">${U.fmtNum(r.corridas)}</td>
                <td class="text-end">${U.fmtNum(r.auditorias)}</td>
                <td class="text-end ${r.errores ? "text-danger" : ""}">${U.fmtNum(r.errores)}</td>
            </tr>`).join("")
            : `<tr><td colspan="4" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function renderActTablaEmpresa(items) {
        document.getElementById("tabla-act-empresa").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.empresa_nombre || r.empresa)}</td>
                <td class="text-end">${U.fmtNum(r.corridas)}</td>
                <td class="text-end">${U.fmtNum(r.auditorias)}</td>
            </tr>`).join("")
            : `<tr><td colspan="3" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function renderActTablaUsuario(items) {
        document.getElementById("tabla-act-usuario").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.user_nombre || r.user_id)}${r.user_nombre ? ` <span class="text-muted small">(${U.escapeHtml(r.user_id)})</span>` : ""}</td>
                <td class="text-end">${U.fmtNum(r.corridas)}</td>
                <td class="text-end">${U.fmtNum(r.auditorias)}</td>
            </tr>`).join("")
            : `<tr><td colspan="3" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function renderActTablaPlantilla(items) {
        document.getElementById("tabla-act-plantilla").innerHTML = items.length ? items.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.plantilla_nombre || ("Plantilla " + r.plantilla_id))}</td>
                <td class="text-end">${U.fmtNum(r.corridas)}</td>
                <td class="text-end">${U.fmtNum(r.auditorias)}</td>
            </tr>`).join("")
            : `<tr><td colspan="3" class="text-muted text-center">Sin datos</td></tr>`;
    }

    async function cargarActividad() {
        const params = U.paramsFecha();
        segToParams(params, "empresa", "campana");
        try {
            const d = await U.apiFetch(`/api/uso-ia/actividad?${params.toString()}`);
            renderActKPIs(d.total || {});

            // Serie diaria (1 medida: auditorías por día).
            const porDia = d.por_dia || [];
            U.renderLinea("actDia", "chart-act-dia", porDia.map((r) => r.dia),
                [{ label: "Auditorías", data: porDia.map((r) => Number(r.auditorias) || 0), color: U.PALETTE[0], fill: true }]);

            // Estado (color semántico fijo por etiqueta).
            const porEstado = d.por_estado || [];
            U.renderDoughnut("actEstado", "chart-act-estado", porEstado, (r) => estadoLabel(r.status),
                { valueFn: (r) => Number(r.corridas) || 0, formato: "num",
                  colors: Object.fromEntries(porEstado.map((r) => [estadoLabel(r.status), ESTADO_INFO[r.status]?.color || "#6c757d"])) });

            // Volumen por campaña / auditor (barras horizontales de cantidad).
            U.renderBarrasH("actCampana", "chart-act-campana", (d.por_campana || []).slice(0, 12),
                (r) => r.campana_nombre || r.campana, (r) => r.auditorias, { formato: "num", color: "#0d6efd", label: "Auditorías" });
            U.renderBarrasH("actUsuario", "chart-act-usuario", (d.por_usuario || []).slice(0, 12),
                (r) => r.user_nombre || r.user_id, (r) => r.auditorias, { formato: "num", color: "#198754", label: "Auditorías" });

            // Origen / modo (doughnut de corridas, colores explícitos).
            const porOrigen = (d.por_origen || []).map((r) => ({ ...r, _label: ORIGEN_LABEL[r.origen] || r.origen }));
            U.renderDoughnut("actOrigen", "chart-act-origen", porOrigen, (r) => r._label,
                { valueFn: (r) => Number(r.corridas) || 0, formato: "num", colors: ORIGEN_COLORS });
            const porModo = (d.por_modo || []).map((r) => ({ ...r, _label: MODO_LABEL[r.modo] || r.modo }));
            U.renderDoughnut("actModo", "chart-act-modo", porModo, (r) => r._label,
                { valueFn: (r) => Number(r.corridas) || 0, formato: "num", colors: MODO_COLORS });

            // Perfil horario (corridas por hora ARG, 0..23).
            U.renderBarras("actHora", "chart-act-hora",
                Array.from({ length: 24 }, (_, h) => h + "h"), perfilHoras(d.por_hora || [], "corridas"),
                { color: U.PALETTE[0] });

            tablasAct.campana.setItems(d.por_campana || []);
            tablasAct.empresa.setItems(d.por_empresa || []);
            tablasAct.usuario.setItems(d.por_usuario || []);
            tablasAct.plantilla.setItems(d.por_plantilla || []);
        } catch (e) {
            document.getElementById("act-kpi-corridas").textContent = "Error";
            U.setEstado("Error en Actividad: " + e.message);
        }
    }

    // Tablas ordenables de Actividad (se registran en init).
    const tablasAct = {};

    function cargarTodo() {
        cargarDashboard();
        cargarActividad();
        logsOffset = 0;
        cargarLogs();
    }

    // ======================================================================
    //  Init
    // ======================================================================

    document.addEventListener("DOMContentLoaded", () => {
        U.setRango("12m");

        // -- Tablas ordenables (client-side: los agregados ya están completos
        //    en memoria; las claves matchean los data-sort de los th) --------
        const num = (campo) => (r) => Number(r[campo]) || 0;
        tablas.feature = U.tablaOrdenable("tabla-feature", {
            label: (r) => U.featLabel(r.feature), costo: num("costo_usd"), pct: num("costo_usd"),
            prom: (r) => (Number(r.llamadas) ? Number(r.costo_usd) / Number(r.llamadas) : 0),
            llamadas: num("llamadas"),
            // "Sync / Batch" ordena por cuánto del uso fue batch (lo barato).
            modo: (r) => (Number(r.llamadas) ? (Number(r.llamadas_batch) || 0) / Number(r.llamadas) : 0),
            entrada: num("input_tokens"), salida: num("output_tokens"),
            razonamiento: num("thoughts_tokens"), tokens: num("total_tokens"),
        }, renderTablaUso);
        tablas.modelo = U.tablaOrdenable("tabla-modelo", {
            label: (r) => r.modelo, costo: num("costo_usd"),
            tokens: num("total_tokens"), llamadas: num("llamadas"),
        }, (items) => U.renderTablaCostos("tabla-modelo", items, (r) => r.modelo));
        tablas.unitModelo = U.tablaOrdenable("tabla-unitario-modelo", {
            modelo: (r) => r.modelo, llamadas: num("llamadas"), costo: num("costo_usd"),
            prom_sync: (r) => r.costo_prom_sync_usd, prom_batch: (r) => r.costo_prom_batch_usd,
            tokens_prom: num("tokens_prom"),
        }, renderUnitarioModelo);
        tablas.unitCampana = U.tablaOrdenable("tabla-unitario-campana", {
            campana: (r) => r.campana, llamadas: num("llamadas"), costo: num("costo_usd"),
            prom_sync: (r) => r.costo_prom_sync_usd, prom_batch: (r) => r.costo_prom_batch_usd,
            tokens_prom: num("tokens_prom"),
        }, renderUnitarioCampana);
        tablas.usuarios = U.tablaOrdenable("tabla-usuarios", {
            usuario: (r) => r.user_nombre || String(r.user_id ?? ""),
            llamadas: num("llamadas"), costo: num("costo_usd"),
        }, renderUsuarios);

        // -- Tablas ordenables de la pestaña Actividad (mismo esquema) --------
        tablasAct.campana = U.tablaOrdenable("tabla-act-campana", {
            campana: (r) => r.campana_nombre || r.campana, corridas: num("corridas"),
            auditorias: num("auditorias"), errores: num("errores"),
        }, renderActTablaCampana);
        tablasAct.empresa = U.tablaOrdenable("tabla-act-empresa", {
            empresa: (r) => r.empresa_nombre || r.empresa,
            corridas: num("corridas"), auditorias: num("auditorias"),
        }, renderActTablaEmpresa);
        tablasAct.usuario = U.tablaOrdenable("tabla-act-usuario", {
            usuario: (r) => r.user_nombre || String(r.user_id ?? ""),
            corridas: num("corridas"), auditorias: num("auditorias"),
        }, renderActTablaUsuario);
        tablasAct.plantilla = U.tablaOrdenable("tabla-act-plantilla", {
            plantilla: (r) => r.plantilla_nombre || String(r.plantilla_id ?? ""),
            corridas: num("corridas"), auditorias: num("auditorias"),
        }, renderActTablaPlantilla);

        // Desplegar/plegar un uso en sus modelos. Delegado en el tbody porque
        // renderTablaUso reescribe las filas en cada carga y reordenamiento.
        document.getElementById("tabla-feature").addEventListener("click", (e) => {
            const fila = e.target.closest("tr.uso-fila");
            if (fila) toggleUso(fila);
        });
        document.getElementById("btn-csv-feature").addEventListener("click", exportarUsoCSV);
        document.querySelectorAll('input[name="mes-dim"]').forEach((radio) =>
            radio.addEventListener("change", renderMesSegunDimension));

        // Desplegar/plegar los lotes de una corrida. Delegado en el tbody porque
        // renderTablaLogs reescribe las filas enteras en cada página y orden.
        document.getElementById("tabla-logs").addEventListener("click", (e) => {
            const boton = e.target.closest(".js-lotes");
            if (boton) toggleLotes(boton);
        });

        // Solicitudes: el orden es server-side (la tabla está paginada); el click
        // en el th recarga la página 1 con ese orden.
        U.bindOrdenable("tabla-logs", (clave, dir) => {
            logsOrden = clave;
            logsDir = dir;
            logsOffset = 0;
            cargarLogs();
        });

        // ?tab=solicitudes (redirect desde la vieja /logs_auditoria) abre esa pestaña.
        const tab = new URLSearchParams(window.location.search).get("tab");
        if (tab === "actividad" || tab === "solicitudes" || tab === "analisis") {
            const boton = document.getElementById(`tab-${tab}`);
            if (boton) bootstrap.Tab.getOrCreateInstance(boton).show();
        }

        document.getElementById("filtros-form").addEventListener("submit", (e) => {
            e.preventDefault();
            cargarTodo();
        });
        document.querySelectorAll("[data-rango]").forEach((b) =>
            b.addEventListener("click", () => { U.setRango(b.dataset.rango); cargarTodo(); }));

        document.getElementById("filtros-logs").addEventListener("submit", (e) => {
            e.preventDefault();
            logsOffset = 0;
            cargarLogs();
        });
        document.getElementById("btn-anterior").addEventListener("click", () => {
            logsOffset = Math.max(0, logsOffset - LIMIT);
            cargarLogs();
        });
        document.getElementById("btn-siguiente").addEventListener("click", () => {
            logsOffset += LIMIT;
            cargarLogs();
        });

        document.getElementById("pres-form").addEventListener("submit", guardarPresupuesto);

        // Segmentadores: al cambiar cualquiera, recargar Resumen + Actividad (no
        // las Solicitudes, que tienen sus propios filtros). "Limpiar" los resetea.
        ["seg-empresa", "seg-campana", "seg-modelo", "seg-modo"].forEach((id) =>
            document.getElementById(id).addEventListener("change", recargarSegmentado));
        document.getElementById("seg-limpiar").addEventListener("click", () => {
            ["seg-empresa", "seg-campana", "seg-modelo", "seg-modo"].forEach((id) => { document.getElementById(id).value = ""; });
            recargarSegmentado();
        });

        cargarSegmentos();
        cargarTodo();
        cargarPresupuesto();
    });
})();
