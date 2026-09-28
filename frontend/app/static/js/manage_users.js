/* Tabla buscable/ordenable/paginada de "Cuentas Existentes" en Gestionar
 * Usuarios. Server-side (backend GET /admin/users/, vía proxy Flask
 * /api/admin/users): antes no había forma de ver todos los usuarios, solo
 * buscar por documento exacto uno por uno.
 */
(function () {
    "use strict";

    const LIMIT = 20;
    const cfg = window.MANAGE_USERS_PERMS || {};
    let offset = 0;
    let total = 0;
    let orden = "documento";
    let dir = "asc";

    function escapeHtml(s) {
        return String(s ?? "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    function accionForm(documento, action, label, btnClass, confirmMsg) {
        return `
            <form method="POST" action="${cfg.editUsersUrl}" class="d-inline" onsubmit="return confirm('${confirmMsg}');">
                <input type="hidden" name="csrf_token" value="${cfg.csrfToken}">
                <input type="hidden" name="documento" value="${documento}">
                <button type="submit" name="action" value="${action}" class="btn btn-sm ${btnClass}">${label}</button>
            </form>`;
    }

    function renderTabla(rows) {
        const tbody = document.getElementById("tabla-cuentas");
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">Sin resultados.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map((r) => {
            const claveBadge = r.must_change_password
                ? '<span class="badge bg-warning text-dark">Debe cambiarla</span>'
                : '<span class="badge bg-success-subtle text-success-emphasis border border-success-subtle">OK</span>';
            const acciones = [
                `<a href="${cfg.editUsersUrl}?edit_doc=${r.documento}" class="btn btn-sm btn-outline-primary">Editar</a>`,
            ];
            if (cfg.puedeResetear) {
                acciones.push(accionForm(r.documento, "reset_password", "Blanquear", "btn-outline-warning",
                    "¿Blanquear la contraseña del documento " + r.documento + "? Quedará igual a su documento."));
            }
            if (cfg.puedeEliminar) {
                acciones.push(accionForm(r.documento, "delete", "Eliminar", "btn-outline-danger",
                    "¿Eliminar el acceso del documento " + r.documento + "?"));
            }
            return `
                <tr>
                    <td>${r.documento}</td>
                    <td>${escapeHtml(r.nombre)}</td>
                    <td>${escapeHtml(r.campana || "—")}</td>
                    <td>${escapeHtml(r.roles || "—")}</td>
                    <td>${claveBadge}</td>
                    <td class="text-end"><div class="d-flex gap-1 justify-content-end">${acciones.join("")}</div></td>
                </tr>`;
        }).join("");
    }

    function actualizarPaginacion() {
        const desde = total === 0 ? 0 : offset + 1;
        const hasta = Math.min(offset + LIMIT, total);
        document.getElementById("cuentas-paginacion-info").textContent = `${desde}-${hasta} de ${total}`;
        document.getElementById("cuentas-btn-anterior").disabled = offset <= 0;
        document.getElementById("cuentas-btn-siguiente").disabled = offset + LIMIT >= total;
    }

    async function cargar() {
        const tbody = document.getElementById("tabla-cuentas");
        const params = new URLSearchParams({ orden, dir, limit: LIMIT, offset });
        const q = document.getElementById("fl-q").value.trim();
        if (q) params.set("q", q);
        try {
            const resp = await fetch(`/api/admin/users?${params.toString()}`, {
                headers: { "X-CSRFToken": cfg.csrfToken },
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
            total = Number(data.total) || 0;
            renderTabla(data.data || []);
            actualizarPaginacion();
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-4">Error cargando cuentas: ${escapeHtml(e.message)}</td></tr>`;
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("th[data-sort]").forEach((th) => {
            th.addEventListener("click", () => {
                const clave = th.dataset.sort;
                dir = (orden === clave && dir === "asc") ? "desc" : "asc";
                orden = clave;
                offset = 0;
                cargar();
            });
        });

        document.getElementById("form-buscar-cuentas").addEventListener("submit", (e) => {
            e.preventDefault();
            offset = 0;
            cargar();
        });

        document.getElementById("cuentas-btn-anterior").addEventListener("click", () => {
            offset = Math.max(0, offset - LIMIT);
            cargar();
        });
        document.getElementById("cuentas-btn-siguiente").addEventListener("click", () => {
            offset += LIMIT;
            cargar();
        });

        cargar();
    });
})();
