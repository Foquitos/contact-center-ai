/* Gastos Chatbot (IA): sección separada del consumo de Gemini del chatbot.
 * Es el único consumo NO relacionado con auditorías, por eso no comparte página
 * con "Gastos y Logs de IA". Consume el mismo endpoint /api/uso-ia/dashboard con
 * grupo=chatbot; helpers compartidos en uso_ia_common.js (window.UsoIA).
 *
 * Cada chatbot (ChatVoltara, CSV, Paygo, y los que se agreguen a futuro) se
 * identifica por extras.effective_campana del libro IA_Uso: el selector y el
 * desglose salen de los datos, no de una lista fija.
 */
(function () {
    "use strict";

    const U = window.UsoIA;

    // ¿El usuario ve costos/tokens? Con uso_ia.chatbot sí (pestaña Resumen +
    // columna Tokens); Calidad (solo chatbot.solicitudes) ve las consultas sin
    // gasto. El backend además NO devuelve tokens a esos usuarios. El flag lo
    // pone el template en el contenedor.
    const puedeCostos = document.querySelector(".uso-ia-page")?.dataset.puedeCostos === "1";

    // Nombres de chatbot vistos hasta ahora (para no vaciar el selector cuando
    // se filtra a uno solo y el backend devuelve un único nombre).
    const chatbotsConocidos = new Set();

    // Tablas ordenables (U.tablaOrdenable): se registran en init y cada carga
    // les pasa los items con setItems (re-render ordenado al click en los th).
    const tablas = {};

    function renderKPIs(total) {
        const costo = Number(total.costo_usd) || 0;
        const llamadas = Number(total.llamadas) || 0;
        document.getElementById("kpi-costo").textContent = U.fmtUSD(costo);
        document.getElementById("kpi-llamadas").textContent = U.fmtNum(llamadas);
        document.getElementById("kpi-tokens").textContent = U.fmtNum(total.total_tokens);
        document.getElementById("kpi-promedio").textContent = llamadas ? U.fmtUSD(costo / llamadas, 4) : "—";
    }

    function renderTablaMes(porMes) {
        const tbody = document.getElementById("tabla-mes");
        tbody.innerHTML = porMes.length ? porMes.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.anio_mes)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
                <td class="text-end">${U.fmtNum(r.total_tokens)}</td>
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
            </tr>`).join("")
            : `<tr><td colspan="4" class="text-muted text-center">Sin datos</td></tr>`;
    }

    function actualizarSelectorChatbots(porChatbot) {
        porChatbot.forEach((r) => chatbotsConocidos.add(r.chatbot));
        const select = document.getElementById("f-chatbot");
        const seleccionado = select.value;
        select.innerHTML = '<option value="">Todos</option>' +
            [...chatbotsConocidos].sort().map((c) =>
                `<option value="${U.escapeHtml(c)}">${U.escapeHtml(c)}</option>`).join("");
        select.value = seleccionado; // conserva la selección tras redibujar
    }

    function renderTablaChatbot(porChatbot) {
        const tbody = document.getElementById("tabla-chatbot");
        tbody.innerHTML = porChatbot.length ? porChatbot.map((r) => `
            <tr>
                <td><span class="badge rounded-pill" style="background:${U.colorFor(r.chatbot)}">&nbsp;</span> ${U.escapeHtml(r.chatbot)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
                <td class="text-end">${U.fmtNum(r.total_tokens)}</td>
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                <td class="text-end fw-bold">${r.llamadas ? U.fmtUSD((Number(r.costo_usd) || 0) / r.llamadas, 4) : "—"}</td>
            </tr>`).join("")
            : `<tr><td colspan="5" class="text-muted text-center">Sin datos</td></tr>`;
    }

    // Etiquetas de día de semana (0=lunes..6=domingo, como los devuelve el backend).
    const DOW_LABELS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

    // Rellena las horas/días sin actividad con 0 para un perfil completo.
    function perfil(items, largo, campoClave, campoValor) {
        return Array.from({ length: largo }, (_, i) => {
            const f = items.find((r) => Number(r[campoClave]) === i);
            return f ? Number(f[campoValor]) || 0 : 0;
        });
    }

    function renderTemporal(d) {
        // Serie diaria (1 medida: consultas por día).
        const porDia = d.por_dia || [];
        U.renderLinea("cbDia", "chart-cb-dia", porDia.map((r) => r.dia),
            [{ label: "Consultas", data: porDia.map((r) => Number(r.llamadas) || 0), color: U.PALETTE[0], fill: true }]);

        // Perfil por hora del día (0..23) y por día de la semana (Lun..Dom).
        U.renderBarras("cbHora", "chart-cb-hora",
            Array.from({ length: 24 }, (_, h) => h + "h"),
            perfil(d.por_hora || [], 24, "hora", "llamadas"), { color: U.PALETTE[0] });
        U.renderBarras("cbDow", "chart-cb-dow",
            DOW_LABELS, perfil(d.por_dia_semana || [], 7, "dow", "llamadas"), { color: U.PALETTE[2] });
    }

    function renderTablaUsuarios(usuarios) {
        const tbody = document.getElementById("tabla-usuarios");
        tbody.innerHTML = usuarios.length ? usuarios.map((r) => `
            <tr>
                <td>${U.escapeHtml(r.user_nombre || r.user_id)}${r.user_nombre ? ` <span class="text-muted small">(${U.escapeHtml(r.user_id)})</span>` : ""}</td>
                <td class="text-end">${U.fmtNum(r.llamadas)}</td>
                <td class="text-end">${U.fmtUSD(r.costo_usd)}</td>
            </tr>`).join("")
            : `<tr><td colspan="3" class="text-muted text-center">Sin datos</td></tr>`;
    }

    async function cargar() {
        const params = U.paramsFecha();
        params.set("grupo", "chatbot");
        const chatbot = document.getElementById("f-chatbot").value;
        if (chatbot) params.set("chatbot", chatbot);
        const usuario = document.getElementById("f-usuario").value.trim();
        if (usuario) params.set("usuario", usuario);
        U.setEstado("Cargando…");
        try {
            const d = await U.apiFetch(`/api/uso-ia/dashboard?${params.toString()}`);
            renderKPIs(d.total || {});
            U.renderMesApilado("mes", "chart-mes", d.por_mes || [], d.por_mes_modelo || [], { ns: "modelo" });
            U.renderDoughnut("chatbot", "chart-chatbot", d.por_chatbot || [], (r) => r.chatbot, { ns: "chatbot" });
            U.renderDoughnut("modelo", "chart-modelo", d.por_modelo || [], (r) => r.modelo, { ns: "modelo" });
            renderTemporal(d);
            actualizarSelectorChatbots(d.por_chatbot || []);
            tablas.chatbot.setItems(d.por_chatbot || []);
            tablas.mes.setItems(d.por_mes || []);
            tablas.usuarios.setItems(d.por_usuario || []);
            U.setEstado(`Rango ${d.rango?.desde || ""} → ${d.rango?.hasta || ""}` + (chatbot ? ` · ${chatbot}` : ""));
        } catch (e) {
            U.setEstado("Error: " + e.message);
        }
    }

    // ======================================================================
    //  Solicitudes (query_chatbots_logs vía /api/uso-ia/chatbot-logs)
    //  Cada interacción una por una: consulta, respuesta y contexto RAG.
    // ======================================================================

    const SOL_LIMIT = 25;
    let solOffset = 0, solTotal = 0, solOrden = null, solDir = "desc";

    // El backend manda fecha con offset ARG explícito (-03:00); forzamos el
    // timeZone de Argentina para no depender de la zona horaria del navegador.
    function fmtFecha(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        return d.toLocaleString("es-AR", {
            timeZone: "America/Argentina/Buenos_Aires",
            day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
        });
    }

    const truncar = (s, n) => { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

    function fmtUsuarioCell(r) {
        if (!r.user_id) return "—";
        return r.user_nombre
            ? `${U.escapeHtml(r.user_nombre)} <span class="text-muted small">(${U.escapeHtml(String(r.user_id))})</span>`
            : U.escapeHtml(String(r.user_id));
    }

    const SOL_COLSPAN = puedeCostos ? 8 : 7;

    function renderTablaSolicitudes(items) {
        const tbody = document.getElementById("tabla-solicitudes");
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="${SOL_COLSPAN}" class="text-center text-muted py-4">Sin interacciones para estos filtros.</td></tr>`;
            return;
        }
        tbody.innerHTML = items.map((r) => {
            const nf = Number(r.num_fuentes) || 0;
            // Columna Tokens solo si el usuario ve costos (Calidad no la recibe).
            let celdaTokens = "";
            if (puedeCostos) {
                const tokens = (Number(r.input_tokens) || 0) + (Number(r.output_tokens) || 0);
                celdaTokens = `<td class="text-end">${tokens ? U.fmtNum(tokens) : "—"}</td>`;
            }
            return `
            <tr class="sol-row" data-id="${r.id}" style="cursor:pointer" title="Ver detalle de la interacción">
                <td class="text-nowrap">${fmtFecha(r.fecha)}</td>
                <td>${fmtUsuarioCell(r)}</td>
                <td class="text-nowrap"><span class="badge rounded-pill" style="background:${U.colorFor(r.effective_campana || "—")}">&nbsp;</span> ${U.escapeHtml(r.effective_campana || "—")}</td>
                <td>
                    <div class="sol-texto">${U.escapeHtml(truncar(r.query_preview, 120))}</div>
                    ${r.query_condensada_preview ? `<div class="sol-texto text-muted small fst-italic" title="Consulta reescrita para buscar en la documentación"><i class="bi bi-arrow-repeat me-1"></i>${U.escapeHtml(truncar(r.query_condensada_preview, 120))}</div>` : ""}
                </td>
                <td><div class="sol-texto text-muted">${U.escapeHtml(truncar(r.response_preview, 120))}</div></td>
                <td class="text-center">${nf ? `<span class="badge bg-light text-dark border">${nf}</span>` : "—"}</td>
                ${celdaTokens}
                <td class="text-end"><i class="bi bi-chevron-right text-muted"></i></td>
            </tr>`;
        }).join("");
    }

    function buildSolParams() {
        // Fecha/chatbot/usuario salen del filtro superior (compartido con Resumen).
        const params = U.paramsFecha("fecha_desde", "fecha_hasta");
        const chatbot = document.getElementById("f-chatbot").value;
        const usuario = document.getElementById("f-usuario").value.trim();
        const q = document.getElementById("fs-q").value.trim();
        if (chatbot) params.set("chatbot", chatbot);
        if (usuario) params.set("usuario", usuario);
        if (q) params.set("q", q);
        if (solOrden) { params.set("orden", solOrden); params.set("dir", solDir); }
        params.set("limit", SOL_LIMIT);
        params.set("offset", solOffset);
        return params;
    }

    function actualizarPaginacionSol() {
        const desde = solTotal === 0 ? 0 : solOffset + 1;
        const hasta = Math.min(solOffset + SOL_LIMIT, solTotal);
        document.getElementById("sol-paginacion").textContent = `${desde}-${hasta} de ${solTotal}`;
        document.getElementById("sol-anterior").disabled = solOffset <= 0;
        document.getElementById("sol-siguiente").disabled = solOffset + SOL_LIMIT >= solTotal;
    }

    async function cargarSolicitudes() {
        try {
            const data = await U.apiFetch(`/api/uso-ia/chatbot-logs?${buildSolParams().toString()}`);
            solTotal = Number(data.total) || 0;
            renderTablaSolicitudes(data.data || []);
            actualizarPaginacionSol();
            // Selector de chatbot: los usuarios con costos ya lo llenan desde el
            // dashboard (IA_Uso); Calidad no llama ese endpoint, así que su
            // selector se puebla con la lista distinct que trae este endpoint.
            if (!puedeCostos && data.chatbots) actualizarSelectorChatbots(data.chatbots.map((c) => ({ chatbot: c })));
        } catch (e) {
            document.getElementById("tabla-solicitudes").innerHTML =
                `<tr><td colspan="${SOL_COLSPAN}" class="text-center text-danger py-4">Error cargando solicitudes: ${U.escapeHtml(e.message)}</td></tr>`;
        }
    }

    // -- Detalle (modal): consulta + respuesta completas + fuentes del contexto --
    let modalInteraccion = null;

    function bloqueTexto(titulo, icono, texto) {
        return `
            <div class="mb-3">
                <div class="fw-semibold small text-muted mb-1"><i class="bi ${icono} me-1"></i>${titulo}</div>
                <div class="border rounded p-2 sol-detalle-texto">${U.escapeHtml(texto || "—")}</div>
            </div>`;
    }

    function bloqueFuentes(fuentes, contextoCrudo) {
        if (!fuentes || !fuentes.length) {
            return contextoCrudo
                ? bloqueTexto("Contexto (sin estructurar)", "bi-file-earmark-text", contextoCrudo)
                : `<div class="text-muted small"><i class="bi bi-info-circle me-1"></i>Sin contexto registrado para esta interacción.</div>`;
        }
        // Se muestra el TÍTULO con el que se cargó el documento; el nombre interno
        // del indexador ("04_dbdoc_19.md") queda como subtítulo, que sirve para
        // rastrear el chunk pero no le dice nada a quien busca la documentación.
        const items = fuentes.map((f, i) => `
            <div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#fuente-${i}">
                        <span class="text-truncate me-2">
                            ${U.escapeHtml(f.titulo || f.archivo || "Documento")}
                            ${f.titulo && f.archivo ? `<span class="text-muted small ms-1">(${U.escapeHtml(f.archivo)})</span>` : ""}
                        </span>
                        ${f.score != null ? `<span class="badge bg-primary-subtle text-primary-emphasis border border-primary-subtle ms-auto">Score ${Number(f.score).toFixed(3)}</span>` : ""}
                    </button>
                </h2>
                <div id="fuente-${i}" class="accordion-collapse collapse" data-bs-parent="#acc-fuentes">
                    <div class="accordion-body sol-detalle-texto">${U.escapeHtml(f.contenido || "—")}</div>
                </div>
            </div>`).join("");
        return `
            <div class="fw-semibold small text-muted mb-1"><i class="bi bi-search me-1"></i>Contexto recuperado (${fuentes.length} ${fuentes.length === 1 ? "fuente" : "fuentes"})</div>
            <div class="accordion" id="acc-fuentes">${items}</div>`;
    }

    async function abrirDetalle(id) {
        const body = document.getElementById("mi-body");
        document.getElementById("mi-id").textContent = `#${id}`;
        body.innerHTML = '<div class="text-center text-muted py-4">Cargando…</div>';
        if (!modalInteraccion) modalInteraccion = new bootstrap.Modal(document.getElementById("modal-interaccion"));
        modalInteraccion.show();
        try {
            const d = await U.apiFetch(`/api/uso-ia/chatbot-logs/${id}`);
            // Los tokens solo se muestran a quien ve costos (el backend ni los manda a Calidad).
            const metaTokens = puedeCostos
                ? `<span><i class="bi bi-cpu me-1"></i>${U.fmtNum((Number(d.input_tokens) || 0) + (Number(d.output_tokens) || 0))} tokens</span>`
                : "";
            const meta = `
                <div class="d-flex flex-wrap gap-3 mb-3 small text-muted">
                    <span><i class="bi bi-clock me-1"></i>${fmtFecha(d.fecha)}</span>
                    <span><i class="bi bi-person me-1"></i>${U.escapeHtml(d.user_nombre || String(d.user_id ?? "—"))}</span>
                    <span><i class="bi bi-robot me-1"></i>${U.escapeHtml(d.effective_campana || "—")}</span>
                    ${metaTokens}
                </div>`;
            // La reescrita solo aparece cuando hubo reescritura (el backend manda
            // null si coincide con lo tipeado). Va JUSTO debajo de la consulta
            // porque es lo que explica contra qué se buscó realmente: en una
            // repregunta ("tiene costo adicional?") las dos no se parecen en nada.
            const bloqueCondensada = d.query_condensada
                ? bloqueTexto("Consulta reescrita para buscar en la documentación",
                              "bi-arrow-repeat", d.query_condensada)
                : "";
            body.innerHTML = meta
                + bloqueTexto("Consulta del usuario", "bi-question-circle", d.query)
                + bloqueCondensada
                + bloqueTexto("Respuesta del chatbot", "bi-chat-left-dots", d.response)
                + bloqueFuentes(d.fuentes, d.context);
        } catch (e) {
            body.innerHTML = `<div class="text-danger">Error cargando el detalle: ${U.escapeHtml(e.message)}</div>`;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        U.setRango("12m");

        // Tablas ordenables (las claves matchean los data-sort de los th).
        const num = (campo) => (r) => Number(r[campo]) || 0;
        tablas.chatbot = U.tablaOrdenable("tabla-chatbot", {
            chatbot: (r) => r.chatbot, costo: num("costo_usd"), tokens: num("total_tokens"),
            llamadas: num("llamadas"),
            prom: (r) => (r.llamadas ? (Number(r.costo_usd) || 0) / r.llamadas : null),
        }, renderTablaChatbot);
        tablas.mes = U.tablaOrdenable("tabla-mes", {
            mes: (r) => r.anio_mes, costo: num("costo_usd"),
            tokens: num("total_tokens"), llamadas: num("llamadas"),
        }, renderTablaMes);
        tablas.usuarios = U.tablaOrdenable("tabla-usuarios", {
            usuario: (r) => r.user_nombre || String(r.user_id ?? ""),
            llamadas: num("llamadas"), costo: num("costo_usd"),
        }, renderTablaUsuarios);

        // Solicitudes: orden server-side (tabla paginada); el click recarga página 1.
        U.bindOrdenable("tabla-solicitudes", (clave, dir) => {
            solOrden = clave; solDir = dir; solOffset = 0; cargarSolicitudes();
        });

        // El filtro superior (fecha/chatbot/usuario) recarga Resumen + Solicitudes.
        // El dashboard de costos (cargar) solo si el usuario ve costos: para
        // Calidad ese endpoint da 403, así que se omite.
        const recargarTodo = () => { if (puedeCostos) cargar(); solOffset = 0; cargarSolicitudes(); };
        document.getElementById("filtros-form").addEventListener("submit", (e) => { e.preventDefault(); recargarTodo(); });
        document.getElementById("f-chatbot").addEventListener("change", recargarTodo);
        document.querySelectorAll("[data-rango]").forEach((b) =>
            b.addEventListener("click", () => { U.setRango(b.dataset.rango); recargarTodo(); }));

        // Búsqueda de texto (solo Solicitudes) y paginación.
        document.getElementById("filtros-solicitudes").addEventListener("submit", (e) => { e.preventDefault(); solOffset = 0; cargarSolicitudes(); });
        document.getElementById("sol-anterior").addEventListener("click", () => { solOffset = Math.max(0, solOffset - SOL_LIMIT); cargarSolicitudes(); });
        document.getElementById("sol-siguiente").addEventListener("click", () => { solOffset += SOL_LIMIT; cargarSolicitudes(); });

        // Clic en una fila -> detalle en el modal.
        document.getElementById("tabla-solicitudes").addEventListener("click", (e) => {
            const tr = e.target.closest(".sol-row");
            if (tr) abrirDetalle(tr.dataset.id);
        });

        if (puedeCostos) cargar();
        cargarSolicitudes();
    });
})();
