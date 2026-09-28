/* Planificador — insumos editables: reparto por skill, ajustes manuales del pronóstico
 * y cortes anunciados por Hidra.
 *
 * Parte de la pantalla en archivo propio: usa window.Planificador (ver el final
 * de planificador.js) y el evento planificador:config. La tarjeta de reparto va en
 * #slot-config-reparto, la de ajustes en #card-ajustes y la de cortes de Hidra en
 * #card-avisos-corte (pestaña Plan).
 */
(function () {
  "use strict";

  const P = window.Planificador;
  if (!P) return;

  const { estado, pedir, aviso, esc, pct, num, dia, conBoton, puedeEditar } = P;

  let datosAsignacion = null;
  let skillsConfig = [];
  let formularioListo = false;

  // `aviso` de planificador.js sólo arma el HTML: acá se lo pone al lado de lo
  // que se acaba de tocar, que es donde se lo va a leer.
  function mostrar(idContenedor, texto, tipo = "warning") {
    const el = document.getElementById(idContenedor) || document.getElementById("avisos");
    if (!el) return;
    const icono = tipo === "success" ? "check-circle" : tipo === "danger" ? "x-octagon" : "exclamation-triangle";
    el.innerHTML = aviso(esc(texto), tipo, icono);
  }

  // =========================================================================
  // 1. REPARTO POR SKILL (Configuración)
  // =========================================================================

  async function cargarReparto() {
    const slot = document.getElementById("slot-config-reparto");
    if (!slot) return;
    try {
      datosAsignacion = await pedir(`asignacion?campana_id=${estado.campana}`);
      slot.hidden = false;
      pintarTarjetaReparto(datosAsignacion);
    } catch (e) {
      // Sin la tarjeta de reparto la configuración se sigue viendo.
      slot.hidden = true;
    }
  }

  function parsearPorcentaje(val) {
    if (val === "" || val === null || val === undefined) return null;
    let s = String(val).trim().replace(",", ".");
    if (s.endsWith("%")) s = s.slice(0, -1).trim();
    const n = parseFloat(s);
    if (isNaN(n)) return null;
    // "70" y "0,7" quieren decir lo mismo; un 1 exacto es el 100%.
    return n > 1.0 ? n / 100.0 : n;
  }

  function hoyIso() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function pintarTarjetaReparto(data, avisoProvisorio = false) {
    const slot = document.getElementById("slot-config-reparto");
    if (!slot) return;

    const hoy = hoyIso();
    const sinTramo = data.sin_tramo || [];
    const skills = data.skills || [];

    const avisoSinTramo = sinTramo.length > 0 ? `
      <div class="alert alert-warning py-2 px-3 small d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
        <div>
          <i class="bi bi-exclamation-triangle me-1"></i>
          <strong>${sinTramo.length} skill${sinTramo.length > 1 ? "s" : ""} con demanda y sin tramo vigente</strong>
          (${esc(sinTramo.map((s) => s.nombre).join(", "))}): quedan fuera del pronóstico.
        </div>
        ${puedeEditar ? `
          <button class="btn btn-sm btn-outline-dark" id="btn-cargar-ceros"
                  title="Precarga 0% desde hoy en esos skills; hay que guardar">Cargar 0%</button>` : ""}
      </div>` : "";

    const bannerProvisorio = avisoProvisorio ? `
      <div class="alert alert-info py-2 px-3 small mb-3">
        <i class="bi bi-info-circle me-1"></i>
        <strong>Precargado con el reparto que se ve hoy.</strong> Revisá cada porcentaje antes de guardar.
        <span class="text-muted">(provisorio: la demanda total del cliente carga de noche)</span>
      </div>` : "";

    const filasHtml = skills.map((s) => {
      const v = s.tramo_vigente;
      const vigentePct = v ? pct(v.porcentaje) : '<span class="text-warning-emphasis fw-semibold">sin tramo</span>';
      const vigenteDesde = v && v.desde ? dia(v.desde) : "—";
      const llamadas30 = s.llamadas_30d !== null && s.llamadas_30d !== undefined ? num(s.llamadas_30d) : "—";

      const camposEdicion = puedeEditar ? `
          <td style="width: 110px;">
            <input type="text" class="form-control form-control-sm text-end in-nuevo-pct"
                   placeholder="ej. 70%" data-skill="${s.skill_id}">
          </td>
          <td style="width: 140px;">
            <input type="date" class="form-control form-control-sm in-nuevo-desde"
                   value="${hoy}" data-skill="${s.skill_id}">
          </td>
          <td style="min-width: 160px;">
            <input type="text" class="form-control form-control-sm in-nuevo-nota"
                   placeholder="Nota opcional" maxlength="400" data-skill="${s.skill_id}">
          </td>` : "";

      return `
        <tr data-skill="${s.skill_id}">
          <td class="fw-semibold small">${esc(s.nombre)}</td>
          <td class="text-end small">${vigentePct}</td>
          <td class="small">${vigenteDesde}</td>
          <td class="text-end small text-muted">${llamadas30}</td>
          ${camposEdicion}
        </tr>`;
    }).join("");

    slot.innerHTML = `
      <div class="card shadow-sm">
        <div class="card-header bg-white d-flex justify-content-between align-items-center flex-wrap gap-2">
          <span class="fw-semibold">Reparto: qué parte de cada cola nos asignan</span>
          ${puedeEditar ? `
            <button class="btn btn-sm btn-primary" id="btn-guardar-reparto">
              <i class="bi bi-check-lg me-1"></i>Guardar cambios
            </button>` : ""}
        </div>
        <div class="card-body">
          ${bannerProvisorio}
          ${avisoSinTramo}
          <div id="reparto-mensajes"></div>
          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0" id="tabla-reparto">
              <thead class="table-light">
                <tr>
                  <th>Skill</th>
                  <th class="text-end">Vigente</th>
                  <th>Desde</th>
                  <th class="text-end" title="Llamadas del cliente (todos los contact centers) en los últimos 30 días">Llamadas 30 d<br><small class="fw-normal text-muted">del cliente</small></th>
                  ${puedeEditar ? '<th class="text-end">Nuevo %</th><th>Desde</th><th>Nota</th>' : ""}
                </tr>
              </thead>
              <tbody>${filasHtml}</tbody>
            </table>
          </div>
          <details class="porque mt-3">
            <summary>Por qué el reparto es por skill</summary>
            <div>
              El pronóstico es de la demanda total del cliente, y lo que nos toca se aplica
              cola por cola: hay colas que van enteras a otro contact center y otras donde
              nos toca más de la mitad. Un tramo nuevo cierra el anterior el día previo; la
              historia no se reescribe, se corrige con un tramo nuevo.
            </div>
          </details>
        </div>
      </div>`;

    const btnCeros = document.getElementById("btn-cargar-ceros");
    if (btnCeros) {
      btnCeros.addEventListener("click", () => {
        sinTramo.forEach((st) => {
          const inPct = slot.querySelector(`.in-nuevo-pct[data-skill="${st.skill_id}"]`);
          const inDesde = slot.querySelector(`.in-nuevo-desde[data-skill="${st.skill_id}"]`);
          const inNota = slot.querySelector(`.in-nuevo-nota[data-skill="${st.skill_id}"]`);
          if (inPct) inPct.value = "0%";
          if (inDesde) inDesde.value = hoy;
          if (inNota && !inNota.value) inNota.value = "0%: la cola no nos llega";
        });
        mostrar("reparto-mensajes", "Precargado. Revisalo y apretá «Guardar cambios».", "info");
      });
    }

    const btnGuardar = document.getElementById("btn-guardar-reparto");
    if (btnGuardar) btnGuardar.addEventListener("click", () => guardarReparto(slot, btnGuardar));
  }

  async function guardarReparto(slot, btnGuardar) {
    const hoy = hoyIso();
    const tramos = [];
    for (const inp of slot.querySelectorAll(".in-nuevo-pct")) {
      const val = inp.value.trim();
      if (!val) continue;
      const pctVal = parsearPorcentaje(val);
      if (pctVal === null || pctVal < 0 || pctVal > 1) {
        mostrar("reparto-mensajes", `Porcentaje inválido: «${val}». Tiene que estar entre 0 y 100%.`, "danger");
        inp.focus();
        return;
      }
      const sid = Number(inp.dataset.skill);
      const inDesde = slot.querySelector(`.in-nuevo-desde[data-skill="${sid}"]`);
      const inNota = slot.querySelector(`.in-nuevo-nota[data-skill="${sid}"]`);
      const desdeVal = inDesde ? inDesde.value : hoy;
      if (!desdeVal) {
        mostrar("reparto-mensajes", "Falta la fecha desde la que rige el tramo.", "danger");
        if (inDesde) inDesde.focus();
        return;
      }
      tramos.push({
        skill_id: sid,
        vigente_desde: desdeVal,
        porcentaje: pctVal,
        nota: inNota ? inNota.value.trim() || null : null,
      });
    }

    if (tramos.length === 0) {
      mostrar("reparto-mensajes", "No cargaste ningún porcentaje nuevo.", "warning");
      return;
    }

    await conBoton(btnGuardar, "Guardando…", async () => {
      try {
        await pedir("asignacion", {
          method: "POST",
          body: JSON.stringify({ campana_id: estado.campana, tramos }),
        });
      } catch (err) {
        mostrar("reparto-mensajes", `No se guardó: ${err.message}`, "danger");
        return;
      }
      await cargarReparto();   // repinta la tarjeta: el mensaje va después
      const cont = document.getElementById("reparto-mensajes");
      if (!cont) return;
      cont.innerHTML = `
        <div class="alert alert-success py-2 px-3 small d-flex justify-content-between align-items-center flex-wrap gap-2 mb-3">
          <div><i class="bi bi-check-circle me-1"></i>
            ${tramos.length} tramo${tramos.length > 1 ? "s" : ""} guardado${tramos.length > 1 ? "s" : ""}.
            <strong>Recalculá el plan para que lo tome.</strong></div>
          ${document.getElementById("btn-recalcular")
            ? '<button class="btn btn-sm btn-success" id="btn-recalcular-post-reparto">Recalcular ahora</button>' : ""}
        </div>`;
      const btnRec = document.getElementById("btn-recalcular-post-reparto");
      if (btnRec) btnRec.addEventListener("click", () => document.getElementById("btn-recalcular").click());
    });
  }

  // Lo llama la tarjeta de seguimiento cuando el reparto de hoy no coincide con
  // el configurado. Precarga, no guarda: el share de hoy es provisorio.
  function proponerReparto(ratio, shareInfo) {
    if (!datosAsignacion) return;
    pintarTarjetaReparto(datosAsignacion, true);

    const slot = document.getElementById("slot-config-reparto");
    if (!slot) return;
    const hoy = hoyIso();

    (datosAsignacion.skills || []).forEach((s) => {
      const v = s.tramo_vigente;
      const inPct = slot.querySelector(`.in-nuevo-pct[data-skill="${s.skill_id}"]`);
      const inDesde = slot.querySelector(`.in-nuevo-desde[data-skill="${s.skill_id}"]`);
      const inNota = slot.querySelector(`.in-nuevo-nota[data-skill="${s.skill_id}"]`);
      if (!inPct) return;
      // Una cola en 0 sigue en 0: que hoy llegue más no la trae de otro contact center.
      inPct.value = v && v.porcentaje > 0 ? `${(Math.min(1.0, v.porcentaje * ratio) * 100).toFixed(1)}%` : "0%";
      if (inDesde) inDesde.value = hoy;
      if (inNota) inNota.value = `Propuesto con el reparto de hoy (${(shareInfo.implicito_hoy * 100).toFixed(1)}%)`;
    });

    const btnTab = document.querySelector('button[data-bs-target="#tab-config"]');
    if (btnTab && window.bootstrap) window.bootstrap.Tab.getOrCreateInstance(btnTab).show();
    setTimeout(() => slot.scrollIntoView({ behavior: "smooth", block: "start" }), 200);
  }

  // =========================================================================
  // 2. AJUSTES MANUALES DEL PRONÓSTICO (pestaña Plan)
  // =========================================================================

  function contenedorMensajesAjustes() {
    const card = document.getElementById("card-ajustes");
    if (card && !document.getElementById("ajustes-mensajes")) {
      card.querySelector(".card-body").insertAdjacentHTML(
        "afterbegin", '<div id="ajustes-mensajes" class="px-3 pt-2"></div>');
    }
    return "ajustes-mensajes";
  }

  async function cargarAjustes() {
    if (!document.getElementById("card-ajustes")) return;
    try {
      const resp = await pedir(`ajustes?campana_id=${estado.campana}`);
      pintarTablaAjustes(resp.ajustes || []);
    } catch (e) {
      mostrar(contenedorMensajesAjustes(), `No se pudieron leer los ajustes: ${e.message}`, "danger");
    }
  }

  function pintarTablaAjustes(ajustes) {
    const tbody = document.querySelector("#tabla-ajustes tbody");
    if (!tbody) return;

    const nombreSkill = (id) => {
      if (!id) return '<span class="fw-semibold">Todos</span>';
      const s = skillsConfig.find((x) => x.skill_id === id);
      return s ? esc(s.nombre) : `Skill ${id}`;
    };

    tbody.innerHTML = ajustes.map((a) => `
        <tr>
          <td class="small">${nombreSkill(a.SkillID)}</td>
          <td class="small text-nowrap">${esc(String(a.Desde).slice(0, 16).replace("T", " "))}</td>
          <td class="small text-nowrap">${esc(String(a.Hasta).slice(0, 16).replace("T", " "))}</td>
          <td class="text-end small fw-semibold">${Number(a.Factor).toFixed(2)}×</td>
          <td class="small">${esc(a.Motivo)}</td>
          ${puedeEditar ? `
            <td class="text-end">
              <button class="btn btn-sm btn-outline-danger py-0 px-2 btn-baja-ajuste"
                      data-id="${a.AjusteID}" title="Deja de aplicarse; el registro queda">Desactivar</button>
            </td>` : ""}
        </tr>`).join("") ||
      `<tr><td colspan="${puedeEditar ? 6 : 5}" class="text-muted small p-3">
         No hay ajustes manuales activos.</td></tr>`;

    tbody.querySelectorAll(".btn-baja-ajuste").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await conBoton(btn, "…", async () => {
          try {
            await pedir(`ajustes/${btn.dataset.id}?campana_id=${estado.campana}`, { method: "DELETE" });
          } catch (err) {
            mostrar(contenedorMensajesAjustes(), `No se pudo desactivar: ${err.message}`, "danger");
            return;
          }
          mostrar(contenedorMensajesAjustes(), "Ajuste desactivado: deja de aplicarse en el próximo recálculo.", "success");
          await cargarAjustes();
        });
      });
    });
  }

  function inicializarFormularioAjustes() {
    const form = document.getElementById("form-nuevo-ajuste");
    if (!form) return;

    const selectSkill = document.getElementById("ajuste-skill");
    if (selectSkill && skillsConfig.length > 0) {
      selectSkill.innerHTML = '<option value="">Todos los skills</option>' +
        skillsConfig.map((s) => `<option value="${s.skill_id}">${esc(s.nombre)}</option>`).join("");
    }

    // La configuración se vuelve a cargar al recalcular o cambiar de campaña: el
    // envío se engancha una sola vez o cada carga sumaría un POST más.
    if (formularioListo) return;
    formularioListo = true;

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const cont = contenedorMensajesAjustes();
      const skillVal = document.getElementById("ajuste-skill").value;
      const desdeVal = document.getElementById("ajuste-desde").value;
      const hastaVal = document.getElementById("ajuste-hasta").value;
      const factorVal = parseFloat(String(document.getElementById("ajuste-factor").value).replace(",", "."));
      const motivoVal = document.getElementById("ajuste-motivo").value.trim();

      if (!desdeVal || !hastaVal) return mostrar(cont, "Faltan las fechas desde y hasta.", "warning");
      if (new Date(hastaVal) <= new Date(desdeVal)) return mostrar(cont, "«Hasta» tiene que ser posterior a «Desde».", "warning");
      if (isNaN(factorVal) || factorVal <= 0 || factorVal > 10) return mostrar(cont, "El factor va entre 0,01 y 10 (1,30 = +30%).", "warning");
      if (motivoVal.length < 3) return mostrar(cont, "El motivo es obligatorio: es lo que permite saber después si el ajuste estuvo bien.", "warning");

      await conBoton(document.getElementById("btn-guardar-ajuste"), "…", async () => {
        try {
          await pedir(`ajustes?campana_id=${estado.campana}`, {
            method: "POST",
            body: JSON.stringify({
              skill_id: skillVal ? Number(skillVal) : null,
              desde: desdeVal, hasta: hastaVal, factor: factorVal, motivo: motivoVal,
            }),
          });
        } catch (err) {
          mostrar(cont, `No se guardó el ajuste: ${err.message}`, "danger");
          return;
        }
        mostrar(cont, "Ajuste guardado: se aplica en el próximo recálculo.", "success");
        form.reset();
        await cargarAjustes();
      });
    });
  }

  // =========================================================================
  // 3. CORTES ANUNCIADOS POR HIDRA (pestaña Plan)
  // =========================================================================

  const ESTADOS_AVISO = {
    pendiente: ["bg-warning text-dark", "Pendiente"],
    aplicado: ["bg-success", "Aplicado"],
    descartado: ["bg-secondary", "Descartado"],
    vencido: ["bg-light text-dark border", "Ya pasó"],
    error: ["bg-danger", "No se pudo leer"],
  };

  function fechaCorta(v) {
    return v ? esc(String(v).slice(0, 16).replace("T", " ")) : "—";
  }

  async function cargarAvisosCorte() {
    const card = document.getElementById("card-avisos-corte");
    if (!card) return;
    let resp;
    try {
      resp = await pedir(`avisos-corte?campana_id=${estado.campana}&dias=30`);
    } catch (e) {
      card.hidden = true;
      return;
    }
    card.hidden = !resp.aplica;
    if (!resp.aplica) return;
    if (resp.sin_tabla) {
      mostrar("avisos-corte-mensajes", resp.detalle || "Falta la migración de avisos de corte.", "warning");
      pintarAvisosCorte([]);
      return;
    }
    const est = resp.estimacion || {};
    const nota = document.getElementById("avisos-corte-nota");
    if (nota && est.rutina && est.grande) {
      nota.textContent = `Leídos de la web de Hidra. Estimación: rutina ${est.rutina.llamadas} llamadas, ` +
        `grande ${est.grande.llamadas} (${est.rutina.origen}). La propuesta no se aplica sola: aplicarla crea un ajuste manual.`;
    }
    pintarAvisosCorte(resp.avisos || []);
  }

  function pintarAvisosCorte(avisos) {
    const tbody = document.querySelector("#tabla-avisos-corte tbody");
    if (!tbody) return;
    const pendientes = avisos.filter((a) => a.Estado === "pendiente").length;
    const contador = document.getElementById("avisos-corte-contador");
    if (contador) contador.textContent = pendientes ? `${pendientes} pendiente${pendientes > 1 ? "s" : ""}` : "";

    tbody.innerHTML = avisos.map((a) => {
      const [clase, texto] = ESTADOS_AVISO[a.Estado] || ["bg-light text-dark", a.Estado];
      const enlace = String(a.Referencia || "").startsWith("http")
        ? ` <a href="${esc(a.Referencia)}" target="_blank" rel="noopener" title="Ver el aviso"><i class="bi bi-box-arrow-up-right"></i></a>` : "";
      const usuarios = a.UsuariosAfectados ? ` · ${num(a.UsuariosAfectados)} usuarios` : "";
      const medido = a.LlamadasReales !== null && a.LlamadasReales !== undefined
        ? `<div class="text-muted small">trajo ${num(a.LlamadasReales)}</div>` : "";
      const factor = a.FactorPropuesto ? Number(a.FactorPropuesto) : null;
      let accion = "";
      if (puedeEditar && a.Estado === "pendiente") {
        accion = `
          <div class="d-flex gap-1 justify-content-end align-items-center">
            <input type="number" step="0.01" min="0.01" max="10" class="form-control form-control-sm text-end input-factor-aviso"
                   style="width: 5.5rem" value="${factor ? factor.toFixed(2) : ""}" data-id="${a.AvisoID}" title="Factor sobre el pronóstico de la ventana">
            <button class="btn btn-sm btn-primary py-0 px-2 btn-aplicar-aviso" data-id="${a.AvisoID}">Aplicar</button>
            <button class="btn btn-sm btn-outline-secondary py-0 px-2 btn-descartar-aviso" data-id="${a.AvisoID}">Descartar</button>
          </div>`;
      }
      return `
        <tr>
          <td class="small text-nowrap">${fechaCorta(a.Inicio)}<div class="text-muted">a ${fechaCorta(a.Fin)}</div></td>
          <td class="small">${esc(a.Resumen || a.Titulo || "")}${enlace}
            <div class="text-muted">${esc(a.Zonas || "")}${usuarios}${a.Programado === false ? " · <b>no programado</b>" : ""}</div></td>
          <td class="small">${a.Escala === "grande" ? '<span class="badge bg-danger">Grande</span>' : esc(a.Escala || "—")}</td>
          <td class="text-end small">${a.LlamadasExtra !== null && a.LlamadasExtra !== undefined ? num(a.LlamadasExtra) : "—"}${medido}</td>
          <td class="text-end small fw-semibold">${factor ? factor.toFixed(2) + "×" : "—"}</td>
          <td><span class="badge ${clase}">${texto}</span></td>
          ${puedeEditar ? `<td class="text-end">${accion}</td>` : ""}
        </tr>`;
    }).join("") ||
      `<tr><td colspan="${puedeEditar ? 7 : 6}" class="text-muted small p-3">
         No hay cortes anunciados en los últimos 30 días.</td></tr>`;

    tbody.querySelectorAll(".btn-aplicar-aviso").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const input = tbody.querySelector(`.input-factor-aviso[data-id="${btn.dataset.id}"]`);
        const factor = input && input.value ? parseFloat(String(input.value).replace(",", ".")) : null;
        if (factor !== null && (isNaN(factor) || factor <= 0 || factor > 10)) {
          return mostrar("avisos-corte-mensajes", "El factor va entre 0,01 y 10 (1,30 = +30%).", "warning");
        }
        await conBoton(btn, "…", async () => {
          try {
            await pedir(`avisos-corte/${btn.dataset.id}/aplicar?campana_id=${estado.campana}`, {
              method: "POST", body: JSON.stringify({ factor }),
            });
          } catch (err) {
            mostrar("avisos-corte-mensajes", `No se pudo aplicar: ${err.message}`, "danger");
            return;
          }
          mostrar("avisos-corte-mensajes", "Aplicado como ajuste manual: se ve en el próximo recálculo.", "success");
          await Promise.all([cargarAvisosCorte(), cargarAjustes()]);
        });
      });
    });
    tbody.querySelectorAll(".btn-descartar-aviso").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await conBoton(btn, "…", async () => {
          try {
            await pedir(`avisos-corte/${btn.dataset.id}/descartar?campana_id=${estado.campana}`, { method: "POST" });
          } catch (err) {
            mostrar("avisos-corte-mensajes", `No se pudo descartar: ${err.message}`, "danger");
            return;
          }
          await cargarAvisosCorte();
        });
      });
    });
  }

  // =========================================================================
  // 4. ARRANQUE
  // =========================================================================

  // Sólo con la configuración: se dispara en cada carga (también al recalcular y
  // al cambiar de campaña), así que escuchar además el plan pedía todo dos veces.
  document.addEventListener("planificador:config", (ev) => {
    const cfg = ev.detail || {};
    skillsConfig = [];
    (cfg.pools || []).forEach((p) => {
      (p.skills || []).forEach((s) => {
        if (!skillsConfig.some((x) => x.skill_id === s.skill_id)) skillsConfig.push(s);
      });
    });
    inicializarFormularioAjustes();
    cargarReparto();
    cargarAjustes();
    cargarAvisosCorte();
  });

  // Para la tarjeta de seguimiento, que vive en planificador.js.
  window.PlanificadorInsumos = { proponerReparto, cargarReparto, cargarAjustes };
})();
