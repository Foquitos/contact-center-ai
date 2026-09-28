/* Planificador — Escenarios de dotación.
 *
 * Simulación de supuestos alternativos sobre la corrida vigente:
 * volumen, TMO, umbrales, ocupación, ausentismo y descansos.
 * No guarda nada ni cambia el plan.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const P = window.Planificador;

  let configActual = null;
  let planActual = null;

  function inicializarUI() {
    const slot = $("slot-escenarios");
    if (!slot) return;

    slot.innerHTML = `
      <div class="mb-3">
        <p class="text-muted small mb-2">
          <i class="bi bi-info-circle me-1"></i>
          No guarda nada ni cambia el plan: recalcula la dotación del plan vigente con otros supuestos.
        </p>

        <!-- Atajos de precarga -->
        <div class="d-flex flex-wrap align-items-center gap-1 mb-3">
          <span class="small text-muted me-1">Atajos:</span>
          <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2" id="esc-btn-atajo-reparto"
                  title="Aumento del 40% en volumen si pasamos del 50% al 70% del reparto de demanda">
            Nos pasan del 50% al 70%
          </button>
          <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2" id="esc-btn-atajo-tmo"
                  title="Incremento del 10% en el tiempo medio de operación">
            TMO +10%
          </button>
          <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2" id="esc-btn-atajo-umbral"
                  title="Estirar el umbral de espera a 30 segundos en todos los skills">
            Umbral 30 s
          </button>
          <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2" id="esc-btn-atajo-ocupacion"
                  title="Subir el techo de ocupación máxima de operadores a 0.88">
            Techo de ocupación 0,88
          </button>
          <button type="button" class="btn btn-outline-secondary btn-sm py-0 px-2" id="esc-btn-atajo-shrinkage"
                  title="Dimensionar sin margen de ausentismo (shrinkage = 0%)">
            Sin margen de ausentismo
          </button>
          <button type="button" class="btn btn-link btn-sm py-0 px-2 text-decoration-none text-muted" id="esc-btn-limpiar"
                  title="Restablecer los valores originales del plan">
            <i class="bi bi-arrow-counterclockwise me-1"></i>Restablecer
          </button>
        </div>

        <!-- Formulario de simulación -->
        <div class="card shadow-sm mb-3">
          <div class="card-header bg-white py-2">
            <span class="fw-semibold small">Supuestos del escenario</span>
          </div>
          <div class="card-body py-2">
            <form id="form-escenario" onsubmit="return false;">
              <div class="row g-2 align-items-end">
                <!-- Volumen -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-volumen" title="Multiplicador porcentual sobre las llamadas pronosticadas">
                    Volumen ±%
                  </label>
                  <div class="input-group input-group-sm">
                    <input type="number" class="form-control" id="esc-volumen" placeholder="0" step="1" min="-90" max="200">
                    <span class="input-group-text">%</span>
                  </div>
                </div>

                <!-- TMO -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-tmo" title="Variación porcentual sobre el TMO por intervalo">
                    TMO ±%
                  </label>
                  <div class="input-group input-group-sm">
                    <input type="number" class="form-control" id="esc-tmo" placeholder="0" step="1" min="-50" max="200">
                    <span class="input-group-text">%</span>
                  </div>
                </div>

                <!-- Ocupación -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-ocupacion">
                    Ocupación máx. <small class="text-muted" id="esc-lbl-act-ocup"></small>
                  </label>
                  <input type="number" class="form-control form-control-sm" id="esc-ocupacion"
                         placeholder="0.85" step="0.01" min="0.10" max="1.00">
                </div>

                <!-- Umbral seg -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-umbral">
                    Umbral espera <small class="text-muted" id="esc-lbl-act-umbral"></small>
                  </label>
                  <div class="input-group input-group-sm">
                    <input type="number" class="form-control" id="esc-umbral" placeholder="20" step="1" min="1" max="300">
                    <span class="input-group-text">s</span>
                  </div>
                </div>

                <!-- Objetivo NDS -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-nds">
                    Objetivo NDS <small class="text-muted" id="esc-lbl-act-nds"></small>
                  </label>
                  <div class="input-group input-group-sm">
                    <input type="number" class="form-control" id="esc-nds" placeholder="80" step="1" min="1" max="99">
                    <span class="input-group-text">%</span>
                  </div>
                </div>

                <!-- Shrinkage -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-shrinkage" title="Margen de ausentismo / shrinkage aplicado">
                    Ausentismo <small class="text-muted" id="esc-lbl-act-shrink"></small>
                  </label>
                  <div class="input-group input-group-sm">
                    <input type="number" class="form-control" id="esc-shrinkage" placeholder="30" step="1" min="0" max="89">
                    <span class="input-group-text">%</span>
                  </div>
                </div>

                <!-- Break -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-break">
                    Break <small class="text-muted" id="esc-lbl-act-break"></small>
                  </label>
                  <div class="input-group input-group-sm">
                    <input type="number" class="form-control" id="esc-break" placeholder="0" step="1" min="0" max="15">
                    <span class="input-group-text">min/h</span>
                  </div>
                </div>

                <!-- Fechas opcionales -->
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-desde">Desde</label>
                  <input type="date" class="form-control form-control-sm" id="esc-desde">
                </div>
                <div class="col-6 col-md-3 col-xl-2">
                  <label class="form-label small mb-1" for="esc-hasta">Hasta</label>
                  <input type="date" class="form-control form-control-sm" id="esc-hasta">
                </div>

                <!-- Botón de acción -->
                <div class="col-12 col-md-3 col-xl-2">
                  <button type="button" id="btn-calcular-escenario" class="btn btn-primary btn-sm w-100">
                    <i class="bi bi-play-fill me-1"></i>Calcular escenario
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>

        <!-- Avisos dinámicos -->
        <div id="esc-avisos"></div>

        <!-- Contenedor de Resultados -->
        <div id="esc-resultados" hidden>
          <!-- 4 KPIs principales -->
          <div class="row g-2 mb-3" id="esc-kpis">
            <div class="col-6 col-md-3">
              <div class="plan-kpi">
                <div class="valor" id="kpi-esc-horas">—</div>
                <div class="etiqueta">Horas-operador a planificar</div>
                <div class="fuente" id="kpi-esc-horas-fuente">base → escenario</div>
              </div>
            </div>
            <div class="col-6 col-md-3">
              <div class="plan-kpi">
                <div class="valor" id="kpi-esc-pico">—</div>
                <div class="etiqueta">Pico de operadores</div>
                <div class="fuente" id="kpi-esc-pico-fuente">base → escenario</div>
              </div>
            </div>
            <div class="col-6 col-md-3">
              <div class="plan-kpi">
                <div class="valor" id="kpi-esc-faltantes">—</div>
                <div class="etiqueta">Horas faltantes vs malla</div>
                <div class="fuente" id="kpi-esc-faltantes-fuente">base → escenario</div>
              </div>
            </div>
            <div class="col-6 col-md-3">
              <div class="plan-kpi">
                <div class="valor" id="kpi-esc-nds">—</div>
                <div class="etiqueta">NDS proyectado</div>
                <div class="fuente" id="kpi-esc-nds-fuente">base → escenario</div>
              </div>
            </div>
          </div>

          <!-- Gráfico por día -->
          <div class="card shadow-sm mb-3">
            <div class="card-header bg-white py-2 d-flex justify-content-between align-items-center">
              <span class="fw-semibold small">Horas-operador por día: base vs escenario</span>
            </div>
            <div class="card-body py-2">
              <div class="plan-chart">
                <canvas id="chart-escenarios"></canvas>
              </div>
            </div>
          </div>

          <!-- Tabla por día -->
          <div class="card shadow-sm">
            <div class="card-header bg-white py-2 d-flex justify-content-between align-items-center flex-wrap gap-2">
              <span class="fw-semibold small">Detalle diario de dotación y faltante</span>
              <div class="form-check form-switch mb-0">
                <input class="form-check-input" type="checkbox" id="chk-esc-todas-cols">
                <label class="form-check-label small" for="chk-esc-todas-cols">Ver todas las columnas</label>
              </div>
            </div>
            <div class="table-responsive tabla-alta">
              <table class="table table-sm table-hover align-middle mb-0 tabla-resumida" id="tabla-escenario-dias">
                <thead class="table-light small">
                  <tr>
                    <th>Día</th>
                    <th class="text-end" title="Horas citables con la configuración base">Horas base</th>
                    <th class="text-end" title="Horas citables con el escenario simulado">Horas escenario</th>
                    <th class="text-end" title="Diferencia de horas requeridas">Diferencia</th>
                    <th class="text-end col-detalle" title="Variación porcentual sobre la base">Var. %</th>
                    <th class="text-end" title="Horas no cubiertas por la malla citada en la base">Faltante base</th>
                    <th class="text-end" title="Horas no cubiertas por la malla citada en el escenario">Faltante escenario</th>
                  </tr>
                </thead>
                <tbody class="small"></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    `;

    slot.hidden = false;
    vincularEventos();
    actualizarValoresReferencia();
  }

  function actualizarValoresReferencia() {
    if (!configActual) return;

    if ($("esc-lbl-act-ocup") && configActual.max_ocupacion !== undefined && configActual.max_ocupacion !== null) {
      $("esc-lbl-act-ocup").textContent = `(${configActual.max_ocupacion})`;
      if (!$("esc-ocupacion").value) $("esc-ocupacion").placeholder = configActual.max_ocupacion;
    }

    // La configuración trae los skills adentro de cada pool, no sueltos.
    const skills = (configActual.pools || []).flatMap((p) => p.skills || []);
    const umbrales = skills.filter((s) => s.activo && s.umbral_seg).map((s) => s.umbral_seg);
    if (umbrales.length && $("esc-lbl-act-umbral")) {
      const minUmb = Math.min(...umbrales);
      $("esc-lbl-act-umbral").textContent = `(${minUmb}s)`;
      if (!$("esc-umbral").value) $("esc-umbral").placeholder = minUmb;
    }

    const objetivos = skills.filter((s) => s.activo && s.objetivo_nds).map((s) => s.objetivo_nds);
    if (objetivos.length && $("esc-lbl-act-nds")) {
      const maxObj = Math.max(...objetivos);
      $("esc-lbl-act-nds").textContent = `(${Math.round(maxObj * 100)}%)`;
      if (!$("esc-nds").value) $("esc-nds").placeholder = Math.round(maxObj * 100);
    }

    if ($("esc-lbl-act-shrink") && configActual.shrinkage !== undefined && configActual.shrinkage !== null) {
      const shPct = Math.round(configActual.shrinkage * 100);
      $("esc-lbl-act-shrink").textContent = `(${shPct}%)`;
      if (!$("esc-shrinkage").value) $("esc-shrinkage").placeholder = shPct;
    }

    if ($("esc-lbl-act-break") && configActual.break_min_por_hora !== undefined && configActual.break_min_por_hora !== null) {
      $("esc-lbl-act-break").textContent = `(${configActual.break_min_por_hora}m)`;
      if (!$("esc-break").value) $("esc-break").placeholder = configActual.break_min_por_hora;
    }

    if (planActual && planActual.corrida) {
      if ($("esc-desde") && !$("esc-desde").value && planActual.corrida.Desde) {
        $("esc-desde").value = String(planActual.corrida.Desde).slice(0, 10);
      }
      if ($("esc-hasta") && !$("esc-hasta").value && planActual.corrida.Hasta) {
        $("esc-hasta").value = String(planActual.corrida.Hasta).slice(0, 10);
      }
    }
  }

  function vincularEventos() {
    // Atajos que precargan supuestos
    $("esc-btn-atajo-reparto")?.addEventListener("click", () => {
      // 70% / 50% = 1.40 (+40% de volumen)
      $("esc-volumen").value = 40;
    });

    $("esc-btn-atajo-tmo")?.addEventListener("click", () => {
      $("esc-tmo").value = 10;
    });

    $("esc-btn-atajo-umbral")?.addEventListener("click", () => {
      $("esc-umbral").value = 30;
    });

    $("esc-btn-atajo-ocupacion")?.addEventListener("click", () => {
      $("esc-ocupacion").value = 0.88;
    });

    $("esc-btn-atajo-shrinkage")?.addEventListener("click", () => {
      $("esc-shrinkage").value = 0;
    });

    $("esc-btn-limpiar")?.addEventListener("click", () => {
      $("esc-volumen").value = "";
      $("esc-tmo").value = "";
      $("esc-ocupacion").value = "";
      $("esc-umbral").value = "";
      $("esc-nds").value = "";
      $("esc-shrinkage").value = "";
      $("esc-break").value = "";
      if (planActual && planActual.corrida) {
        $("esc-desde").value = String(planActual.corrida.Desde).slice(0, 10);
        $("esc-hasta").value = String(planActual.corrida.Hasta).slice(0, 10);
      }
    });

    $("chk-esc-todas-cols")?.addEventListener("change", (e) => {
      const tabla = $("tabla-escenario-dias");
      if (!tabla) return;
      if (e.target.checked) {
        tabla.classList.remove("tabla-resumida");
      } else {
        tabla.classList.add("tabla-resumida");
      }
    });

    $("btn-calcular-escenario")?.addEventListener("click", calcularEscenario);
  }

  async function calcularEscenario() {
    const btn = $("btn-calcular-escenario");
    if (!btn || !window.Planificador) return;

    await window.Planificador.conBoton(btn, "Calculando…", async () => {
      $("esc-avisos").innerHTML = "";

      const cambios = {};

      const volVal = $("esc-volumen").value.trim();
      if (volVal !== "") {
        cambios.factor_volumen = 1 + Number(volVal) / 100;
      }

      const tmoVal = $("esc-tmo").value.trim();
      if (tmoVal !== "") {
        cambios.factor_tmo = 1 + Number(tmoVal) / 100;
      }

      const ocupVal = $("esc-ocupacion").value.trim();
      if (ocupVal !== "") {
        cambios.max_ocupacion = Number(ocupVal);
      }

      const umbVal = $("esc-umbral").value.trim();
      if (umbVal !== "") {
        cambios.umbral_seg = Number(umbVal);
      }

      const ndsVal = $("esc-nds").value.trim();
      if (ndsVal !== "") {
        cambios.objetivo_nds = Number(ndsVal) / 100;
      }

      const shVal = $("esc-shrinkage").value.trim();
      if (shVal !== "") {
        cambios.shrinkage = Number(shVal) / 100;
      }

      const brkVal = $("esc-break").value.trim();
      if (brkVal !== "") {
        cambios.break_min_por_hora = Number(brkVal);
      }

      const desdeVal = $("esc-desde")?.value || null;
      const hastaVal = $("esc-hasta")?.value || null;

      const payload = {
        campana_id: window.Planificador.estado.campana,
        desde: desdeVal,
        hasta: hastaVal,
        cambios: cambios,
      };

      try {
        const data = await window.Planificador.pedir("escenario", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        pintarResultados(data);
      } catch (err) {
        $("esc-avisos").innerHTML = window.Planificador.aviso(
          `No se pudo calcular el escenario: ${window.Planificador.esc(err.message)}`,
          "danger",
          "x-octagon"
        );
      }
    });
  }

  function pintarResultados(data) {
    const P = window.Planificador;
    const res = data.resumen || {};
    const base = res.base || {};
    const esc = res.escenario || {};
    const dif = res.diferencia || {};

    // Avisos devueltos por el backend (ej: si la base no coincide con la corrida guardada)
    if (data.avisos && data.avisos.length > 0) {
      $("esc-avisos").innerHTML = data.avisos
        .map((a) => P.aviso(P.esc(a), "warning", "exclamation-triangle"))
        .join("");
    }

    // 1. KPIs
    const difHorasSigno = dif.horas_operador > 0 ? "+" : "";
    const difPctSigno = dif.horas_operador_pct > 0 ? "+" : "";
    $("kpi-esc-horas").innerHTML = `${P.num(base.horas_operador, 1)} → ${P.num(esc.horas_operador, 1)} <small class="text-muted fs-6">h</small>`;
    $("kpi-esc-horas-fuente").innerHTML = `Δ ${difHorasSigno}${P.num(dif.horas_operador, 1)} h (${difPctSigno}${P.pct(dif.horas_operador_pct)})`;

    const picoSigno = dif.pico_operadores > 0 ? "+" : "";
    $("kpi-esc-pico").innerHTML = `${P.num(base.pico_operadores)} → ${P.num(esc.pico_operadores)} <small class="text-muted fs-6">op</small>`;
    $("kpi-esc-pico-fuente").innerHTML = `Δ ${picoSigno}${P.num(dif.pico_operadores)} op pico`;

    if (base.horas_faltantes !== null && base.horas_faltantes !== undefined) {
      const faltaSigno = (dif.horas_faltantes || 0) > 0 ? "+" : "";
      $("kpi-esc-faltantes").innerHTML = `${P.num(base.horas_faltantes, 1)} → ${P.num(esc.horas_faltantes, 1)} <small class="text-muted fs-6">h</small>`;
      $("kpi-esc-faltantes-fuente").innerHTML = `Δ ${faltaSigno}${P.num(dif.horas_faltantes, 1)} h vs malla`;
    } else {
      $("kpi-esc-faltantes").innerHTML = "—";
      $("kpi-esc-faltantes-fuente").textContent = "Sin malla en el período";
    }

    if (base.nds_promedio !== null && base.nds_promedio !== undefined) {
      $("kpi-esc-nds").innerHTML = `${P.pct(base.nds_promedio)} → ${P.pct(esc.nds_promedio)}`;
      $("kpi-esc-nds-fuente").innerHTML = `Abandono: ${P.pct(base.abandono_promedio)} → ${P.pct(esc.abandono_promedio)}`;
    } else {
      $("kpi-esc-nds").innerHTML = "—";
      $("kpi-esc-nds-fuente").textContent = "Sin llamadas en el período";
    }

    // 2. Gráfico por día
    const porDia = data.por_dia || [];
    const dias = porDia.map((d) => P.dia(d.dia));
    const dataBase = porDia.map((d) => d.horas_base);
    const dataEsc = porDia.map((d) => d.horas_escenario);

    P.dibujar(
      "chart-escenarios",
      dias,
      [
        {
          label: "Base (plan vigente)",
          data: dataBase,
          borderColor: P.COLOR.planificar || "#eb6834",
        },
        {
          label: "Escenario simulado",
          data: dataEsc,
          borderColor: P.COLOR.linea || "#1baf7a",
        },
      ],
      true,
      (t) => t[0].label
    );

    // 3. Tabla de días
    const tbody = $("tabla-escenario-dias")?.querySelector("tbody");
    if (tbody) {
      tbody.innerHTML = porDia.map((d) => {
        const difH = Math.round((d.horas_escenario - d.horas_base) * 10) / 10;
        const difHSigno = difH > 0 ? "+" : "";
        const claseDif = difH > 0 ? "brecha-falta" : (difH < 0 ? "brecha-sobra" : "");
        const difPct = d.horas_base > 0 ? difH / d.horas_base : 0;
        const difPctSigno = difPct > 0 ? "+" : "";

        return `
          <tr>
            <td>${P.dia(d.dia)}</td>
            <td class="text-end">${P.num(d.horas_base, 1)} h</td>
            <td class="text-end fw-semibold">${P.num(d.horas_escenario, 1)} h</td>
            <td class="text-end ${claseDif}">${difHSigno}${P.num(difH, 1)} h</td>
            <td class="text-end col-detalle text-muted">${difPctSigno}${P.pct(difPct)}</td>
            <td class="text-end">${d.faltante_base !== null ? P.num(d.faltante_base, 1) + " h" : "—"}</td>
            <td class="text-end">${d.faltante_escenario !== null ? P.num(d.faltante_escenario, 1) + " h" : "—"}</td>
          </tr>
        `;
      }).join("");
    }

    $("esc-resultados").hidden = false;
  }

  function arrancar() {
    if (window.Planificador && window.Planificador.estado) {
      if (window.Planificador.estado.config) {
        configActual = window.Planificador.estado.config;
      }
    }
    inicializarUI();
  }

  // Escuchar eventos globales del Planificador
  document.addEventListener("planificador:config", (ev) => {
    configActual = ev.detail;
    actualizarValoresReferencia();
  });

  document.addEventListener("planificador:plan", (ev) => {
    planActual = ev.detail;
    actualizarValoresReferencia();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", arrancar);
  } else {
    arrancar();
  }
})();
