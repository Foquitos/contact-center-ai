/* Pantalla "Logs de Ingreso" (pagina_web.LoginAudit vía proxy Flask
 * /api/admin/login-log/* -> FastAPI /session/*).
 *
 * Reutiliza helpers de uso_ia_common.js (window.UsoIA): apiFetch con CSRF,
 * escapeHtml, fmtNum, colorFor/PALETTE, setRango, paramsFecha, defaults de
 * Chart.js. Los gráficos se manejan con instancias propias (registro local)
 * porque son de conteos, no de costos USD.
 *
 * Fechas: created_at/last_seen vienen en hora local Argentina (SYSDATETIME, sin
 * sufijo de zona) y el navegador de los operadores también está en Argentina,
 * así que se interpretan/muestran tal cual.
 */
(function () {
    "use strict";

    const U = window.UsoIA;
    const PROXY = "/api/admin/login-log";
    const HIST_LIMIT = 50;
    const ACTIVOS_REFRESH_MS = 45000;

    // --- Estado del historial (paginado/ordenable) ---------------------------
    let histOffset = 0;
    let histTotal = 0;
    let histOrden = "fecha";
    let histDir = "desc";
    let activosTimer = null;

    const EVENT_LABEL = { login: "Ingreso", logout: "Cierre de sesión", login_failed: "Intento fallido" };
    const EVENT_BADGE = { login: "bg-success", logout: "bg-secondary", login_failed: "bg-danger" };
    const DOW_LABEL = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]; // dow 1..7 -> índice 0..6

    // -- Formateo --------------------------------------------------------------
    function fmtFecha(iso) {
        if (!iso) return "—";
        const [fecha, hora] = String(iso).split("T");
        if (!hora) return iso;
        return `${fecha.split("-").reverse().join("/")} ${hora.substring(0, 8)}`;
    }

    function fmtHace(iso) {
        if (!iso) return "—";
        const t = new Date(iso).getTime();
        if (isNaN(t)) return "—";
        const seg = Math.max(0, Math.round((Date.now() - t) / 1000));
        if (seg < 60) return "hace instantes";
        const min = Math.round(seg / 60);
        if (min < 60) return `hace ${min} min`;
        const h = Math.round(min / 60);
        if (h < 24) return `hace ${h} h`;
        return `hace ${Math.round(h / 24)} d`;
    }

    function fmtFechaCorta(iso) {
        // "2026-07-23" -> "23/07"
        const p = String(iso).split("T")[0].split("-");
        return p.length === 3 ? `${p[2]}/${p[1]}` : iso;
    }

    // User-agent -> "Navegador · SO" legible. El orden importa (Edge/Opera
    // contienen "Chrome"; Chrome antes que Safari).
    function parseUA(ua) {
        if (!ua) return "—";
        // UA de librería/servidor (ej. la llamada interna Flask->FastAPI): no es
        // un dispositivo real. Cubre las filas viejas que quedaron con
        // "python-requests" antes del fix.
        if (/python-requests|python-urllib|curl\/|go-http|okhttp|java\/|aiohttp/i.test(ua)) return "—";
        let nav = "Otro", os = "";
        if (/edg/i.test(ua)) nav = "Edge";
        else if (/opr|opera/i.test(ua)) nav = "Opera";
        else if (/chrome|crios/i.test(ua)) nav = "Chrome";
        else if (/firefox|fxios/i.test(ua)) nav = "Firefox";
        else if (/safari/i.test(ua)) nav = "Safari";
        if (/windows/i.test(ua)) os = "Windows";
        else if (/android/i.test(ua)) os = "Android";
        else if (/iphone|ipad|ipod/i.test(ua)) os = "iOS";
        else if (/mac os|macintosh/i.test(ua)) os = "macOS";
        else if (/linux/i.test(ua)) os = "Linux";
        return os ? `${nav} · ${os}` : nav;
    }

    function usuarioCell(nombre, doc) {
        if (nombre) {
            return `<div>${U.escapeHtml(nombre)}</div><div class="text-muted small">${U.escapeHtml(doc ?? "")}</div>`;
        }
        return `<span>${U.escapeHtml(doc ?? "—")}</span>`;
    }

    // -- Gráficos (registro local; destruir antes de redibujar) ----------------
    const charts = {};
    function destroy(k) { if (charts[k]) { charts[k].destroy(); delete charts[k]; } }
    const GRID = "rgba(33, 37, 41, 0.06)";

    function renderLineaDia(porDia) {
        destroy("dia");
        const ctx = document.getElementById("chart-dia");
        if (!ctx) return;
        const labels = porDia.map((r) => fmtFechaCorta(r.dia));
        charts.dia = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        type: "line", label: "Ingresos",
                        data: porDia.map((r) => Number(r.logins) || 0),
                        borderColor: U.PALETTE[0], backgroundColor: "rgba(13,110,253,.10)",
                        fill: true, tension: .3, pointRadius: porDia.length > 60 ? 0 : 2, borderWidth: 2,
                    },
                    {
                        type: "line", label: "Usuarios únicos",
                        data: porDia.map((r) => Number(r.usuarios_unicos) || 0),
                        borderColor: U.PALETTE[2], backgroundColor: "transparent",
                        fill: false, tension: .3, pointRadius: porDia.length > 60 ? 0 : 2, borderWidth: 2,
                    },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                plugins: { legend: { position: "bottom" } },
                scales: {
                    x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 16 } },
                    y: { beginAtZero: true, grid: { color: GRID }, border: { display: false }, ticks: { precision: 0 } },
                },
            },
        });
    }

    function renderBarrasVert(key, canvasId, labels, valores, color, tooltipLabel) {
        destroy(key);
        const ctx = document.getElementById(canvasId);
        if (!ctx) return;
        charts[key] = new Chart(ctx, {
            type: "bar",
            data: { labels, datasets: [{ label: tooltipLabel, data: valores, backgroundColor: color, borderRadius: 4, maxBarThickness: 26 }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${tooltipLabel}: ${U.fmtNum(c.parsed.y)}` } } },
                scales: {
                    x: { grid: { display: false }, border: { display: false } },
                    y: { beginAtZero: true, grid: { color: GRID }, border: { display: false }, ticks: { precision: 0 } },
                },
            },
        });
    }

    function renderTopUsuarios(items) {
        destroy("top");
        const ctx = document.getElementById("chart-top");
        if (!ctx) return;
        const labels = items.map((r) => r.nombre || String(r.documento));
        const alto = Math.max(200, items.length * 30 + 40);
        if (ctx.parentElement) ctx.parentElement.style.height = alto + "px";
        charts.top = new Chart(ctx, {
            type: "bar",
            data: { labels, datasets: [{ label: "Ingresos", data: items.map((r) => Number(r.logins) || 0), backgroundColor: U.PALETTE[0], borderRadius: 4, maxBarThickness: 18 }] },
            options: {
                indexAxis: "y", responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `Ingresos: ${U.fmtNum(c.parsed.x)}` } } },
                scales: {
                    x: { beginAtZero: true, grid: { color: GRID }, border: { display: false }, ticks: { precision: 0 } },
                    y: {
                        grid: { display: false }, border: { display: false },
                        ticks: { autoSkip: false, callback(v) { const e = this.getLabelForValue(v); return e.length > 26 ? e.slice(0, 25) + "…" : e; } },
                    },
                },
            },
        });
    }

    // -- Carga de datos --------------------------------------------------------
    function setKpi(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = (val === null || val === undefined) ? "—" : U.fmtNum(val);
    }

    async function loadDashboard() {
        const params = U.paramsFecha("fecha_desde", "fecha_hasta");
        U.setEstado("Cargando…");
        try {
            const d = await U.apiFetch(`${PROXY}/dashboard?${params.toString()}`);
            const k = d.kpis || {};
            setKpi("kpi-conectados", k.conectados_ahora);
            setKpi("kpi-logins-hoy", k.logins_hoy);
            setKpi("kpi-usuarios-hoy", k.usuarios_hoy);
            setKpi("kpi-fallidos", k.fallidos_hoy);
            const prom = document.getElementById("kpi-promedio");
            if (prom) prom.textContent = k.promedio_diario != null ? U.fmtNum(k.promedio_diario) : "—";

            renderLineaDia(d.por_dia || []);

            const horas = Array.from({ length: 24 }, (_, h) => {
                const f = (d.por_hora || []).find((r) => Number(r.hora) === h);
                return f ? Number(f.logins) || 0 : 0;
            });
            renderBarrasVert("hora", "chart-hora", Array.from({ length: 24 }, (_, h) => `${h}h`), horas, U.PALETTE[4], "Ingresos");

            const dows = DOW_LABEL.map((_, i) => {
                const f = (d.por_dia_semana || []).find((r) => Number(r.dow) === i + 1);
                return f ? Number(f.logins) || 0 : 0;
            });
            renderBarrasVert("dow", "chart-dow", DOW_LABEL, dows, U.PALETTE[5], "Ingresos");

            renderTopUsuarios(d.top_usuarios || []);

            const fall = d.fallidos_por_dia || [];
            renderBarrasVert("fallidos", "chart-fallidos", fall.map((r) => fmtFechaCorta(r.dia)),
                fall.map((r) => Number(r.fallidos) || 0), U.PALETTE[6], "Fallidos");

            U.setEstado("");
        } catch (e) {
            U.setEstado("Error: " + e.message);
        }
    }

    async function loadActivos() {
        const tbody = document.getElementById("tabla-activos");
        try {
            const d = await U.apiFetch(`${PROXY}/activos`);
            const rows = d.data || [];
            // Mantener el KPI de conectados en vivo (el dashboard es más lento de refrescar).
            setKpi("kpi-conectados", d.total || 0);
            if (!rows.length) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">Nadie conectado en este momento.</td></tr>';
            } else {
                tbody.innerHTML = rows.map((r) => `
                    <tr>
                        <td>${usuarioCell(r.nombre, r.documento)}</td>
                        <td class="ip-cell">${U.escapeHtml(r.ip_address || "—")}</td>
                        <td class="ua-cell" title="${U.escapeHtml(r.user_agent || "")}">${U.escapeHtml(parseUA(r.user_agent))}</td>
                        <td class="text-nowrap small">${fmtFecha(r.login_at)}</td>
                        <td class="text-nowrap small">${fmtHace(r.last_seen)}</td>
                    </tr>`).join("");
            }
            const hint = document.getElementById("activos-hint");
            if (hint) hint.textContent = `Actualizado ${new Date().toLocaleTimeString("es-AR")} · se refresca solo`;
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-4">Error: ${U.escapeHtml(e.message)}</td></tr>`;
        }
    }

    // -- Historial -------------------------------------------------------------
    function buildHistParams() {
        const params = U.paramsFecha("fecha_desde", "fecha_hasta");
        params.set("orden", histOrden);
        params.set("dir", histDir);
        params.set("limit", HIST_LIMIT);
        params.set("offset", histOffset);
        const ev = document.getElementById("fl-event").value;
        const doc = document.getElementById("fl-documento").value;
        if (ev) params.set("event", ev);
        if (doc) params.set("documento", doc);
        return params;
    }

    function renderHistorial(rows) {
        const tbody = document.getElementById("tabla-historial");
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">Sin eventos para estos filtros.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map((r) => `
            <tr>
                <td class="text-nowrap">${fmtFecha(r.created_at)}</td>
                <td>${usuarioCell(r.nombre, r.documento)}</td>
                <td><span class="badge ${EVENT_BADGE[r.event] || "bg-secondary"}">${U.escapeHtml(EVENT_LABEL[r.event] || r.event)}</span></td>
                <td class="ip-cell">${U.escapeHtml(r.ip_address || "—")}</td>
                <td class="ua-cell" title="${U.escapeHtml(r.user_agent || "")}">${U.escapeHtml(parseUA(r.user_agent))}</td>
            </tr>`).join("");
    }

    function actualizarPaginacion() {
        const desde = histTotal === 0 ? 0 : histOffset + 1;
        const hasta = Math.min(histOffset + HIST_LIMIT, histTotal);
        document.getElementById("hist-paginacion-info").textContent = `${desde}-${hasta} de ${U.fmtNum(histTotal)}`;
        document.getElementById("hist-btn-anterior").disabled = histOffset <= 0;
        document.getElementById("hist-btn-siguiente").disabled = histOffset + HIST_LIMIT >= histTotal;
    }

    async function loadHistorial() {
        const tbody = document.getElementById("tabla-historial");
        try {
            const d = await U.apiFetch(`${PROXY}/logins?${buildHistParams().toString()}`);
            histTotal = Number(d.total) || 0;
            renderHistorial(d.data || []);
            actualizarPaginacion();
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-4">Error: ${U.escapeHtml(e.message)}</td></tr>`;
        }
    }

    // -- Init ------------------------------------------------------------------
    function recargarTodo() {
        histOffset = 0;
        loadDashboard();
        loadHistorial();
    }

    document.addEventListener("DOMContentLoaded", () => {
        U.setRango("12m");

        document.getElementById("filtros-form").addEventListener("submit", (e) => {
            e.preventDefault();
            recargarTodo();
        });
        document.querySelectorAll("[data-rango]").forEach((b) => {
            b.addEventListener("click", () => { U.setRango(b.dataset.rango); recargarTodo(); });
        });

        // Filtros del historial (no tocan los gráficos).
        document.getElementById("fl-aplicar").addEventListener("click", () => { histOffset = 0; loadHistorial(); });
        document.getElementById("fl-documento").addEventListener("keydown", (e) => {
            if (e.key === "Enter") { histOffset = 0; loadHistorial(); }
        });

        // Ordenamiento del historial (server-side, whitelist en el backend).
        document.querySelectorAll("#tabla-historial")[0].closest("table").querySelectorAll("th[data-sort]").forEach((th) => {
            th.style.cursor = "pointer";
            th.addEventListener("click", () => {
                const clave = th.dataset.sort;
                histDir = (histOrden === clave && histDir === "asc") ? "desc" : "asc";
                histOrden = clave;
                histOffset = 0;
                loadHistorial();
            });
        });

        document.getElementById("hist-btn-anterior").addEventListener("click", () => {
            histOffset = Math.max(0, histOffset - HIST_LIMIT);
            loadHistorial();
        });
        document.getElementById("hist-btn-siguiente").addEventListener("click", () => {
            histOffset += HIST_LIMIT;
            loadHistorial();
        });

        loadDashboard();
        loadHistorial();
        loadActivos();
        activosTimer = setInterval(loadActivos, ACTIVOS_REFRESH_MS);
        // Pausar el auto-refresh cuando la pestaña no está visible (ahorro).
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) { clearInterval(activosTimer); activosTimer = null; }
            else if (!activosTimer) { loadActivos(); activosTimer = setInterval(loadActivos, ACTIVOS_REFRESH_MS); }
        });
    });
})();
