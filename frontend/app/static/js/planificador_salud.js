/* Planificador — salud del plan (frescura de los datos, migraciones, avisos).
 *
 * Parte de la pantalla en archivo propio: usa window.Planificador (ver el final
 * de planificador.js) y los eventos planificador:config / planificador:plan.
 *
 * Contesta lo que conviene saber ANTES de discutir un número: cuándo se calculó
 * el plan y si fue a mano, si alguna fuente no llegó hasta donde debería, qué
 * migraciones faltan y qué parámetros están cargados a mano en vez de medidos.
 */
(function () {
  "use strict";

  const P = () => window.Planificador;
  const $ = (id) => document.getElementById(id);
  const pad = (n) => String(n).padStart(2, "0");

  // Pestañas a las que puede mandar un aviso ("Ir a arreglarlo").
  const PESTANAS = {
    plan: "#tab-plan", refuerzos: "#tab-refuerzos", comparacion: "#tab-comparacion",
    escenarios: "#tab-escenarios", config: "#tab-config",
    laboratorio: "#tab-laboratorio",
  };
  const DIAS = ["todos los días", "Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"];
  const ESTADO = {
    ok:        { clase: "bg-success-subtle text-success-emphasis", texto: "al día" },
    atrasado:  { clase: "bg-warning-subtle text-warning-emphasis", texto: "atrasado" },
    falta:     { clase: "bg-danger-subtle text-danger-emphasis",   texto: "falta" },
    error:     { clase: "bg-danger-subtle text-danger-emphasis",   texto: "no se pudo leer" },
    no_aplica: { clase: "bg-light text-muted border",              texto: "no aplica" },
  };

  let corrida = null;
  let config = null;
  let salud = null;
  let errorSalud = null;

  const ddmm = (iso) => {
    const p = String(iso).slice(0, 10).split("-");
    return p.length === 3 ? `${p[2]}/${p[1]}` : String(iso);
  };
  // Corrida.CreadoEn se guarda en UTC (DEFAULT SYSUTCDATETIME) y viaja sin zona:
  // leída tal cual, el encabezado adelantaba tres horas.
  const desdeUtc = (iso) => new Date(/(Z|[+-]\d\d:?\d\d)$/.test(String(iso)) ? iso : `${iso}Z`);
  const fechaHora = (d) =>
    `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;

  function irA(pestana, ancla) {
    const selector = PESTANAS[pestana];
    const boton = selector && document.querySelector(`[data-bs-target="${selector}"]`);
    if (boton && window.bootstrap) window.bootstrap.Tab.getOrCreateInstance(boton).show();
    // La pestaña tarda un instante en mostrarse; un ancla oculta (un enchufe que
    // todavía no tiene contenido) no sirve de destino, así que se cae a la pestaña.
    setTimeout(() => {
      const el = ancla ? $(ancla) : null;
      const destino = el && !el.hidden ? el : document.querySelector(selector || "");
      if (destino) destino.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 200);
  }

  // ------------------------------------------------------------- encabezado

  function pintarEncabezado() {
    const slot = $("slot-encabezado");
    if (!slot) return;
    if (!corrida) { slot.innerHTML = '<span id="salud-badge"></span>'; pintarBadge(); return; }

    const m = corrida.Metricas || {};
    // Corridas anteriores a que se registrara el origen: no se dice nada.
    const origen = m.origen === "automatico" ? " · automático"
      : m.origen === "manual" ? " · a mano" : "";

    let viejo = "";
    if (corrida.CreadoEn) {
      const creado = desdeUtc(corrida.CreadoEn);
      creado.setHours(0, 0, 0, 0);
      const hoy = new Date();
      hoy.setHours(0, 0, 0, 0);
      const dias = Math.round((hoy - creado) / 86400000);
      // La antelación de los refuerzos se cuenta desde hoy, pero la gente que
      // falta sale de este cálculo: con un plan viejo, el pedido es viejo.
      if (dias >= 1) {
        viejo = ` <span class="text-warning-emphasis fw-semibold">
          <i class="bi bi-exclamation-triangle me-1"></i>El plan es de hace ${dias}
          día${dias > 1 ? "s" : ""}: recalculalo antes de pedir refuerzos.</span>`;
      }
    }

    slot.innerHTML = `<span class="text-muted">Plan calculado el
        ${corrida.CreadoEn ? fechaHora(desdeUtc(corrida.CreadoEn)) : "—"}${origen}
        · del ${ddmm(corrida.Desde)} al ${ddmm(corrida.Hasta)}</span>${viejo}
      <span id="salud-badge" class="ms-2"></span>`;
    pintarBadge();
  }

  function pintarBadge() {
    const b = $("salud-badge");
    if (!b) return;
    if (!salud) { b.innerHTML = ""; return; }
    const r = salud.resumen || {};
    const fuentes = (r.atrasadas || 0) + (r.faltan || 0) + (r.errores || 0);
    const pendientes = r.migraciones_pendientes || 0;
    const partes = [];
    if (fuentes) partes.push(`${fuentes} fuente${fuentes > 1 ? "s" : ""} con problemas`);
    if (pendientes) partes.push(`${pendientes} migraci${pendientes > 1 ? "ones" : "ón"} pendiente${pendientes > 1 ? "s" : ""}`);
    b.innerHTML = partes.length
      ? `<button type="button" class="btn btn-link btn-sm p-0 align-baseline text-danger"
           data-ir-pestana="config" data-ir-ancla="salud-card">
           <i class="bi bi-exclamation-circle me-1"></i>${partes.join(" · ")}</button>`
      : `<span class="text-success" title="Todas las fuentes al día">
           <i class="bi bi-check-circle me-1"></i>datos al día</span>`;
  }

  // ------------------------------------------------ tarjeta de configuración

  function lineaProcedencia(cfg) {
    const franjasManuales = (cfg.disponibilidad || []).filter((f) => f.origen === "manual");
    // Con muchas franjas a mano el detalle no se lee: se cuentan, y cada una ya
    // lleva su marca en la tabla de franjas.
    const manuales = franjasManuales.length > 3
      ? [`disponibilidad (${franjasManuales.length} franjas)`]
      : franjasManuales.map((f) => `disponibilidad ${DIAS[f.dia_semana] ?? f.dia_semana} ` +
          `${pad(f.hora_desde)}–${pad(f.hora_hasta)} h ` +
          `(${P().num(f.factor, 3).replace(".", ",")})`);
    const pr = cfg.procedencia || {};
    const medidos = [];
    if (pr.shrinkage_origen === "manual") manuales.push("shrinkage");
    else if (pr.shrinkage_origen) {
      medidos.push(`shrinkage${pr.shrinkage_medido_en ? " (" + ddmm(pr.shrinkage_medido_en) + ")" : ""}`);
    }
    if (pr.paciencia_origen === "manual") manuales.push("paciencia");
    else if (pr.paciencia_origen) medidos.push("paciencia");
    if ((cfg.disponibilidad || []).some((f) => f.origen === "medido")) medidos.push("disponibilidad");
    // El shrinkage por media hora: dónde descuenta más que el promedio del día.
    const pp = cfg.perfil_presencia;
    if (pp && pp.medido_en) {
      const donde = (pp.habil || []).filter((f) => f.exceso > 0).slice(0, 3)
        .map((f) => `${f.hora} +${Math.round(f.exceso * 100)} pp`).join(", ");
      medidos.push(`presencia por media hora (${ddmm(pp.medido_en)}` +
                   `${donde ? `; en hábiles falta más a las ${donde}` : ""})`);
    }
    // El rinde de la gente nueva contra un antiguo, tramo por tramo.
    const an = cfg.antiguedad;
    if (an && an.medido_en) {
      const semana = (d) => Math.floor(d / 7) + 1;
      const tramos = (an.tramos || []).filter((t) => t.dia_hasta !== null && t.dia_hasta !== undefined)
        .map((t) => `semanas ${semana(t.dia_desde)}–${t.dia_hasta / 7} ${Math.round(t.rinde * 100)}%`)
        .join(", ");
      medidos.push(`rinde de la gente nueva (${ddmm(an.medido_en)}` +
                   `${tramos ? `; contra un antiguo: ${tramos}` : ""})`);
    }

    const esc = P().esc;
    return `<div class="small mb-2">
        <div><span class="fw-semibold">Cargados a mano:</span>
          ${manuales.length ? esc(manuales.join(" · ")) : '<span class="text-muted">ninguno</span>'}</div>
        <div><span class="fw-semibold">Medidos de los datos:</span>
          ${medidos.length ? esc(medidos.join(" · ")) : '<span class="text-muted">ninguno</span>'}</div>
      </div>`;
  }

  function pintarTarjeta() {
    const slot = $("slot-config-arriba");
    if (!slot || !config) return;
    const esc = P().esc;

    let cuerpo;
    if (errorSalud) {
      cuerpo = `<div class="small text-danger">No se pudo revisar la salud de los datos: ${esc(errorSalud)}</div>`;
    } else if (!salud) {
      cuerpo = '<div class="small text-muted">Revisando las fuentes…</div>';
    } else {
      const filas = (salud.fuentes || []).map((f) => {
        const e = ESTADO[f.estado] || ESTADO.error;
        const detalle = f.estado === "ok" || f.estado === "no_aplica" ? ""
          : `<div class="text-muted" style="font-size:.75rem">${esc(f.detalle)}</div>`;
        return `<tr title="${esc(f.detalle)}">
            <td>${esc(f.nombre)}${detalle}</td>
            <td class="text-nowrap">${f.ultimo ? esc(f.ultimo) : "—"}</td>
            <td><span class="badge ${e.clase}">${e.texto}</span></td>
          </tr>`;
      }).join("");

      const migraciones = salud.migraciones || [];
      const pendientes = migraciones.filter((m) => m.aplicada === false);
      const sinVerificar = migraciones.filter((m) => m.aplicada === null);
      const lista = (ms) => ms.map((m) =>
        `<li><code>${esc(m.archivo)}</code> <span class="text-muted">— ${esc(m.senal || "")}</span></li>`).join("");
      const bloqueMig = `
        <details class="porque mt-2" ${pendientes.length ? "open" : ""}>
          <summary>${pendientes.length
            ? `${pendientes.length} migraci${pendientes.length > 1 ? "ones" : "ón"} sin aplicar`
            : "Todas las migraciones verificables están aplicadas"}
            <span class="fw-normal">(de ${migraciones.length})</span></summary>
          <div>
            ${pendientes.length ? `<ul class="mb-1 ps-3">${lista(pendientes)}</ul>` : ""}
            ${sinVerificar.length ? `<div class="text-muted">No se pudieron verificar:</div>
              <ul class="mb-0 ps-3">${lista(sinVerificar)}</ul>` : ""}
            <div class="text-muted mt-1">Se corren a mano contra la base, en orden de fecha.</div>
          </div>
        </details>`;

      cuerpo = `
        <div class="table-responsive">
          <table class="table table-sm align-middle mb-0 small">
            <thead class="table-light"><tr>
              <th>Fuente</th><th>Último dato</th><th>Estado</th>
            </tr></thead>
            <tbody>${filas}</tbody>
          </table>
        </div>
        ${bloqueMig}`;
    }

    slot.innerHTML = `
      <div class="card shadow-sm" id="salud-card">
        <div class="card-header bg-white d-flex justify-content-between align-items-center">
          <span class="fw-semibold">Datos y migraciones</span>
          <small class="text-muted">Si un número no cierra, mirá esto primero.</small>
        </div>
        <div class="card-body">
          ${lineaProcedencia(config)}
          ${cuerpo}
        </div>
      </div>`;
    slot.hidden = false;
  }

  async function cargarSalud() {
    salud = null;
    errorSalud = null;
    pintarTarjeta();
    try {
      salud = await P().pedir(`salud?campana_id=${P().estado.campana}`);
    } catch (e) {
      errorSalud = e.message;
    }
    pintarTarjeta();
    pintarBadge();
  }

  // ----------------------------------------------------- botón de recalcular

  // El select de días sólo afecta al recálculo; que el botón lo diga evita
  // pensar que cambiar los días cambia lo que se está viendo.
  function etiquetaRecalcular() {
    const btn = $("btn-recalcular");
    const sel = $("f-dias");
    if (!btn || !sel || btn.disabled) return;
    btn.innerHTML = `<i class="bi bi-cpu me-1"></i>Recalcular ${sel.value} días`;
  }

  // ------------------------------------------------------------------ eventos

  document.addEventListener("planificador:config", (ev) => {
    config = ev.detail;
    cargarSalud();
  });
  document.addEventListener("planificador:plan", (ev) => {
    corrida = (ev.detail && ev.detail.corrida) || null;
    pintarEncabezado();
  });

  // Delegado: los avisos del plan y el badge se regeneran en cada carga.
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".btn-ir-ancla, [data-ir-pestana]");
    if (!btn) return;
    ev.preventDefault();
    irA(btn.dataset.pestana || btn.dataset.irPestana, btn.dataset.ancla || btn.dataset.irAncla);
  });

  function iniciar() {
    if ($("f-dias")) $("f-dias").addEventListener("change", etiquetaRecalcular);
    etiquetaRecalcular();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
