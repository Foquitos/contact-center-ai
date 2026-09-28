/* Cupos de Auditoría — pantalla del gerente de operaciones.
 *
 * Una fila por campaña con el cupo mensual, lo consumido y —lo que motivó la
 * pantalla— cuánto puede gastar como máximo si consume el cupo entero.
 * Los datos salen de /api/cuotas/ (proxy Flask -> FastAPI /cuotas/).
 */
(function () {
    const { apiFetch, fmtUSD, fmtNum, escapeHtml } = window.UsoIA;

    // CSRFProtect exige el token en toda escritura; sin este header el PUT vuelve
    // con "Bad Request: The CSRF token is missing" antes de llegar al backend.
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

    let datos = { campanas: [], totales: {} };
    let modalCupo, modalDetalle;

    const $ = (id) => document.getElementById(id);
    const mesElegido = () => $("f-mes").value;

    /* -------------------------------------------------------------- helpers */

    // "sin tope" no es lo mismo que 0: el cupo vacío mide sin bloquear.
    const num = (v) => (v === null || v === undefined ? '<span class="text-muted">—</span>' : fmtNum(v));
    const usd = (v) => (v === null || v === undefined ? '<span class="text-muted">—</span>' : fmtUSD(v));

    function barra(fila) {
        const consumido = fila.consumido || 0;
        if (fila.limite === null || fila.limite === undefined) {
            return `<span class="text-muted small">${fmtNum(consumido)} sin tope</span>`;
        }
        const pct = fila.limite ? Math.min(100, Math.round((consumido / fila.limite) * 100)) : 0;
        const color = pct >= 100 ? "bg-danger" : pct >= 80 ? "bg-warning" : "bg-success";
        return `
            <div class="d-flex align-items-center gap-2">
                <div class="progress flex-grow-1" style="height: 8px;" title="${pct}% del cupo">
                    <div class="progress-bar ${color}" style="width: ${pct}%"></div>
                </div>
                <span class="small text-nowrap">${fmtNum(consumido)} / ${fmtNum(fila.limite)}</span>
            </div>`;
    }

    function filtrar(filas) {
        const texto = ($("buscar").value || "").trim().toLowerCase();
        const soloConCupo = $("solo-con-cupo").checked;
        return filas.filter((f) => {
            if (soloConCupo && (f.limite === null || f.limite === undefined)) return false;
            if (!texto) return true;
            return (`${f.empresa} ${f.campana}`).toLowerCase().includes(texto);
        });
    }

    /* -------------------------------------------------------------- render */

    function renderKpis() {
        const t = datos.totales || {};
        const tarjetas = [
            ["Campañas con cupo", fmtNum(t.campanas_con_cupo), "bi-sliders", ""],
            ["Cupo total del mes", fmtNum(t.cupo_total), "bi-clipboard-check", "auditorías"],
            ["Consumido", fmtNum(t.consumido_total), "bi-activity", "auditorías"],
            ["Gasto máximo posible", fmtUSD(t.gasto_maximo_usd), "bi-cash-coin",
             `consumido ${fmtUSD(t.gasto_consumido_usd)}`],
        ];
        $("kpis").innerHTML = tarjetas.map(([titulo, valor, icono, pie]) => `
            <div class="col-6 col-lg-3">
                <div class="card shadow-sm h-100">
                    <div class="card-body py-3">
                        <div class="text-muted small"><i class="bi ${icono} me-1"></i>${titulo}</div>
                        <div class="fs-4 fw-semibold">${valor}</div>
                        <div class="small text-muted">${pie}</div>
                    </div>
                </div>
            </div>`).join("");
    }

    function renderTabla() {
        const filas = filtrar(datos.campanas || []);
        if (!filas.length) {
            $("cuerpo-cuotas").innerHTML =
                '<tr><td colspan="8" class="text-center text-muted py-4">No hay campañas para mostrar.</td></tr>';
            return;
        }
        $("cuerpo-cuotas").innerHTML = filas.map((f) => {
            const inactivo = f.limite !== null && f.limite !== undefined && !f.activo;
            const aviso = inactivo
                ? ' <span class="badge bg-secondary" title="El cupo está cargado pero apagado: no bloquea">pausado</span>'
                : "";
            const propio = f.costo_unitario_propio
                ? ""
                : ' <i class="bi bi-asterisk text-muted small" title="Promedio general: esta campaña todavía no tiene auditorías propias con las que calcular su costo."></i>';
            return `
            <tr>
                <td>
                    <div class="fw-semibold">${escapeHtml(f.campana)}${aviso}</div>
                    <div class="small text-muted">${escapeHtml(f.empresa)}</div>
                </td>
                <td class="text-end">${num(f.limite)}</td>
                <td class="text-end">${num(f.limite_usuario)}</td>
                <td>${barra(f)}</td>
                <td class="text-end">${num(f.disponible)}</td>
                <td class="text-end">${f.costo_unitario_usd ? fmtUSD(f.costo_unitario_usd, 4) : '<span class="text-muted">—</span>'}${propio}</td>
                <td class="text-end fw-semibold">${usd(f.gasto_maximo_usd)}</td>
                <td class="text-end text-nowrap">
                    <button class="btn btn-sm btn-outline-secondary" data-detalle="${f.campana_id}" title="Ver quién consumió">
                        <i class="bi bi-people"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" data-editar="${f.campana_id}" title="Editar cupo">
                        <i class="bi bi-pencil"></i>
                    </button>
                </td>
            </tr>`;
        }).join("");

        $("cuerpo-cuotas").querySelectorAll("[data-editar]").forEach((b) =>
            b.addEventListener("click", () => abrirEdicion(Number(b.dataset.editar))));
        $("cuerpo-cuotas").querySelectorAll("[data-detalle]").forEach((b) =>
            b.addEventListener("click", () => abrirDetalle(Number(b.dataset.detalle))));
    }

    /* --------------------------------------------------------------- datos */

    async function cargar() {
        $("cuerpo-cuotas").innerHTML =
            '<tr><td colspan="8" class="text-center text-muted py-4">Cargando…</td></tr>';
        const params = new URLSearchParams();
        if (mesElegido()) params.set("anio_mes", mesElegido());
        try {
            datos = await apiFetch(`/api/cuotas/?${params}`);
            renderKpis();
            renderTabla();
        } catch (e) {
            $("cuerpo-cuotas").innerHTML =
                `<tr><td colspan="8" class="text-danger text-center py-4">${escapeHtml(e.message || e)}</td></tr>`;
        }
    }

    /* ------------------------------------------------------------- edición */

    function proyeccion() {
        const fila = (datos.campanas || []).find((f) => f.campana_id === Number($("cupo-campana-id").value));
        const limite = $("cupo-limite").value === "" ? null : Number($("cupo-limite").value);
        const unitario = fila && fila.costo_unitario_usd;
        if (limite === null) {
            $("cupo-proyeccion").innerHTML =
                "Sin tope: se registra el consumo de la campaña pero no se bloquea a nadie.";
            return;
        }
        if (!unitario) {
            $("cupo-proyeccion").innerHTML =
                `Cupo de <b>${fmtNum(limite)}</b> auditorías por mes. Todavía no hay historial para estimar el gasto.`;
            return;
        }
        $("cupo-proyeccion").innerHTML =
            `Cupo de <b>${fmtNum(limite)}</b> auditorías por mes a ${fmtUSD(unitario, 4)} cada una: ` +
            `hasta <b>${fmtUSD(limite * unitario)}</b> de gasto mensual en esta campaña.`;
    }

    function abrirEdicion(campanaId) {
        const fila = (datos.campanas || []).find((f) => f.campana_id === campanaId);
        if (!fila) return;
        $("cupo-campana-id").value = campanaId;
        $("cupo-campana").textContent = `${fila.campana} (${fila.empresa})`;
        $("cupo-limite").value = fila.limite ?? "";
        $("cupo-limite-usuario").value = fila.limite_usuario ?? "";
        $("cupo-activo").checked = fila.activo !== false;
        $("cupo-nota").value = fila.nota || "";
        proyeccion();
        modalCupo.show();
    }

    async function guardar() {
        const campanaId = Number($("cupo-campana-id").value);
        const limite = $("cupo-limite").value === "" ? null : Number($("cupo-limite").value);
        const limiteUsuario = $("cupo-limite-usuario").value === "" ? null : Number($("cupo-limite-usuario").value);
        const boton = $("btn-guardar-cupo");
        boton.disabled = true;
        try {
            const r = await fetch(`/api/cuotas/${campanaId}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
                body: JSON.stringify({
                    limite: limite,
                    limite_usuario: limiteUsuario,
                    activo: $("cupo-activo").checked,
                    nota: $("cupo-nota").value || null,
                }),
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(data.detail || "No se pudo guardar el cupo.");
            modalCupo.hide();
            await cargar();
        } catch (e) {
            alert(e.message || e);
        } finally {
            boton.disabled = false;
        }
    }

    /* ------------------------------------------------------------- detalle */

    async function abrirDetalle(campanaId) {
        const fila = (datos.campanas || []).find((f) => f.campana_id === campanaId);
        $("detalle-campana").textContent = fila ? `${fila.campana} (${fila.empresa})` : "";
        $("detalle-cuerpo").innerHTML = '<div class="text-muted">Cargando…</div>';
        modalDetalle.show();
        const params = new URLSearchParams();
        if (mesElegido()) params.set("anio_mes", mesElegido());
        try {
            const data = await apiFetch(`/api/cuotas/${campanaId}/consumo?${params}`);
            if (!data.usuarios.length) {
                $("detalle-cuerpo").innerHTML =
                    '<div class="text-muted">Nadie consumió cupo de esta campaña en el mes.</div>';
                return;
            }
            const unitario = fila && fila.costo_unitario_usd;
            $("detalle-cuerpo").innerHTML = `
                <table class="table table-sm align-middle mb-0">
                    <thead class="table-light">
                        <tr>
                            <th>Usuario</th>
                            <th class="text-end">Auditorías</th>
                            <th class="text-end">De transcripción</th>
                            <th class="text-end">Pedidos</th>
                            <th class="text-end">Gasto aprox.</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.usuarios.map((u) => `
                            <tr>
                                <td>${escapeHtml(u.nombre || u.documento || "—")}</td>
                                <td class="text-end">${fmtNum(u.consumido)}</td>
                                <td class="text-end">${fmtNum(u.transcripciones)}</td>
                                <td class="text-end">${fmtNum(u.pedidos)}</td>
                                <td class="text-end">${unitario ? fmtUSD(u.consumido * unitario) : "—"}</td>
                            </tr>`).join("")}
                    </tbody>
                </table>`;
        } catch (e) {
            $("detalle-cuerpo").innerHTML = `<div class="text-danger">${escapeHtml(e.message || e)}</div>`;
        }
    }

    /* ----------------------------------------------------------------- init */

    document.addEventListener("DOMContentLoaded", () => {
        modalCupo = new bootstrap.Modal($("modal-cupo"));
        modalDetalle = new bootstrap.Modal($("modal-detalle"));

        const hoy = new Date();
        $("f-mes").value = `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, "0")}`;

        $("f-mes").addEventListener("change", cargar);
        $("btn-refrescar").addEventListener("click", cargar);
        $("buscar").addEventListener("input", renderTabla);
        $("solo-con-cupo").addEventListener("change", renderTabla);
        $("cupo-limite").addEventListener("input", proyeccion);
        $("btn-guardar-cupo").addEventListener("click", guardar);

        cargar();
    });
})();
