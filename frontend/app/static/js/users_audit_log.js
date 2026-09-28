/* Tabla filtrable/ordenable/paginada del log RBAC (pagina_web.RbacAuditLog),
 * vía proxy Flask /api/admin/audit-log -> FastAPI GET /roles/audit_log.
 * El backend ya enriquece cada fila con actor_nombre / entity_nombre y resuelve
 * los role_ids del detail a nombres (roles_nombres).
 */
(function () {
    "use strict";

    const LIMIT = 50;
    const csrfToken = window.AUDIT_LOG_CSRF || "";
    let offset = 0;
    let total = 0;
    let orden = "fecha";
    let dir = "desc";

    const ACTION_LABEL = {
        user_create: "Alta de usuario",
        user_password_reset: "Blanqueo de contraseña",
        user_delete: "Borrado de usuario",
        user_roles_set: "Cambio de roles",
        bulk_role_assign: "Alta masiva",
        role_create: "Alta de rol",
        role_update: "Edición de rol",
        role_delete: "Borrado de rol",
        role_impersonate: "Simulación de rol",
    };
    const ACTION_BADGE = {
        user_create: "bg-success",
        user_password_reset: "bg-warning text-dark",
        user_delete: "bg-danger",
        user_roles_set: "bg-info text-dark",
        bulk_role_assign: "bg-success-subtle text-success-emphasis border border-success-subtle",
        role_create: "bg-primary",
        role_update: "bg-primary-subtle text-primary-emphasis border border-primary-subtle",
        role_delete: "bg-danger-subtle text-danger-emphasis border border-danger-subtle",
        role_impersonate: "bg-secondary",
    };
    const ENTITY_ICON = { user: "bi-person", role: "bi-shield-lock" };

    function escapeHtml(s) {
        return String(s ?? "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    function fmtFecha(iso) {
        if (!iso) return "—";
        // created_at ya viene en hora Argentina (SYSDATETIME del servidor), sin
        // sufijo de zona: se muestra tal cual, sin reinterpretar el navegador.
        const [fecha, hora] = String(iso).split("T");
        if (!hora) return iso;
        return `${fecha.split("-").reverse().join("/")} ${hora.substring(0, 8)}`;
    }

    // Actor: nombre en negrita + documento chico debajo (fallback al número).
    function fmtActor(r) {
        const doc = r.actor_documento;
        if (r.actor_nombre) {
            return `<div>${escapeHtml(r.actor_nombre)}</div><div class="text-muted small">${doc}</div>`;
        }
        return String(doc ?? "—");
    }

    // Entidad: icono según tipo + nombre (rol o persona afectada); fallback tipo #id.
    function fmtEntidad(r) {
        const icon = ENTITY_ICON[r.entity_type] || "bi-box";
        const tipoTxt = r.entity_type === "user" ? "Usuario" : r.entity_type === "role" ? "Rol" : escapeHtml(r.entity_type || "");
        let nombre;
        if (r.entity_nombre) {
            nombre = escapeHtml(r.entity_nombre);
        } else if (r.entity_id != null) {
            nombre = `<span class="text-muted">#${r.entity_id}</span>`;
        } else {
            return "—";
        }
        const sub = (r.entity_type === "user" && r.entity_id != null)
            ? `<div class="text-muted small">${r.entity_id}</div>` : "";
        return `<div><i class="bi ${icon} me-1 text-muted"></i>${nombre}</div>` +
               `<div class="text-muted small">${tipoTxt}</div>${sub}`;
    }

    // Detalle legible por tipo de acción (el backend ya resolvió roles_nombres).
    function fmtDetalle(r) {
        const d = r.detail;
        switch (r.action) {
            case "user_create":
                return d && d.origen === "bulk"
                    ? '<span class="text-muted small">Vía alta masiva</span>'
                    : '<span class="text-muted small">Alta individual</span>';
            case "user_password_reset":
                return '<span class="text-muted small">Clave = documento, cambio forzado</span>';
            case "user_delete":
                return '<span class="text-muted small">Se borró acceso y roles</span>';
            case "user_roles_set": {
                if (!d) return "—";
                const nombres = d.roles_nombres || [];
                if (!nombres.length) return '<span class="text-muted small">Sin roles (usuario básico)</span>';
                return nombres.map((n) => `<span class="badge bg-light text-dark border me-1">${escapeHtml(n)}</span>`).join("");
            }
            case "bulk_role_assign": {
                if (!d) return "—";
                const partes = [];
                if (d.documentos != null) partes.push(`${d.documentos} docs`);
                if (d.creados) partes.push(`creados: ${d.creados}`);
                if (d.solo_rol) partes.push(`solo rol: ${d.solo_rol}`);
                if (d.sin_cambios) partes.push(`sin cambios: ${d.sin_cambios}`);
                if (d.fallos) partes.push(`<span class="text-danger">fallos: ${d.fallos}</span>`);
                return `<span class="small">${partes.join(" · ")}</span>`;
            }
            case "role_create":
            case "role_delete":
            case "role_impersonate":
                // El nombre del rol ya se muestra en la columna Entidad.
                return "—";
            case "role_update": {
                if (!d) return "—";
                const props = (d.permisos_propios_despues || []).length;
                return `<span class="text-muted small">${props} permiso(s) propio(s)` +
                       `${d.parent_role_id ? ", hereda de #" + d.parent_role_id : ""}</span>`;
            }
            default:
                if (!d) return "—";
                if (typeof d === "string") return escapeHtml(d);
                return `<span class="text-muted small">${escapeHtml(JSON.stringify(d))}</span>`;
        }
    }

    function renderTabla(rows) {
        const tbody = document.getElementById("tabla-audit-log");
        if (!rows.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-4">Sin resultados para estos filtros.</td></tr>';
            return;
        }
        tbody.innerHTML = rows.map((r) => `
            <tr>
                <td class="text-nowrap">${fmtFecha(r.created_at)}</td>
                <td>${fmtActor(r)}</td>
                <td><span class="badge ${ACTION_BADGE[r.action] || 'bg-secondary'}">${escapeHtml(ACTION_LABEL[r.action] || r.action)}</span></td>
                <td>${fmtEntidad(r)}</td>
                <td>${fmtDetalle(r)}</td>
            </tr>`).join("");
    }

    function actualizarPaginacion() {
        const desde = total === 0 ? 0 : offset + 1;
        const hasta = Math.min(offset + LIMIT, total);
        document.getElementById("audit-paginacion-info").textContent = `${desde}-${hasta} de ${total}`;
        document.getElementById("audit-btn-anterior").disabled = offset <= 0;
        document.getElementById("audit-btn-siguiente").disabled = offset + LIMIT >= total;
    }

    function buildParams() {
        const params = new URLSearchParams({ orden, dir, limit: LIMIT, offset });
        const desde = document.getElementById("fl-desde").value;
        const hasta = document.getElementById("fl-hasta").value;
        const action = document.getElementById("fl-action").value;
        const entityType = document.getElementById("fl-entity-type").value;
        const actor = document.getElementById("fl-actor").value;
        if (desde) params.set("fecha_desde", desde);
        if (hasta) params.set("fecha_hasta", hasta);
        if (action) params.set("action", action);
        if (entityType) params.set("entity_type", entityType);
        if (actor) params.set("actor", actor);
        return params;
    }

    async function cargar() {
        const tbody = document.getElementById("tabla-audit-log");
        try {
            const resp = await fetch(`/api/admin/audit-log?${buildParams().toString()}`, {
                headers: { "X-CSRFToken": csrfToken },
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
            total = Number(data.total) || 0;
            renderTabla(data.data || []);
            actualizarPaginacion();
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger py-4">Error cargando el log: ${escapeHtml(e.message)}</td></tr>`;
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

        document.getElementById("filtros-form").addEventListener("submit", (e) => {
            e.preventDefault();
            offset = 0;
            cargar();
        });

        document.getElementById("audit-btn-anterior").addEventListener("click", () => {
            offset = Math.max(0, offset - LIMIT);
            cargar();
        });
        document.getElementById("audit-btn-siguiente").addEventListener("click", () => {
            offset += LIMIT;
            cargar();
        });

        cargar();
    });
})();
