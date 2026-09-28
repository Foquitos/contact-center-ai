/* Planificador — pronóstico de llamadas, dotación necesaria y comparación con la
 * malla de RRHH.
 *
 * La página se llena desde /planificador/api/*, que proxea a FastAPI. Acá no hay
 * lógica de negocio: el requerimiento ya viene calculado del backend, incluido el
 * motivo por el que cada intervalo pide la dotación que pide.
 *
 * Sobre los gráficos: son DOS, con el mismo eje de tiempo, y no uno con dos ejes.
 * Llamadas y operadores son magnitudes distintas; con dos escalas independientes
 * el gráfico puede sugerir cualquier relación según cómo se elijan, y la relación
 * entre las dos es justamente lo que se está tratando de mostrar. En cambio "en
 * línea", "a planificar" y "citados" sí van juntos: son la misma unidad.
 */
(function () {
  "use strict";

  const API = "/planificador/api";
  const $ = (id) => document.getElementById(id);
  const EDITA = window.PLAN_PUEDE_EDITAR === true;

  const COLOR = {
    linea: "#1baf7a",
    planificar: "#eb6834",
    citados: "#4a3aa7",
    real: "#2a78d6",
    pronost: "#eb6834",
    ingenuo: "#8d8b85",
    produccion: "#4a3aa7",
    combinado: "#0f9b8e",
    // Digital, la gente que se puede pasar a la línea. Va sumada encima de los
    // citados y punteada: es capacidad posible, no gente asignada.
    refuerzo: "#0f9b8e",
    grid: "#e6e5e1",
    texto: "#52514e",
  };

  const DIAS = ["Todos", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes",
                "Sábado", "Domingo"];

  const estado = {
    campana: 20,
    config: null,
    requerimiento: [],
    cotejo: { real: [], cliente: [], hasta: null },
    dotacion: null,
    dias: [],
    franjas: [],
    calibracion: null,
    backtest: null,
    refuerzos: null,
    extras_habituales: null,
    charts: {},
  };

  // ------------------------------------------------------------------ helpers

  const pct = (v, d = 1) =>
    (v === null || v === undefined ? "—" : (v * 100).toFixed(d) + "%");
  const num = (v, d = 0) =>
    (v === null || v === undefined ? "—" : Number(v).toFixed(d));
  const hora = (iso) => {
    const d = new Date(iso);
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0");
  };
  const dia = (iso) => String(iso).slice(0, 10);
  const hoyIso = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-` +
           String(d.getDate()).padStart(2, "0");
  };
  // «vie 25/09 · hoy»: en un selector de 30 fechas el día de la semana es lo
  // primero que se busca, y el ISO pelado obliga a hacer la cuenta.
  const SEMANA = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  function etiquetaDia(iso) {
    const d = new Date(`${iso}T00:00:00`);
    if (isNaN(d)) return iso;
    const man = new Date(); man.setDate(man.getDate() + 1);
    const hoy = hoyIso();
    const manIso = `${man.getFullYear()}-${String(man.getMonth() + 1).padStart(2, "0")}-` +
                   String(man.getDate()).padStart(2, "0");
    const sufijo = iso === hoy ? " · hoy" : iso === manIso ? " · mañana" : "";
    return `${SEMANA[d.getDay()]} ${iso.slice(8, 10)}/${iso.slice(5, 7)}${sufijo}`;
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function aviso(texto, tipo = "warning", icono = "exclamation-triangle") {
    return `<div class="alert alert-${tipo} py-2 px-3 small d-flex align-items-start gap-2">
              <i class="bi bi-${icono} mt-1"></i><div>${texto}</div></div>`;
  }

  // Nada de la campaña escrito a mano: el nombre del refuerzo (Digital en Voltara)
  // viene con la configuración.
  const REF = () => ((estado.config || {}).refuerzo || {}).etiqueta || "Refuerzo";

  function pintarEtiquetasDeCampana() {
    document.querySelectorAll(".lbl-refuerzo").forEach((el) => { el.textContent = REF(); });
  }

  // Flask-WTF protege todo lo que no sea GET. Sin este header un POST vuelve 400
  // con "Tu sesión venció", que no tiene nada que ver con la sesión ni con los
  // permisos: es el token CSRF que faltaba.
  const CSRF = document.querySelector('meta[name="csrf-token"]')
    ?.getAttribute("content") || "";

  async function pedir(ruta, opciones = {}) {
    const metodo = (opciones.method || "GET").toUpperCase();
    const headers = { "Content-Type": "application/json", ...(opciones.headers || {}) };
    if (metodo !== "GET") headers["X-CSRFToken"] = CSRF;

    const r = await fetch(`${API}/${ruta}`, { ...opciones, headers });
    const cuerpo = r.status === 204 ? null : await r.json().catch(() => null);
    if (!r.ok) {
      const err = new Error((cuerpo && cuerpo.detail) || `Error ${r.status}`);
      err.status = r.status;
      throw err;
    }
    return cuerpo;
  }

  // Una comparación larga no entra en una request (gunicorn corta a los 30 s): se
  // lanza en segundo plano y se consulta hasta que termina. `alAvanzar` recibe los
  // segundos transcurridos, para mostrar que sigue viva.
  async function pedirBacktest(params, alAvanzar) {
    const lanzado = await pedir(`backtest?${params}&en_segundo_plano=true`);
    const inicio = Date.now();
    while (Date.now() - inicio < 15 * 60 * 1000) {
      await new Promise((ok) => setTimeout(ok, 2000));
      const t = await pedir(`trabajos/${encodeURIComponent(lanzado.job_id)}`);
      if (t.estado === "listo") return t.resultado;
      if (t.estado === "error") throw new Error(t.detalle || "La comparación falló.");
      if (t.estado === "desconocido") throw new Error("Se perdió el seguimiento de la comparación; volvé a lanzarla.");
      if (alAvanzar) alAvanzar(t.segundos);
    }
    throw new Error("La comparación tardó más de 15 minutos; probá con un período más corto.");
  }

  async function conBoton(btn, etiqueta, fn) {
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${etiqueta}`;
    try { return await fn(); } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  }

  // -------------------------------------------------------------------- carga

  // ------------------------------------------------- refresco automático de hoy

  // Con hoy en pantalla, «Real» y «Conectados» avanzan cada media hora: se relee
  // el plan solo (no la configuración, que pisaría lo que se esté editando).
  // Sólo con la pestaña Plan a la vista y el navegador en primer plano.
  const REFRESCO_MS = 10 * 60 * 1000;
  let ultimaLectura = null;

  function marcarLectura() {
    ultimaLectura = Date.now();
    pintarActualizado();
  }

  function pintarActualizado() {
    const el = $("plan-actualizado");
    if (!el || !ultimaLectura) return;
    const min = Math.floor((Date.now() - ultimaLectura) / 60000);
    const hoyEnPantalla = $("f-dia") && $("f-dia").value === hoyIso();
    el.textContent = min < 1 ? "Actualizado recién" : `Actualizado hace ${min} min`;
    el.title = hoyEnPantalla
      ? "Con el día de hoy en pantalla el plan se relee solo cada 10 minutos."
      : "Se relee solo cada 10 minutos cuando el día elegido es hoy.";
    el.classList.toggle("text-warning-emphasis", min >= 30);
  }

  function tocaRefrescar() {
    const planVisible = $("tab-plan") && $("tab-plan").classList.contains("active");
    return document.visibilityState === "visible" && planVisible && ultimaLectura &&
           $("f-dia") && $("f-dia").value === hoyIso() &&
           Date.now() - ultimaLectura >= REFRESCO_MS;
  }

  async function refrescarPlan() {
    const campana = estado.campana;
    try {
      const plan = await pedir(`plan?campana_id=${campana}`);
      if (campana !== estado.campana) return;     // cambiaron de campaña en el medio
      $("avisos").innerHTML = "";
      pintarPlan(plan);
      document.dispatchEvent(new CustomEvent("planificador:plan", { detail: plan }));
      cargarSeguimiento();
      marcarLectura();
    } catch (e) {
      ultimaLectura = Date.now();   // no reintentar en loop; se vuelve a probar en 10 min
    }
  }

  setInterval(() => {
    pintarActualizado();
    if (tocaRefrescar()) refrescarPlan();
  }, 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (tocaRefrescar()) refrescarPlan();
  });

  async function cargarTodo() {
    $("avisos").innerHTML = "";
    try {
      const [config, plan] = await Promise.all([
        pedir(`config?campana_id=${estado.campana}`),
        // Una campaña dada de alta sin fuente de datos tiene configuración pero no
        // plan: ese error no tiene que tapar la configuración.
        pedir(`plan?campana_id=${estado.campana}`).catch((e) => ({ __fallo: e })),
      ]);
      estado.config = config;
      pintarEtiquetasDeCampana();
      pintarSelectorDePool();
      pintarConfiguracion(config);
      document.dispatchEvent(new CustomEvent("planificador:config", { detail: config }));
      if ($("btn-recalcular")) $("btn-recalcular").disabled = config.con_fuente === false;
      if (plan && plan.__fallo) {
        if (config.con_fuente !== false) throw plan.__fallo;
        $("avisos").innerHTML = aviso(
          `<strong>${esc(config.nombre || "Esta campaña")} todavía no tiene fuente de datos.</strong> ` +
          "Está dada de alta en el planificador y se puede configurar, pero para " +
          "pronosticar hace falta declarar de dónde se leen sus llamadas y su malla " +
          "(<code>planificador_datos.FUENTES</code>).", "info", "info-circle");
        $("kpis").innerHTML = "";
        limpiarGraficos();
        return;
      }
      pintarPlan(plan);
      document.dispatchEvent(new CustomEvent("planificador:plan", { detail: plan }));
      marcarLectura();
      restaurarComparacion();
      cargarEventos();
      cargarCodigos();
      cargarSeguimiento();
      cargarRefuerzos();
    } catch (e) {
      // 409 = la migración todavía no corrió. Es un paso pendiente del deploy,
      // no un error: se dice qué falta y cómo se arregla.
      if (e.status === 409) {
        $("avisos").innerHTML = aviso(
          `<strong>Falta aplicar una migración del planificador.</strong><br>` +
          `${esc(e.message)}`, "info", "info-circle");
      } else {
        $("avisos").innerHTML = aviso(
          `No se pudo cargar el planificador: ${esc(e.message)}`, "danger", "x-octagon");
      }
    }
  }

  function pintarSelectorDePool() {
    $("f-pool").innerHTML = (estado.config.pools || [])
      .map((p) => `<option value="${p.pool_id}">${esc(p.nombre)}</option>`).join("");
  }

  function pintarPlan(plan) {
    if (!plan || !plan.corrida) {
      $("kpis").innerHTML = "";
      $("avisos").innerHTML += aviso(
        "Todavía no hay ninguna corrida. Apretá <strong>Recalcular</strong> para armar la primera.",
        "info", "info-circle");
      limpiarGraficos();
      return;
    }

    // El aviso que más importa: mientras sólo tengamos nuestra porción de las
    // llamadas, el pronóstico no puede anticipar un cambio en el reparto.
    if (plan.sobre_demanda_total === false) {
      $("avisos").innerHTML += aviso(
        "<strong>El pronóstico es sobre las llamadas que recibimos nosotros, no sobre " +
        "la demanda total del cliente.</strong> La serie histórica mezcla el volumen " +
        "del cliente con el porcentaje que nos asignan, así que si ese reparto cambia " +
        "el pronóstico va a quedar corto o largo. Se corrige cuando esté cargada la " +
        "descarga del 100% de las llamadas.");
    }

    const m = plan.corrida.Metricas || {};
    if (m.avisos_detalle && m.avisos_detalle.length) {
      const vistos = new Set();
      const unicos = [];
      for (const a of m.avisos_detalle) {
        if (!vistos.has(a.texto)) {
          vistos.add(a.texto);
          unicos.push(a);
        }
      }
      const accionables = unicos.filter((a) => a.nivel === "config" || a.nivel === "alerta");
      const informativos = unicos.filter((a) => a.nivel === "info");

      accionables.forEach((a) => {
        // Amarillo los dos: «config» se distingue por el ícono y el botón. El rojo
        // queda para cuando no se pudo cargar la pantalla.
        const tipo = "warning";
        const icono = a.nivel === "config" ? "gear-wide-connected" : "exclamation-triangle";
        let boton = "";
        if (a.accion && a.accion.pestana) {
          boton = `<button class="btn btn-sm btn-outline-dark ms-auto btn-ir-ancla align-self-center text-nowrap" ` +
                  `data-pestana="${esc(a.accion.pestana)}" data-ancla="${esc(a.accion.ancla || '')}">` +
                  `<i class="bi bi-arrow-right-circle me-1"></i>Ir a arreglarlo</button>`;
        }
        $("avisos").innerHTML += `<div class="alert alert-${tipo} py-2 px-3 small d-flex align-items-center gap-2 flex-wrap mb-2">` +
          `<i class="bi bi-${icono}"></i><div class="flex-grow-1">${esc(a.texto)}</div>${boton}</div>`;
      });

      if (informativos.length) {
        const items = informativos.map((a) => `<li class="mb-1">${esc(a.texto)}</li>`).join("");
        $("avisos").innerHTML += `<details class="porque mb-2"><summary>${informativos.length} aviso${informativos.length > 1 ? "s informativos" : " informativo"}</summary>` +
          `<div class="pt-1"><ul class="mb-0 ps-3">${items}</ul></div></details>`;
      }
    } else if (m.avisos && m.avisos.length) {
      const unicos = [...new Set(m.avisos)];
      $("avisos").innerHTML += aviso(unicos.map(esc).join("<br>"));
    }

    const b = m.brecha;
    // Con el rinde por antigüedad medido la brecha va contra los citados en
    // equivalentes: se dice en el KPI para que no parezca otra cuenta.
    const notaEquivalentes = m.antiguedad ? " · citados en equivalentes por antigüedad" : "";
    $("kpis").innerHTML = [
      kpi(num(m.llamadas), "Llamadas pronosticadas", "línea de base estacional"),
      kpi(num(m.pico_operadores), "Pico de operadores", "a planificar"),
      kpi(num(m.horas_operador, 0), "Horas-operador", "todo el horizonte"),
      kpi(pct(m.nds_promedio), "Nivel de servicio proyectado", "Erlang C, contractual"),
      kpi(pct(m.abandono_promedio, 2), "Abandono proyectado", "Erlang A"),
      // Con Digital configurado, lo que importa es lo que falta AUN pasando a su
      // gente: lo otro se resuelve en el día sin tocar la malla.
      b && b.con_refuerzo
        ? kpi(num(b.horas_operador_faltantes_netas, 0), `Horas-operador faltantes tras ${REF()}`,
              `de ${num(b.horas_operador_faltantes, 0)} h; ${REF()} cubre ${
                num(b.horas_operador_cubre_refuerzo, 0)} h${notaEquivalentes}`)
        : b ? kpi(num(b.horas_operador_faltantes, 0), "Horas-operador faltantes",
                  `${b.intervalos_en_falta} de ${b.intervalos_con_malla} intervalos${notaEquivalentes}`)
        : kpi(num(m.atipicos_nuevos), "Días atípicos detectados", "sobre el histórico"),
    ].join("");

    estado.requerimiento = plan.requerimiento || [];
    // Las dos referencias contra las que se lee el plan. `hasta` es el último
    // intervalo cerrado: sin él no se puede distinguir "entraron cero" de
    // "todavía no pasó", y la curva real se desplomaría hasta el final del día.
    estado.cotejo = {
      real: plan.real || [],
      cliente: plan.cliente || [],
      conectados: plan.conectados || [],
      hasta: plan.real_hasta ? new Date(plan.real_hasta).getTime() : null,
    };
    estado.extras_habituales = plan.extras_habituales || null;
    estado.dias = [...new Set(estado.requerimiento.map((r) => dia(r.Intervalo)))].sort();
    // Al volver a leer se queda en el día que se estaba mirando; si no, arranca
    // en hoy, que es lo que se mira a primera hora.
    const antes = $("f-dia").value;
    $("f-dia").innerHTML = estado.dias
      .map((d) => `<option value="${d}">${etiquetaDia(d)}</option>`).join("");
    if (estado.dias.includes(antes)) $("f-dia").value = antes;
    else if (estado.dias.includes(hoyIso())) $("f-dia").value = hoyIso();
    redibujar();
  }

  function kpi(valor, etiqueta, fuente) {
    return `<div class="col-6 col-md-4 col-xl-2">
              <div class="plan-kpi">
                <div class="valor">${valor}</div>
                <div class="etiqueta">${esc(etiqueta)}</div>
                ${fuente ? `<div class="fuente">${esc(fuente)}</div>` : ""}
              </div></div>`;
  }

  // ---------------------------------------------------------------- gráficos

  function poolActual() {
    const id = Number($("f-pool").value);
    return (estado.config.pools || []).find((p) => p.pool_id === id);
  }

  function filasVisibles() {
    const pool = Number($("f-pool").value);
    const d = $("f-dia").value;
    return estado.requerimiento
      .filter((r) => r.PoolID === pool && dia(r.Intervalo) === d)
      .sort((a, b) => a.Intervalo.localeCompare(b.Intervalo));
  }

  // Clave común de las tres series. Se usa el instante y no el texto ISO porque
  // vienen de tres tablas distintas: basta que una traiga los segundos y la otra
  // no para que dejen de cruzarse, y el gráfico saldría vacío sin decir por qué.
  const instante = (iso) => new Date(iso).getTime();

  // Un intervalo se nombra por donde ARRANCA, así que "cerrado hasta las 11:00"
  // en realidad quiere decir hasta las 11:30. Media hora de diferencia sobre el
  // dato más nuevo es justo la que se mira para decidir un refuerzo.
  const finDeIntervalo = (iso) => {
    const d = new Date(iso);
    d.setMinutes(d.getMinutes() + ((estado.config || {}).intervalo_min || 30));
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0");
  };

  function porIntervalo(filas, skills) {
    const m = new Map();
    filas.forEach((f) => {
      if (!skills.has(f.SkillID)) return;
      const k = instante(f.Intervalo);
      m.set(k, (m.get(k) || 0) + f.Llamadas);
    });
    return m;
  }

  function tipoDeDiaPlan(diaIso) {
    const extras = estado.extras_habituales;
    const feriados = (extras && Array.isArray(extras.feriados)) ? extras.feriados : [];
    if (feriados.includes(diaIso)) return "domingo_feriado";
    const partes = diaIso.split("-").map(Number);
    const dt = new Date(partes[0], partes[1] - 1, partes[2]);
    const w = dt.getDay();
    if (w === 0) return "domingo_feriado";
    if (w === 6) return "sabado";
    return "habil";
  }

  function obtenerExtrasPorHora(poolId, tipoDia) {
    const extras = estado.extras_habituales;
    if (!extras || !extras.por_pool) return {};
    const filas = extras.por_pool[poolId] || extras.por_pool[String(poolId)] || [];
    const mapa = {};
    filas.forEach((f) => {
      if (f.tipo_dia === tipoDia) {
        mapa[f.hora] = f.personas;
      }
    });
    return mapa;
  }

  // La media hora en curso, si el día elegido es hoy; null en cualquier otro día.
  function indiceAhora(filas) {
    const t = Date.now();
    if (!filas.length || dia(filas[0].Intervalo) !== hoyIso()) return null;
    let i = null;
    filas.forEach((r, k) => { if (instante(r.Intervalo) <= t) i = k; });
    return i;
  }

  function redibujar() {
    const filas = filasVisibles();
    const etiquetas = filas.map((r) => hora(r.Intervalo));

    // El pronóstico no se lee solo: al lado van lo que pronosticó el cliente y
    // lo que efectivamente entró. Las tres son NUESTRAS llamadas —el forecast
    // del cliente ya trae el reparto adentro— así que son comparables.
    const skills = new Set(((poolActual() || {}).skills || []).map((x) => x.skill_id));
    const real = porIntervalo(estado.cotejo.real, skills);
    const cliente = porIntervalo(estado.cotejo.cliente, skills);
    // Vienen ya resueltos por intervalo desde la misma vista que usa el tablero
    // de la operación: deduplicados por persona y en operadores-equivalentes, no
    // en cabezas. Acá no hay nada que agregar ni que filtrar por skill.
    // Con el reparto por origen, en la misma forma que las filas de Comparación:
    // así se leen con las mismas funciones (`conectadosPartidos`, `origenConectados`)
    // y los dos lugares no pueden contar distinto.
    const conectados = new Map(estado.cotejo.conectados.map((f) => [instante(f.Intervalo), {
      conectados: f.Operadores,
      conectados_pool: f.Telefonicos, conectados_refuerzo: f.Refuerzo,
      conectados_otras: f.Otras, conectados_digital: f.Digital, conectados_dedicada: f.Dedicada,
      conectados_pool_en_linea: f.TelefonicosEnLinea,
      conectados_refuerzo_en_linea: f.RefuerzoEnLinea,
      conectados_otras_en_linea: f.OtrasEnLinea,
      conectados_digital_en_linea: f.DigitalEnLinea,
      conectados_dedicada_en_linea: f.DedicadaEnLinea,
    }]));
    const cerrado = (r) => estado.cotejo.hasta !== null
                           && instante(r.Intervalo) <= estado.cotejo.hasta;

    // Los mismos colores que la pestaña Comparación: el real es el azul, el
    // nuestro el naranja y el del cliente el violeta punteado. Es la misma
    // entidad en los dos lugares, así que tiene que ser el mismo color.
    const llamadas = [];
    if (filas.some(cerrado)) {
      llamadas.push({
        label: "Real (lo que entró)",
        // Cero adentro de la ventana cerrada, hueco después: así la línea
        // termina donde termina el dato en vez de caer a cero el resto del día.
        data: filas.map((r) => (cerrado(r) ? real.get(instante(r.Intervalo)) || 0 : null)),
        borderColor: COLOR.real, backgroundColor: COLOR.real,
      });
    }
    llamadas.push({
      label: "Nuestro pronóstico",
      data: filas.map((r) => Number(r.Llamadas)),
      borderColor: COLOR.pronost, backgroundColor: COLOR.pronost,
    });
    if (filas.some((r) => cliente.has(instante(r.Intervalo)))) {
      llamadas.push({
        label: "Forecast del cliente",
        data: filas.map((r) => cliente.get(instante(r.Intervalo)) || 0),
        borderColor: COLOR.produccion, backgroundColor: COLOR.produccion,
        borderDash: [2, 3],
      });
    }
    const ahora = indiceAhora(filas);
    dibujar("chart-llamadas", etiquetas, llamadas, llamadas.length > 1, null, { ahora });
    pintarCotejo(filas, real, cliente, cerrado);

    const series = [
      { label: "A planificar (a citar)", data: filas.map((r) => r.OperadoresPlanificar),
        borderColor: COLOR.planificar, backgroundColor: COLOR.planificar },
      { label: "Necesarios (en línea)", data: filas.map((r) => r.OperadoresLinea),
        borderColor: COLOR.linea, backgroundColor: COLOR.linea,
        borderDash: [3, 3], borderWidth: 1.5 },
    ];
    if (filas.some((r) => r.OperadoresPlanificados !== null &&
                          r.OperadoresPlanificados !== undefined)) {
      series.push({
        label: "Citados (a citar)",
        data: filas.map((r) => r.OperadoresPlanificados),
        borderColor: COLOR.citados, backgroundColor: COLOR.citados,
      });
      // Los citados en equivalentes por antigüedad y las extras habituales NO van
      // como líneas: Ignacio (2026-09-16) las sacó porque confundían. Siguen en la
      // tabla —el «≈» de Citados y la columna de detalle «Extras hab.»—.
    }
    if (filas.some((r) => r.RefuerzoDisponible !== null &&
                          r.RefuerzoDisponible !== undefined)) {
      series.push({
        label: `Citados + ${REF()} (a citar)`,
        data: filas.map((r) => (r.OperadoresPlanificados ?? 0) + (r.RefuerzoDisponible ?? 0)),
        borderColor: COLOR.refuerzo, backgroundColor: COLOR.refuerzo,
      });
    }
    if (filas.some((r) => conectados.has(instante(r.Intervalo)))) {
      // Hueco después del último intervalo cerrado, igual que la curva de llamadas
      // reales: la línea termina donde termina el dato.
      const partidos = filas.map((r) => conectados.get(instante(r.Intervalo)))
        .filter(Boolean).map(conectadosPartidos).filter(Boolean);
      const conectadoDe = (r, campo) => {
        if (!cerrado(r)) return null;
        const c = conectados.get(instante(r.Intervalo));
        if (!c) return 0;
        const p = conectadosPartidos(c);
        // Una media hora sin partir en un día partido queda como hueco: el total
        // logueado ahí mezclaría unidades en la misma línea.
        if (p) return p[campo];
        return partidos.length ? null : c.conectados;
      };
      if (partidos.length) {
        // Partidos como Citados / Citados + Digital, y EN LÍNEA: sin el tiempo en
        // break y otras pausas, que es lo que se compara contra «Necesarios».
        // Punteadas las dos, como toda serie en línea; + Digital con punto más
        // corto para distinguirla.
        const sinPausas = partidos.some((p) => p.sinPausas);
        series.push({
          label: `Conectados telefónicos (${sinPausas ? "en línea, sin pausas" : "en línea"})`,
          data: filas.map((r) => conectadoDe(r, "tel")),
          borderColor: COLOR.real, backgroundColor: COLOR.real,
          borderDash: [4, 3], borderWidth: 1.5,
        }, {
          label: `Conectados telefónicos + ${REF()} (en línea)`,
          data: filas.map((r) => conectadoDe(r, "conRef")),
          borderColor: COLOR.real, backgroundColor: COLOR.real,
          borderDash: [1, 3], borderWidth: 2,
        });
      } else {
        series.push({
          label: "Conectados (en línea)",
          data: filas.map((r) => conectadoDe(r, "tel")),
          borderColor: COLOR.real, backgroundColor: COLOR.real,
          borderDash: [4, 3], borderWidth: 1.5,
        });
      }
    }
    dibujar("chart-operadores", etiquetas, series, true, null, { ahora });

    pintarTabla(filas, real, cliente, cerrado, conectados);
    pintarExplicacionDePool();
  }

  // Lo que contesta la pantalla de un vistazo: cómo viene el día contra los dos
  // pronósticos. Sólo sobre los intervalos YA CERRADOS —comparar el acumulado
  // real de media mañana contra el pronóstico del día entero da siempre que
  // sobra gente, y es la lectura que hace tomar la decisión al revés.
  function pintarCotejo(filas, real, cliente, cerrado) {
    const destino = $("plan-cotejo");
    if (!destino) return;
    if (!filas.length) { destino.innerHTML = ""; return; }
    const cerradas = filas.filter(cerrado);
    const suma = (fs, fn) => fs.reduce((a, r) => a + (fn(r) || 0), 0);
    const partes = [];

    if (cerradas.length) {
      const entraron = suma(cerradas, (r) => real.get(instante(r.Intervalo)));
      const contra = (previsto, quien) => {
        if (!(previsto > 0)) return "";
        const d = entraron / previsto - 1;
        return `${Math.abs(d * 100).toFixed(0)}% ${d >= 0 ? "más" : "menos"} de lo ` +
               `que ${quien} (${num(previsto)})`;
      };
      const nuestro = contra(suma(cerradas, (r) => Number(r.Llamadas)), "pronosticamos");
      const suyo = contra(suma(cerradas, (r) => cliente.get(instante(r.Intervalo))),
                          "pronosticó el cliente");
      // Un día terminado se dice terminado. "Hasta las 00:00 entraron…" es lo
      // que sale de aplicarle a un día cerrado la frase pensada para hoy.
      const cabeza = cerradas.length === filas.length
        ? `El día cerró con <strong>${num(entraron)}</strong> llamadas`
        : `Hasta las ${finDeIntervalo(cerradas[cerradas.length - 1].Intervalo)} ` +
          `entraron <strong>${num(entraron)}</strong> llamadas`;
      partes.push(cabeza + (nuestro ? `: ${nuestro}` : "") +
                  (suyo ? ` y ${suyo}` : "") + ".");
    }

    const suyoDia = suma(filas, (r) => cliente.get(instante(r.Intervalo)));
    // El total del día sólo agrega algo mientras el día no haya terminado: con el
    // día cerrado repetiría los mismos números de la frase anterior.
    if (cerradas.length < filas.length) {
      partes.push(
        `Para el día entero pronosticamos ` +
        `<strong>${num(suma(filas, (r) => Number(r.Llamadas)))}</strong>` +
        (suyoDia > 0 ? ` y el cliente ${num(suyoDia)}` : "") + ".");
    }

    // El forecast del cliente no siempre cubre todas las colas. Cuando le falta
    // una, su curva queda por debajo por una razón que no es su pronóstico, y
    // sin decirlo la comparación lo favorece.
    const pool = poolActual();
    if (suyoDia > 0 && pool) {
      const cubiertos = new Set(estado.cotejo.cliente.map((f) => f.SkillID));
      const faltan = (pool.skills || [])
        .filter((x) => x.llamadas_30d > 0 && !cubiertos.has(x.skill_id));
      if (faltan.length) {
        partes.push(
          `<span class="text-warning-emphasis">El forecast del cliente no trae ` +
          `${faltan.map((x) => esc(x.nombre)).join(", ")}, así que en este pool ` +
          `queda por debajo por cobertura, no por pronóstico.</span>`);
      }
    }
    destino.innerHTML = partes.join(" ");
  }

  function pintarExplicacionDePool() {
    const p = poolActual();
    if (!p) { $("pool-explicacion").innerHTML = ""; return; }
    const skills = (p.skills || []).map((s) =>
      `${esc(s.nombre)} <span class="text-muted">(${s.llamadas_30d.toLocaleString()} llam./30d)</span>`
    ).join(" · ") || "<em>sin skills asignados</em>";
    const vacio = p.motivo_vacio
      ? `<br><span class="text-warning-emphasis"><i class="bi bi-info-circle me-1"></i>
         Este pool se ve vacío: ${esc(p.motivo_vacio)}</span>` : "";
    $("pool-explicacion").innerHTML =
      `<strong>${esc(p.nombre)}</strong> atiende: ${skills}.${vacio}`;
  }

  // ------------------------------------------------ zoom y barra de cada gráfico

  // chartjs-plugin-zoom llega por CDN: si no cargó, los gráficos se dibujan igual
  // y la barra no ofrece lo que no puede hacer.
  const HAY_ZOOM = () => !!(window.Chart && Chart.registry.plugins.get("zoom"));
  const FMT = new Intl.NumberFormat("es-AR", { maximumFractionDigits: 1 });

  // Línea vertical donde está el mouse: con 7 series en el mismo gráfico, sin
  // ella no se sabe a qué media hora corresponde el tooltip.
  const pluginCruz = {
    id: "planCruz",
    afterDatasetsDraw(chart) {
      const activos = chart.tooltip && chart.tooltip.getActiveElements();
      if (!activos || !activos.length) return;
      const x = activos[0].element.x;
      const { top, bottom } = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = "rgba(82, 81, 78, .35)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      ctx.restore();
    },
  };

  // Marca de «ahora» en el intradía de hoy: separa lo que ya pasó de lo que falta.
  const pluginAhora = {
    id: "planAhora",
    afterDatasetsDraw(chart, _args, opts) {
      if (opts.indice === null || opts.indice === undefined) return;
      const x = chart.scales.x.getPixelForValue(opts.indice);
      const { top, bottom, left, right } = chart.chartArea;
      if (x < left || x > right) return;          // fuera de la ventana con zoom
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = "#b3261e";
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#b3261e";
      ctx.font = "600 11px system-ui, sans-serif";
      ctx.textAlign = x > right - 40 ? "right" : "left";
      ctx.fillText(opts.texto || "Ahora", x + (ctx.textAlign === "right" ? -4 : 4), top + 11);
      ctx.restore();
    },
  };

  function estaConZoom(chart) {
    return !!(chart && typeof chart.isZoomedOrPanned === "function" && chart.isZoomedOrPanned());
  }

  function actualizarBarra(canvasId) {
    const barra = document.querySelector(`.plan-chart-barra[data-chart="${canvasId}"]`);
    if (!barra) return;
    const zoom = estaConZoom(estado.charts[canvasId]);
    barra.querySelector('[data-accion="reset"]').hidden = !zoom;
  }

  // El PNG sale con fondo: el canvas es transparente y pegado en un mail o una
  // planilla con fondo oscuro no se leería.
  function descargarPng(canvasId) {
    const chart = estado.charts[canvasId];
    if (!chart) return;
    const src = chart.canvas;
    const lienzo = document.createElement("canvas");
    lienzo.width = src.width;
    lienzo.height = src.height;
    const ctx = lienzo.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, lienzo.width, lienzo.height);
    ctx.drawImage(src, 0, 0);
    const a = document.createElement("a");
    const diaSel = $("f-dia") && $("f-dia").value ? `_${$("f-dia").value}` : "";
    a.download = `${canvasId.replace(/^chart-/, "planificador_")}${diaSel}.png`;
    a.href = lienzo.toDataURL("image/png");
    a.click();
  }

  // Ampliado = el mismo gráfico sobre toda la ventana (no una copia): Chart.js
  // escucha el tamaño del contenedor y se redibuja solo, con el zoom intacto.
  function ampliar(canvasId, si) {
    const cont = $(canvasId).parentElement;
    const abierto = si ?? !cont.classList.contains("expandido");
    document.querySelectorAll(".plan-chart.expandido").forEach((c) => c.classList.remove("expandido"));
    document.body.classList.toggle("plan-chart-abierto", abierto);
    if (abierto) cont.classList.add("expandido");
    const btn = cont.querySelector('[data-accion="ampliar"]');
    if (btn) {
      btn.innerHTML = `<i class="bi bi-${abierto ? "fullscreen-exit" : "arrows-fullscreen"}"></i>`;
      btn.title = abierto ? "Cerrar (Esc)" : "Ampliar";
    }
  }

  function asegurarBarra(canvasId) {
    const cont = $(canvasId).parentElement;
    if (cont.querySelector(".plan-chart-barra")) return;
    const card = cont.closest(".card");
    const titulo = card && card.querySelector(".card-header .fw-semibold");
    const barra = document.createElement("div");
    barra.className = "plan-chart-barra";
    barra.dataset.chart = canvasId;
    barra.innerHTML = `
      <span class="plan-chart-titulo">${esc(titulo ? titulo.textContent.trim() : "")}</span>
      <span class="plan-chart-ayuda" title="Arrastrá sobre el gráfico para acercar un tramo · Ctrl + rueda para acercar o alejar · Shift + arrastrar para moverte · doble clic para volver a verlo entero · clic en la leyenda para ocultar una serie">
        <i class="bi bi-zoom-in"></i></span>
      <button type="button" class="btn btn-light btn-sm" data-accion="reset" hidden
              title="Ver el gráfico entero (doble clic)"><i class="bi bi-arrow-counterclockwise me-1"></i>Quitar zoom</button>
      <button type="button" class="btn btn-light btn-sm" data-accion="png"
              title="Descargar como imagen" aria-label="Descargar como imagen"><i class="bi bi-download"></i></button>
      <button type="button" class="btn btn-light btn-sm" data-accion="ampliar"
              title="Ampliar" aria-label="Ampliar"><i class="bi bi-arrows-fullscreen"></i></button>`;
    if (!HAY_ZOOM()) barra.querySelector(".plan-chart-ayuda").hidden = true;
    barra.addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-accion]");
      if (!b) return;
      const chart = estado.charts[canvasId];
      if (b.dataset.accion === "reset" && chart) { chart.resetZoom(); actualizarBarra(canvasId); }
      if (b.dataset.accion === "png") descargarPng(canvasId);
      if (b.dataset.accion === "ampliar") ampliar(canvasId);
    });
    cont.appendChild(barra);
    $(canvasId).addEventListener("dblclick", () => {
      const chart = estado.charts[canvasId];
      if (estaConZoom(chart)) { chart.resetZoom(); actualizarBarra(canvasId); }
    });
  }

  // ◀ ▶ al lado de los selectores de día: recorrer una semana de a un día sin
  // abrir la lista cada vez.
  function conFlechas(selectId) {
    const sel = $(selectId);
    if (!sel || sel.parentElement.classList.contains("input-group")) return;
    const grupo = document.createElement("div");
    grupo.className = "input-group input-group-sm flex-nowrap w-auto grupo-dia";
    const flecha = (paso, icono, texto) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-outline-secondary";
      b.title = texto;
      b.setAttribute("aria-label", texto);
      b.innerHTML = `<i class="bi bi-chevron-${icono}"></i>`;
      b.addEventListener("click", () => {
        const i = sel.selectedIndex + paso;
        if (i < 0 || i >= sel.options.length) return;
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event("change"));
      });
      return b;
    };
    sel.replaceWith(grupo);
    grupo.append(flecha(-1, "left", "Día anterior"), sel, flecha(1, "right", "Día siguiente"));
  }

  // Se cierra con Esc o con un clic afuera, como cualquier ventana.
  const cerrarAmpliado = () => {
    const abierto = document.querySelector(".plan-chart.expandido canvas");
    if (abierto) ampliar(abierto.id, false);
  };
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") cerrarAmpliado(); });
  document.addEventListener("click", (ev) => {
    if (document.body.classList.contains("plan-chart-abierto") &&
        !ev.target.closest(".plan-chart.expandido")) cerrarAmpliado();
  });

  // `extra.ahora`: índice de la media hora en curso, para marcarla.
  function dibujar(canvasId, etiquetas, series, conLeyenda, tituloTooltip, extra = {}) {
    // Si se redibuja el mismo eje (otro pool, el mismo día), el zoom se conserva:
    // se estaba mirando ese tramo y cambiar de pool no es pedir verlo entero.
    const previo = estado.charts[canvasId];
    let ventana = null;
    if (previo && estaConZoom(previo) &&
        JSON.stringify(previo.data.labels) === JSON.stringify(etiquetas)) {
      const x = previo.scales.x;
      ventana = { min: x.min, max: x.max };
    }
    if (previo) previo.destroy();
    asegurarBarra(canvasId);
    const zoom = HAY_ZOOM() ? {
      zoom: {
        drag: { enabled: true, threshold: 8, backgroundColor: "rgba(42, 120, 214, .12)",
                borderColor: "rgba(42, 120, 214, .6)", borderWidth: 1 },
        // La rueda sola sigue scrolleando la página: el zoom con rueda va con Ctrl.
        wheel: { enabled: true, modifierKey: "ctrl" },
        pinch: { enabled: true },
        mode: "x",
        onZoomComplete: () => actualizarBarra(canvasId),
      },
      pan: { enabled: true, mode: "x", modifierKey: "shift",
             onPanComplete: () => actualizarBarra(canvasId) },
      // Nunca menos de 4 puntos a la vista: más cerca no se lee nada nuevo.
      limits: { x: { min: "original", max: "original", minRange: 3 } },
    } : undefined;
    estado.charts[canvasId] = new Chart($(canvasId), {
      plugins: [pluginCruz, pluginAhora],
      type: "line",
      data: {
        labels: etiquetas,
        datasets: series.map((s) => ({
          ...s,
          borderWidth: s.borderWidth ?? 2,          // marcas finas: el dato, no la tinta
          pointRadius: 0, pointHoverRadius: 5, tension: 0.25, fill: false,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        // Lugar arriba para la barra del gráfico, así no tapa el pico de las curvas.
        layout: { padding: { top: 26 } },
        interaction: { mode: "index", intersect: false },   // cruz + tooltip
        plugins: {
          // Una sola serie no necesita leyenda: el título de la tarjeta la nombra.
          legend: { display: conLeyenda, position: "bottom",
                    labels: { boxWidth: 10, boxHeight: 10, color: COLOR.texto } },
          tooltip: { callbacks: {
            title: (t) => (tituloTooltip ? tituloTooltip(t)
                                         : `${$("f-dia").value} ${t[0].label}`),
            label: (c) => `${c.dataset.label}: ${
              c.parsed.y === null || c.parsed.y === undefined ? "—" : FMT.format(c.parsed.y)}`,
          } },
          ...(zoom ? { zoom } : {}),
          planAhora: { indice: extra.ahora ?? null },
        },
        scales: {
          x: { grid: { color: COLOR.grid, drawTicks: false },
               ticks: { color: COLOR.texto, maxTicksLimit: 12, autoSkip: true } },
          // Un solo eje y, siempre desde cero: arrancarlo en otro lado exagera
          // las diferencias.
          y: { beginAtZero: true, grid: { color: COLOR.grid },
               ticks: { color: COLOR.texto, precision: 0, callback: (v) => FMT.format(v) } },
        },
      },
    });
    if (ventana) estado.charts[canvasId].zoomScale("x", ventana, "none");
    actualizarBarra(canvasId);
  }

  function limpiarGraficos() {
    Object.values(estado.charts).forEach((c) => c.destroy());
    estado.charts = {};
    document.querySelectorAll('.plan-chart-barra [data-accion="reset"]')
      .forEach((b) => { b.hidden = true; });
    $("tabla-intervalos").querySelector("tbody").innerHTML = "";
    if ($("plan-cotejo")) $("plan-cotejo").innerHTML = "";
  }

  function claseMotivo(motivo) {
    if (!motivo) return "";
    if (motivo.startsWith("abandono")) return "motivo-abandono";
    if (motivo.includes("ocupación")) return "motivo-ocup";
    return "motivo-nds";
  }

  // Los citados son personas. Con el rinde por antigüedad medido va al lado lo que
  // equivalen, que es contra lo que se calcula la brecha.
  function celdaCitados(r) {
    if (r.OperadoresPlanificados === null || r.OperadoresPlanificados === undefined) return "—";
    if (r.CitadosEquivalentes === null || r.CitadosEquivalentes === undefined) {
      return String(r.OperadoresPlanificados);
    }
    const eq = Number(r.CitadosEquivalentes);
    const titulo = `${r.OperadoresPlanificados} personas con turno equivalen a ${num(eq, 1)}: ` +
      "la gente en sus primeras semanas de piso tarda más por llamada. " +
      `La brecha se calcula sobre ${Math.round(eq)}.`;
    return `<span class="con-cuenta" title="${esc(titulo)}">${r.OperadoresPlanificados}` +
      ` <small class="text-muted">≈${num(eq, 1)}</small></span>`;
  }

  // De «en línea» a «a citar» hay tres descuentos encadenados, y el grande NO es
  // el ausentismo: medido sobre 30 días, en hábiles la cadena multiplica por 1,27
  // y 1,15 de eso es disponibilidad y break (gente logueada que no está sobre la
  // cola). La cuenta se muestra partida en esos dos tramos para que se vea.
  // Los factores vienen con la configuración de ahora; si la cuenta no da lo
  // guardado, la config cambió después de calcular y se dice eso en su lugar.
  function celdaPlanificar(r) {
    const valor = `<td class="text-end fw-semibold">${r.OperadoresPlanificar}</td>`;
    const linea = Number(r.OperadoresLinea);
    if (!linea || !r.Disponibilidad) return valor;
    const br = r.Break || 0;
    const aus = r.Ausentismo || 0;
    const logueados = linea / r.Disponibilidad / (1 - br);
    // En el MISMO orden que `dimensionar_intervalo` (disponibilidad, ausentismo,
    // break): con otro orden el último decimal cambia y un entero justo salta.
    const bruto = linea / r.Disponibilidad / (1 - aus) / (1 - br);
    // De madrugada se redondea para abajo, sin bajar nunca de los de en línea.
    const abajo = r.Redondeo === "abajo";
    const redondeado = abajo ? Math.max(linea, Math.floor(bruto + 1e-9)) : Math.ceil(bruto);
    const titulo = redondeado !== Number(r.OperadoresPlanificar)
      ? "La configuración cambió después de calcular este plan: recalcular para ver la cuenta."
      : `${linea} en línea ÷ disponibilidad ${pct(r.Disponibilidad, 0)}`
        + (br ? ` ÷ (1 − break ${pct(br, 1)})` : "")
        + ` = ${num(logueados, 1)} logueados (pausas y break, no es ausentismo).`
        + ` ÷ (1 − ausentismo ${pct(aus, 1)}) = ${num(bruto, 2)}`
        + ` → ${r.OperadoresPlanificar} a citar`
        + (abajo ? " (redondeado para abajo: en esta franja no se suma una persona por una fracción)." : ".");
    return `<td class="text-end fw-semibold"><span class="con-cuenta" title="${esc(titulo)}">` +
      `${r.OperadoresPlanificar}</span></td>`;
  }

  function celdaBrecha(r) {
    if (r.Brecha === null || r.Brecha === undefined) return '<td class="text-end">—</td>';
    const clase = r.Brecha < 0 ? "brecha-falta" : "brecha-sobra";
    const signo = r.Brecha > 0 ? "+" : "";
    return `<td class="text-end ${clase}">${signo}${r.Brecha}</td>`;
  }

  // Lo que sigue faltando si se pasa a la línea a la gente de Digital con turno.
  // Cero con brecha negativa es "se tapa con Digital": se dice, en vez de dejar un
  // cero que parece que no faltaba nada.
  function celdasRefuerzo(r) {
    if (r.RefuerzoDisponible === null || r.RefuerzoDisponible === undefined) {
      return '<td class="text-end col-detalle">—</td><td class="text-end col-detalle">—</td>';
    }
    const titulo = r.RefuerzoCubre
      ? `Faltan ${-r.Brecha}: ${REF()} tiene ${r.RefuerzoDisponible} con turno y cubre ${r.RefuerzoCubre}`
      : `${REF()} tiene ${r.RefuerzoDisponible} con turno`;
    const disp = `<td class="text-end text-muted col-detalle" title="${esc(titulo)}">${r.RefuerzoDisponible}</td>`;
    if (r.FaltanteNeto === null || r.FaltanteNeto === undefined || !(r.Brecha < 0)) {
      return disp + '<td class="text-end col-detalle">—</td>';
    }
    return disp + (r.FaltanteNeto > 0
      ? `<td class="text-end brecha-falta col-detalle" title="${esc(titulo)}">−${r.FaltanteNeto}</td>`
      : `<td class="text-end brecha-sobra col-detalle" title="${esc(titulo)}">cubre</td>`);
  }

  // Real contra un pronóstico, con la misma cuenta que el desvío de Comparación
  // (real / pronóstico − 1). Sin color: en media hora con pocas llamadas el
  // porcentaje salta mucho y el rojo del 10% pintaría medio día.
  function desvioTxt(entro, previsto) {
    if (!(previsto > 0)) return "—";
    const d = entro / previsto - 1;
    return `${d > 0 ? "+" : ""}${(d * 100).toFixed(0)}%`;
  }

  function pintarTabla(filas, real, cliente, cerrado, conectados) {
    const pool = Number($("f-pool") ? $("f-pool").value : 0);
    const diaSel = $("f-dia") ? $("f-dia").value : "";
    const tipoDia = diaSel ? tipoDeDiaPlan(diaSel) : "habil";
    const extrasPorHora = obtenerExtrasPorHora(pool, tipoDia);

    const iAhora = indiceAhora(filas);
    $("tabla-intervalos").querySelector("tbody").innerHTML = filas.map((r, i) => {
      const k = instante(r.Intervalo);
      const hStr = hora(r.Intervalo);
      const extraVal = extrasPorHora[hStr];
      const extraTxt = (extraVal !== undefined && extraVal !== null)
        ? (extraVal > 0 ? `+${num(extraVal, 1)}` : num(extraVal, 1))
        : "—";
      const extraTitulo = (extraVal !== undefined && extraVal !== null)
        ? `Extras habituales: ${extraTxt} personas promedio en ${tipoDia} a las ${hStr} (últimas 8 semanas). Informativo: no altera el plan.`
        : "Sin datos de extras habituales";

      return `
      <tr${i === iAhora ? ' class="fila-ahora" title="Media hora en curso"' : ""}>
        <td>${hStr}</td>
        <td class="text-end">${num(r.Llamadas, 1)}</td>
        <td class="text-end col-detalle">${cliente.has(k) ? num(cliente.get(k), 1) : "—"}</td>
        <td class="text-end">${cerrado(r) ? num(real.get(k) || 0, 1) : "—"}</td>
        <td class="text-end text-nowrap">${cerrado(r)
          ? `${desvioTxt(real.get(k) || 0, Number(r.Llamadas))} <small class="text-muted">/ ${
              cliente.has(k) ? desvioTxt(real.get(k) || 0, cliente.get(k)) : "—"}</small>`
          : "—"}</td>
        <td class="text-end col-detalle">${num(r.TmoSeg)}s</td>
        <td class="text-end col-detalle">${num(r.Trafico, 1)}</td>
        <td class="text-end">${r.OperadoresLinea}</td>
        ${cerrado(r) && conectados.has(k)
          ? celdaConectados(conectados.get(k), 1, "text-end", origenConectados(conectados.get(k)))
          : '<td class="text-end">—</td>'}
        ${celdaPlanificar(r)}
        <td class="text-end">${celdaCitados(r)}</td>
        <td class="text-end col-detalle text-muted" title="${esc(extraTitulo)}">${extraTxt}</td>
        ${celdaBrecha(r)}
        ${celdasRefuerzo(r)}
        <td class="text-end col-detalle">${pct(r.NdsContractual)}</td>
        <td class="text-end col-detalle">${r.AsaSeg === null || r.AsaSeg === undefined
                                ? "—" : Math.round(r.AsaSeg) + "s"}</td>
        <td class="text-end col-detalle">${pct(r.Abandono, 2)}</td>
        <td class="text-end col-detalle">${pct(r.Ocupacion)}</td>
        <td class="small ${claseMotivo(r.Motivo)}">${esc(r.Motivo)}</td>
      </tr>`;
    }).join("");
    irAFilaAhora(`${pool}|${diaSel}`);
  }

  // Al abrir hoy (o volver a hoy desde otro día o pool), la tabla se para en la
  // media hora en curso. Sólo al cambiar de vista: el refresco automático no
  // mueve una tabla que alguien está leyendo.
  let vistaTablaAnterior = null;
  function irAFilaAhora(clave) {
    if (clave === vistaTablaAnterior) return;
    vistaTablaAnterior = clave;
    const wrap = $("tabla-intervalos-wrap");
    const fila = wrap && wrap.querySelector("tr.fila-ahora");
    if (!fila) { if (wrap) wrap.scrollTop = 0; return; }
    const cabecera = wrap.querySelector("thead");
    // Dos filas de contexto arriba: se ve de dónde viene el día.
    wrap.scrollTop = Math.max(0, fila.offsetTop - (cabecera ? cabecera.offsetHeight : 0)
                                  - 2 * fila.offsetHeight);
  }

  // -------------------------------------------------------- seguimiento del día

  let ultimoSeguimiento = null;
  let timerSeguimiento = null;

  async function cargarSeguimiento() {
    try {
      const s = await pedir(`seguimiento?campana_id=${estado.campana}`);
      ultimoSeguimiento = Date.now();
      $("seguimiento").innerHTML = s.hay_corrida ? tarjetaSeguimiento(s) : "";
      if (s.hay_corrida && EDITA && $("btn-proponer-reparto")) {
        $("btn-proponer-reparto").addEventListener("click", () => {
          if (window.PlanificadorInsumos && s.share) {
            window.PlanificadorInsumos.proponerReparto(
              s.share.implicito_hoy / s.share.aplicado, s.share
            );
          }
        });
      }
    } catch (e) {
      $("seguimiento").innerHTML = "";
    }
  }

  if (!timerSeguimiento) {
    timerSeguimiento = setInterval(() => {
      if (document.visibilityState === "visible") {
        cargarSeguimiento();
      }
    }, 30 * 60 * 1000);

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && ultimoSeguimiento) {
        if (Date.now() - ultimoSeguimiento >= 30 * 60 * 1000) {
          cargarSeguimiento();
        }
      }
    });
  }

  function tarjetaSeguimiento(s) {
    if (s.desvio === null || s.desvio === undefined) {
      return `<div class="alert alert-light border py-2 px-3 small mb-0">
                Todavía no hay suficientes intervalos cerrados hoy para medir el desvío.
              </div>`;
    }
    const arriba = s.desvio > 0;
    const tipo = !s.avisar ? "success" : (arriba ? "warning" : "info");
    const icono = !s.avisar ? "check-circle" : (arriba ? "arrow-up-right" : "arrow-down-right");
    const signo = arriba ? "+" : "";
    const horaAct = new Date().toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });

    // El share implícito es lo que distingue "el cliente tiene más llamadas" de
    // "nos están mandando una porción más grande". Se marca como provisorio a
    // propósito: la demanda real del cliente recién entra de noche.
    let causa = "";
    if (s.share) {
      const dif = s.share.implicito_hoy - s.share.aplicado;
      causa = Math.abs(dif) < 0.02 ? `
        <div class="small mt-1">El reparto viene en línea con el configurado
          (${pct(s.share.aplicado)}), así que el desvío es de la demanda del cliente.</div>`
        : `<div class="small mt-1">
             <strong>Hoy nos estaría llegando el ${pct(s.share.implicito_hoy)}</strong> del cliente
             contra el ${pct(s.share.aplicado)} configurado. Si se confirma esta noche, hay que
             actualizar el tramo de asignación: el desvío no es de volumen del cliente,
             es de reparto.
             <span class="text-muted">(provisorio: la demanda total del cliente carga de noche)</span>
             ${EDITA ? `
               <div class="mt-2">
                 <button class="btn btn-sm btn-outline-primary" id="btn-proponer-reparto">
                   <i class="bi bi-magic me-1"></i>Proponer reparto desde hoy
                 </button>
               </div>` : ""}
           </div>`;
    }

    // Línea de próximas horas hablando en gente a citar
    let lineaProximas = "";
    if (s.proximas_horas && s.proximas_horas.length > 0) {
      const momentos = s.proximas_horas.map((p) => new Date(p.intervalo));
      const minM = new Date(Math.min(...momentos));
      const maxM = new Date(Math.max(...momentos));
      const finM = new Date(maxM.getTime() + 30 * 60 * 1000);
      const franja = `${hora(minM)} a ${hora(finM)}`;

      let maxFaltan = 0;
      let picoItem = null;
      for (const p of s.proximas_horas) {
        const f = p.faltan ?? 0;
        if (f > maxFaltan || picoItem === null) {
          maxFaltan = f;
          picoItem = p;
        }
      }

      if (maxFaltan > 0 && picoItem) {
        const dig = picoItem.digital_con_turno ?? 0;
        const tras = picoItem.faltan_tras_digital ?? 0;
        lineaProximas = `<div class="small mt-2 pt-2 border-top">
          Si sigue así, de <strong>${franja}</strong> faltan hasta <strong>${maxFaltan} personas a citar</strong>
          (${REF()} con turno: ${dig}; tras ${REF()}: ${tras}).
        </div>`;
      } else {
        lineaProximas = `<div class="small mt-2 pt-2 border-top">
          Si sigue así, de <strong>${franja}</strong> con la gente citada alcanza.
        </div>`;
      }
    }

    return `<div class="alert alert-${tipo} py-2 px-3 mb-0">
      <div class="d-flex align-items-start gap-2">
        <i class="bi bi-${icono} mt-1"></i>
        <div class="w-100">
          <div class="d-flex justify-content-between align-items-center">
            <strong>El día viene ${signo}${(s.desvio * 100).toFixed(1)}% contra el pronóstico.</strong>
            <small class="text-muted">Actualizado ${horaAct}</small>
          </div>
          ${num(s.acumulado_real)} llamadas reales contra ${num(s.acumulado_pronosticado)}
          pronosticadas en los ${s.intervalos_cerrados} intervalos cerrados.
          ${s.proyeccion_cierre ? `Al cierre proyecta <strong>${num(s.proyeccion_cierre)}</strong>
            en vez de ${num(s.pronostico_dia)}.` : ""}
          ${causa}
          ${lineaProximas}
        </div>
      </div></div>`;
  }

  // ----------------------------------------------------------------- eventos

  async function cargarEventos() {
    try {
      const { eventos } = await pedir(`eventos?campana_id=${estado.campana}`);
      const lista = eventos || [];
      lista.sort((a, b) => {
        if (!a.Confirmado && b.Confirmado) return -1;
        if (a.Confirmado && !b.Confirmado) return 1;
        return new Date(b.Desde) - new Date(a.Desde);
      });

      const sinRevisar = lista.filter((e) => !e.Confirmado).length;
      const badgeContador = $("eventos-contador");
      if (badgeContador) {
        badgeContador.textContent = sinRevisar > 0 ? `${sinRevisar} sin revisar` : "al día";
        badgeContador.className = `badge ${sinRevisar > 0 ? "bg-warning text-dark" : "bg-success"}`;
      }

      const tbody = $("tabla-eventos").querySelector("tbody");
      tbody.innerHTML = lista.slice(0, 60).map((e) => {
        const estadoBadge = e.Confirmado
          ? `<span class="badge ${e.ExcluirDeEntrenamiento ? "bg-secondary" : "bg-info text-dark"}">
               ${e.ExcluirDeEntrenamiento ? "confirmado" : "descartado"}</span>`
          : `<span class="badge bg-warning text-dark">detectado</span>`;

        let accionHtml = "";
        if (EDITA) {
          if (!e.Confirmado) {
            accionHtml = `
              <td class="text-nowrap">
                <div class="d-flex align-items-center gap-1">
                  <select class="form-select form-select-sm py-0 ev-tipo" style="width: 120px; font-size: 0.75rem;" data-id="${e.EventoID}">
                    <option value="corte">corte de luz</option>
                    <option value="clima">clima</option>
                    <option value="feriado">feriado/puente</option>
                    <option value="operativo">operativo</option>
                    <option value="paro">paro del cliente</option>
                    <option value="otro" selected>otro</option>
                  </select>
                  <input type="text" class="form-control form-control-sm py-0 ev-desc" style="width: 140px; font-size: 0.75rem;" placeholder="Descripción (opcional)" value="${esc(e.Descripcion || '')}" data-id="${e.EventoID}">
                  <button class="btn btn-sm btn-primary py-0 px-2 btn-confirmar-ev" data-id="${e.EventoID}" title="Confirmar: queda excluido del entrenamiento">Confirmar</button>
                  <button class="btn btn-sm btn-outline-secondary py-0 px-2 btn-descartar-ev" data-id="${e.EventoID}" title="Descartar: día normal, NO se excluye">Descartar</button>
                </div>
              </td>`;
          } else {
            accionHtml = `<td class="small text-muted text-nowrap">Revisado</td>`;
          }
        }

        return `
          <tr>
            <td class="small text-nowrap">${dia(e.Desde)}</td>
            <td class="small">${esc(e.Tipo)}</td>
            <td class="text-end small">${e.Factor ? Number(e.Factor).toFixed(2) + "x" : "—"}</td>
            <td class="small">${esc(e.Descripcion)}</td>
            <td>${estadoBadge}</td>
            ${accionHtml}
          </tr>`;
      }).join("") ||
        `<tr><td colspan="${EDITA ? 6 : 5}" class="text-muted small p-3">
           Todavía no hay eventos. Se cargan solos al recalcular, con los días que
           el sistema detecta como atípicos.</td></tr>`;

      tbody.querySelectorAll(".btn-confirmar-ev").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.id;
          const evObj = lista.find((x) => String(x.EventoID) === String(id));
          if (!evObj) return;
          const fila = btn.closest("tr");
          const tipo = fila.querySelector(".ev-tipo").value;
          const desc = fila.querySelector(".ev-desc").value;
          await conBoton(btn, "Guardando…", async () => {
            await pedir(`eventos/${id}?campana_id=${estado.campana}`, {
              method: "PUT",
              body: JSON.stringify({
                desde: evObj.Desde,
                hasta: evObj.Hasta,
                tipo: tipo,
                descripcion: desc || null,
                factor: evObj.Factor,
                excluir_de_entrenamiento: true,
                confirmado: true,
              }),
            });
            $("avisos").innerHTML = aviso(
              "Día confirmado como evento: queda fuera del entrenamiento desde el próximo recálculo.",
              "success", "check-circle");
            await cargarEventos();
          }).catch((err) => {
            $("avisos").innerHTML = aviso(`No se pudo confirmar: ${esc(err.message)}`, "danger", "x-octagon");
          });
        });
      });

      tbody.querySelectorAll(".btn-descartar-ev").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.id;
          const evObj = lista.find((x) => String(x.EventoID) === String(id));
          if (!evObj) return;
          const fila = btn.closest("tr");
          const desc = fila.querySelector(".ev-desc").value;
          await conBoton(btn, "Guardando…", async () => {
            await pedir(`eventos/${id}?campana_id=${estado.campana}`, {
              method: "PUT",
              body: JSON.stringify({
                desde: evObj.Desde,
                hasta: evObj.Hasta,
                tipo: "normal",
                descripcion: desc || "Descartado: día normal",
                factor: evObj.Factor,
                excluir_de_entrenamiento: false,
                confirmado: true,
              }),
            });
            $("avisos").innerHTML = aviso(
              "Descartado: es un día normal y vuelve a contar para el entrenamiento.",
              "success", "check-circle");
            await cargarEventos();
          }).catch((err) => {
            $("avisos").innerHTML = aviso(`No se pudo descartar: ${esc(err.message)}`, "danger", "x-octagon");
          });
        });
      });

    } catch (e) { /* la tabla de eventos no puede tumbar la pantalla */ }
  }

  async function cargarCodigos() {
    try {
      const { codigos } = await pedir("config/codigos-payroll");
      const cuerpo = $("tabla-codigos").querySelector("tbody");
      cuerpo.innerHTML = (codigos || []).map((c) => `
        <tr>
          <td><code>${esc(c.Codigo)}</code></td>
          <td><span class="badge bg-light text-dark">${esc(c.Clase)}</span></td>
          <td class="small text-muted">${esc(c.Descripcion)}</td>
        </tr>`).join("") ||
        `<tr><td colspan="3" class="text-muted small p-3">
           Sin catálogo cargado: falta la migración 2026-09-03b.</td></tr>`;
    } catch (e) { /* opcional */ }
  }

  // ----------------------------------------------------------- configuración

  function pintarConfiguracion(cfg) {
    $("c-intervalo").value = cfg.intervalo_min ?? "";
    $("c-ocupacion").value = cfg.max_ocupacion ?? "";
    $("c-shrinkage").value = cfg.shrinkage ?? "";
    $("c-shrinkage-feriado").value = cfg.shrinkage_feriado ?? "";
    $("c-shrinkage-no-habil").value = cfg.shrinkage_no_habil ?? "";
    $("c-break").value = cfg.break_min_por_hora ?? 0;
    // El aviso aparece sólo cuando el break está prendido, que es cuando puede
    // estar descontándose dos veces contra la disponibilidad.
    $("c-break-aviso").innerHTML = (cfg.break_min_por_hora > 0)
      ? `Descuenta ${((cfg.break_min_por_hora / 60) * 100).toFixed(1)}% adicional.
         Verificá que la disponibilidad esté medida NETA de break o se cuenta dos veces.`
      : "";
    $("c-redondeo-desde").value = cfg.redondeo_abajo_desde ?? "";
    $("c-redondeo-hasta").value = cfg.redondeo_abajo_hasta ?? "";
    $("c-intradia").value = cfg.intradia_desde_hora ?? "";
    $("c-ancla-peso").value = cfg.ancla_mensual_peso ?? 0;
    $("c-ancla-desde").value = cfg.ancla_mensual_desde_dias ?? 7;
    $("c-paciencia").value = cfg.paciencia_seg ?? "";
    $("c-horizonte").value = cfg.procedencia?.paciencia_horizonte_seg ?? "";
    $("c-semanas").value = cfg.semanas_base ?? "";
    $("c-dias-nivel").value = cfg.dias_nivel ?? "";
    $("c-nivel-tipo-dia").checked = cfg.nivel_por_tipo_de_dia !== false;
    $("c-deriva-dias").value = cfg.reparto_deriva_dias ?? "";
    $("c-deriva-tope").value = cfg.reparto_deriva_tope ?? "";
    $("c-tipo-dia-dias").value = cfg.reparto_tipo_dia_dias ?? "";
    $("c-tipo-dia-tope").value = cfg.reparto_tipo_dia_tope ?? "";
    $("c-combinar-cliente").checked = cfg.combinar_cliente === true;
    // El estado de los pesos va al lado de la llave: prender la combinación sin
    // pesos medidos no hace nada, y sin decirlo acá parece que sí.
    const pesoNoHabil = cfg.combinar_cliente_peso_no_habil;
    $("c-combinar-fuente").textContent = pesoNoHabil
      ? `peso medido para sábados, domingos y feriados: ${Number(pesoNoHabil).toFixed(3)}`
        + (cfg.combinar_cliente_medido_en
           ? ` (${String(cfg.combinar_cliente_medido_en).slice(0, 10)})` : "")
      : "sin medir: la llave no hace nada hasta que se mida el peso, abajo, en «Combinar con el pronóstico que manda el cliente»";

    const p = cfg.procedencia || {};
    $("c-shrinkage-fuente").textContent = p.shrinkage_origen === "presencia"
      ? `medido como presencia en la línea: gente con turno de las telefónicas contra `
        + `los conectados de las telefónicas y de otras sub-campañas, sin ${REF()}`
        + (p.shrinkage_medido_en ? ` (aplicado el ${dia(p.shrinkage_medido_en)})` : "")
        + `. El que no está en la línea se descuenta; el que otra sub-campaña cubre, no.`
      : p.shrinkage_origen === "payroll"
      ? `medido sobre payroll: ${pct(p.shrinkage_ausentismo)} de ausentismo + `
        + `faltante dentro del turno, sobre el universo de la malla. Aparte hay `
        + `${pct(p.shrinkage_capacitacion)} de capacitación, que es número de `
        + `nómina y no se descuenta acá: el que está en capacitación no figura `
        + `entre los citados por RRHH.`
      : (p.shrinkage_origen === "manual" ? "puesto a mano" : "valor de arranque, sin medir");
    $("c-paciencia-fuente").textContent = p.paciencia_origen === "km"
      ? `Kaplan-Meier ajustado a ${p.paciencia_horizonte_seg}s`
      : (p.paciencia_origen === "manual" ? "puesta a mano" : "sin medir");

    pintarClima(cfg.clima || {});
    // La segunda opinión del nivel vive en la misma tarjeta que el clima porque
    // depende de él, pero el dato viene del cfg de la campaña y no del bloque
    // de clima. Se deshabilita junto con el clima: sin clima no tiene rasgos.
    if ($("c-nivel-gbdt")) {
      $("c-nivel-gbdt").checked = !!cfg.nivel_gbdt;
      $("c-nivel-gbdt").disabled = $("c-clima-activo").disabled;
      $("c-nivel-peso").value = cfg.nivel_gbdt_peso ?? "";
      $("c-nivel-peso").disabled = $("c-clima-activo").disabled;
    }
    if ($("c-elasticidad")) {
      $("c-elasticidad").checked = !!cfg.clima_elasticidad_tipo_dia;
      $("c-elasticidad").disabled = $("c-clima-activo").disabled;
    }

    estado.franjas = (cfg.disponibilidad || []).map((f) => ({ ...f }));
    pintarFranjas();
    pintarPools(cfg);
    pintarPuestos(cfg.puestos_malla || {});
  }

  // Quién de la nómina cuenta como dotación telefónica. Se muestran los puestos
  // que TIENEN horas de piso y no el catálogo entero: la pregunta no es qué
  // puestos existen sino a quién se está contando, a quién se deja afuera y
  // cuánto pesa cada uno.
  function pintarPuestos(pm) {
    const cuerpo = $("tabla-puestos");
    if (!cuerpo) return;
    const filas = pm.puestos || [];
    const marca = (v) => {
      if (v === null || v === undefined) {
        return '<span class="text-warning-emphasis fw-semibold">sin clasificar</span>';
      }
      return v ? '<span class="text-success-emphasis">sí</span>'
               : '<span class="text-muted">no</span>';
    };
    cuerpo.querySelector("tbody").innerHTML = filas.map((f) => `
      <tr${f.en_malla === false ? ' class="text-muted"' : ""}>
        <td>${esc(f.nombre)}</td>
        <td class="text-end">${f.personas.toLocaleString()}</td>
        <td class="text-end">${Math.round(f.horas).toLocaleString()}</td>
        <td class="small">${marca(f.en_malla)}</td>
      </tr>`).join("") ||
      `<tr><td colspan="4" class="text-muted small p-3">${
        esc(pm.motivo || "Sin horas de piso en el período.")}</td></tr>`;

    const resumen = $("puestos-resumen");
    if (!resumen) return;
    if (!pm.disponible && filas.length) {
      // La tabla de abajo ya muestra quién aporta cuántas horas, que es
      // exactamente el dato con el que alguien decide aplicar la migración. No
      // se adelanta acá cuáles quedarían afuera: la regla vive en la migración y
      // repetirla en el navegador sería una segunda versión de la verdad.
      resumen.innerHTML = aviso(
        `<strong>Todavía cuenta a todos.</strong> Falta correr ` +
        `<code>2026-09-09e_planificador_puestos_malla.sql</code>; hasta entonces ` +
        `la malla citada incluye a los puestos que no atienden.`,
        "info", "info-circle");
      return;
    }
    const sin = pm.sin_clasificar || [];
    resumen.innerHTML =
      (pm.disponible
        ? `<span class="text-muted">Cuentan ${Math.round(pm.horas_en_malla || 0).toLocaleString()} h
           de ${Math.round((pm.horas_en_malla || 0) + (pm.horas_fuera || 0)).toLocaleString()} h
           de piso del período.</span>` : "") +
      (sin.length ? aviso(
        `<strong>Sin clasificar:</strong> ${sin.map(esc).join(", ")}. ` +
        `Por ahora no cuentan en la malla; si alguno atiende, marcalo.`) : "");
  }

  function pintarClima(c) {
    if (!c.disponible) {
      $("c-clima-activo").checked = false;
      $("c-clima-activo").disabled = true;
      $("c-clima-estado").innerHTML = aviso(
        `El clima todavía no está disponible: ${esc(c.motivo || "")}. ` +
        `Corré <code>scripts/migrations/2026-09-07d_planificador_clima.sql</code>.`,
        "info", "info-circle");
      return;
    }
    $("c-clima-activo").checked = !!c.activo;
    $("c-clima-lat").value = c.lat ?? "";
    $("c-clima-lon").value = c.lon ?? "";

    const suficiente = (c.dias_observados || 0) >= (c.minimo_para_ajustar || 0);
    if (!c.dias_cargados) {
      $("c-clima-estado").innerHTML = aviso(
        "No hay clima cargado todavía. Corré <code>python scripts/clima_voltara.py " +
        `--campana ${estado.campana} --desde 2022-01-01</code> para traer el histórico, ` +
        "y después dejalo en el cron junto con la descarga del IVR.", "warning", "cloud-download");
      return;
    }
    $("c-clima-estado").innerHTML = aviso(
      `<strong>${num(c.dias_cargados)} días cargados</strong> ` +
      `(${dia(c.desde)} a ${dia(c.hasta)}), de los cuales ` +
      `<strong>${num(c.dias_observados)} observados</strong> hasta el ` +
      `${dia(c.hasta_observado)}. ` +
      (suficiente
        ? `Alcanza para ajustar el modelo (mínimo ${num(c.minimo_para_ajustar)} días).`
        : `Hacen falta al menos ${num(c.minimo_para_ajustar)} días observados para ajustar.`) +
      (c.activo ? "" : " <strong>Está apagado</strong>: probalo primero arriba, en " +
                       "«Probar un cambio»."),
      suficiente ? (c.activo ? "success" : "info") : "warning",
      suficiente ? (c.activo ? "check-circle" : "info-circle") : "exclamation-triangle");
  }

  async function guardarClima() {
    const vacio = (v) => v === "" ? null : Number(v);
    await conBoton($("btn-guardar-clima"), "Guardando…", async () => {
      try {
        await pedir(`config/campana?campana_id=${estado.campana}`, {
          method: "PUT",
          body: JSON.stringify({
            clima_activo: $("c-clima-activo").checked,
            clima_lat: vacio($("c-clima-lat").value),
            clima_lon: vacio($("c-clima-lon").value),
            clima_elasticidad_tipo_dia: $("c-elasticidad")
              ? $("c-elasticidad").checked : undefined,
            nivel_gbdt: $("c-nivel-gbdt") ? $("c-nivel-gbdt").checked : undefined,
            nivel_gbdt_peso: $("c-nivel-peso") ? vacio($("c-nivel-peso").value) : undefined,
          }),
        });
        await cargarTodo();
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudo guardar el clima: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  function pintarPools(cfg) {
    const sinPool = (cfg.sin_pool || []);
    $("config-pools").innerHTML = (cfg.pools || []).map((p) => {
      const skills = (p.skills || []).map((s) => `
        <tr data-skill="${s.skill_id}">
          <td class="text-nowrap">${esc(s.nombre)}</td>
          <td class="text-end">${s.llamadas_30d.toLocaleString()}</td>
          ${campoSkill(s, "objetivo_nds", s.objetivo_nds, "0.01", 0, 1)}
          ${campoSkill(s, "umbral_seg", s.umbral_seg, "1", 1, 600)}
          ${campoSkill(s, "max_abandono", s.max_abandono, "0.001", 0, 1)}
          ${campoSkill(s, "paciencia_seg", s.paciencia_seg, "1", 1, 3600)}
          ${campoSkill(s, "max_asa_seg", s.max_asa_seg, "1", 1, 3600)}
          ${campoSkill(s, "objetivo_nds_2", s.objetivo_nds_2, "0.01", 0, 1)}
          ${campoSkill(s, "umbral_seg_2", s.umbral_seg_2, "1", 1, 3600)}
          <td class="text-center"><input type="checkbox" class="form-check-input"
                data-skill-campo="prioridad" ${s.prioridad ? "checked" : ""}
                ${EDITA ? "" : "disabled"}></td>
          <td class="text-center"><input type="checkbox" class="form-check-input"
                data-skill-campo="activo" ${s.activo ? "checked" : ""}
                ${EDITA ? "" : "disabled"}></td>
        </tr>`).join("");
      const origen = (p.origen_rrhh || []).map((o) =>
        `<span class="badge bg-light text-dark border me-1 mb-1">${esc(o.sub_campana || o.campana_rrhh_id)}</span>`
      ).join("") || '<span class="text-muted small">sin sub-campañas de RRHH asociadas: no se puede comparar contra la malla</span>';

      return `
        <div class="mb-4">
          <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
            <h6 class="mb-1">${esc(p.nombre)}
              <span class="text-muted fw-normal small">
                — ${p.llamadas_30d.toLocaleString()} llamadas en 30 días,
                mínimo ${p.min_operadores} operador${p.min_operadores === 1 ? "" : "es"}
              </span>
            </h6>
          </div>
          ${p.motivo_vacio ? `<div class="small text-warning-emphasis mb-1">
             <i class="bi bi-info-circle me-1"></i>Se ve vacío en el plan: ${esc(p.motivo_vacio)}</div>` : ""}
          <div class="table-responsive">
          <table class="table table-sm mb-2 align-middle" data-pool="${p.pool_id}">
            <thead class="table-light"><tr>
              <th>Skill</th><th class="text-end">Llam. 30d</th>
              <th class="text-end">Objetivo NDS</th><th class="text-end">Umbral (s)</th>
              <th class="text-end">Techo abandono</th><th class="text-end">Paciencia (s)</th>
              <th class="text-end">ASA máx (s)</th>
              <th class="text-end">NDS 2º</th><th class="text-end">Umbral 2º (s)</th>
              <th class="text-center">Prioridad</th><th class="text-center">Activo</th>
            </tr></thead>
            <tbody>${skills || '<tr><td colspan="11" class="text-muted small">sin skills</td></tr>'}</tbody>
          </table>
          </div>
          <details class="porque mb-2"><summary>Cómo se leen estas columnas</summary><div>
            <strong>Techo de abandono y nivel de atención son el mismo dato</strong>
            (nivel = 1 − abandono), así que se carga uno solo: si estaban los dos,
            llenar ambos con valores "coherentes" pedía la misma restricción dos
            veces. Un techo de 0,5% es un nivel de atención de 99,5%. El plan
            informa las dos lecturas por intervalo.<br><br>
            <strong>Vacío no es cero.</strong> Vacío significa «no lo restrinjas»;
            un techo de abandono en 0 exige abandono nulo y pide dotación
            infinita.<br><br>
            <strong>Prioridad</strong> es la cola que el ACD atiende primero: cuando
            entra una de esas, es la que sale. Cambia sólo cómo se verifica su techo
            de abandono —no espera detrás de las demás— y no el dimensionamiento del
            pool, que sigue siendo el agregado: con la misma gente, una llamada
            prioritaria abandona mucho menos que una común.<br><br>
            <strong>La paciencia es por skill</strong>, y con razón: el que se
            quedó sin luz espera mucho más que un electrodependiente. Vacío
            significa «usá la de la campaña», que es sólo el valor por defecto.
            Para dimensionar la cola compartida se pondera por llamadas; para
            verificar el techo de abandono se usa la de cada skill por separado.
          </div></details>
          ${EDITA && (p.skills || []).length ? `
            <button class="btn btn-sm btn-outline-primary mb-2"
                    data-guardar-skills="${p.pool_id}">Guardar objetivos</button>` : ""}
          <div class="small"><span class="text-muted">Gente de RRHH:</span> ${origen}</div>
        </div>`;
    }).join("") + (sinPool.length ? `
        <div class="porque">
          <strong>Fuera del dimensionamiento:</strong>
          ${sinPool.map((s) => `${esc(s.nombre)} (${s.llamadas_30d.toLocaleString()} llam./30d)`).join(", ")}.
          Son colas sin volumen; si se las asignara a un pool, pedirían cobertura
          mínima para llamadas que no existen.
        </div>` : "");

    $("config-pools").querySelectorAll("[data-guardar-skills]").forEach((b) => {
      b.addEventListener("click", () => guardarSkills(Number(b.dataset.guardarSkills), b));
    });
  }

  // Una celda editable de la tabla de skills. Vacío = sin restricción, que es
  // distinto de cero: un techo de abandono en 0 exige abandono nulo y pide
  // dotación infinita, mientras que vacío significa "no lo restrinjas".
  function campoSkill(s, campo, valor, paso, min, max) {
    return `<td class="text-end"><input type="number"
              class="form-control form-control-sm text-end" style="min-width:5.5rem"
              data-skill-campo="${campo}" step="${paso}" min="${min}" max="${max}"
              value="${valor ?? ""}" placeholder="—" ${EDITA ? "" : "disabled"}></td>`;
  }

  async function guardarSkills(poolId, btn) {
    const tabla = $("config-pools").querySelector(`table[data-pool="${poolId}"]`);
    if (!tabla) return;
    const filas = [...tabla.querySelectorAll("tbody tr[data-skill]")];
    await conBoton(btn, "Guardando…", async () => {
      try {
        // El backend REEMPLAZA la fila entera, así que hay que mandar todos los
        // campos y no sólo los que cambiaron. Y `pool_id` va sí o sí: sin él el
        // skill sale del dimensionamiento sin que nadie lo haya pedido.
        for (const fila of filas) {
          // `min_nivel_atencion_b` se manda SIEMPRE en null: es la misma
          // restricción que el techo de abandono expresada al revés, y dejar un
          // valor viejo cargado la aplicaría dos veces —y encima con Erlang B,
          // que ignora la paciencia y es sistemáticamente pesimista—.
          const cuerpo = { pool_id: poolId, min_nivel_atencion_b: null };
          fila.querySelectorAll("[data-skill-campo]").forEach((el) => {
            cuerpo[el.dataset.skillCampo] = el.type === "checkbox"
              ? el.checked
              : (el.value === "" ? null : Number(el.value));
          });
          await pedir(`config/skills/${fila.dataset.skill}?campana_id=${estado.campana}`,
                      { method: "PUT", body: JSON.stringify(cuerpo) });
        }
        await cargarTodo();
        $("avisos").innerHTML = aviso(
          "Objetivos guardados. Recalculá para que el plan los use.",
          "success", "check-circle");
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudieron guardar: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  function pintarFranjas() {
    $("tabla-franjas").querySelector("tbody").innerHTML = estado.franjas.map((f, i) => `
      <tr>
        <td><select class="form-select form-select-sm" data-campo="dia_semana" data-i="${i}"
                    ${EDITA ? "" : "disabled"}>
          ${DIAS.map((d, k) => `<option value="${k}" ${k === f.dia_semana ? "selected" : ""}>${d}</option>`).join("")}
        </select></td>
        <td><input type="number" class="form-control form-control-sm" min="0" max="23"
                   value="${f.hora_desde}" data-campo="hora_desde" data-i="${i}" ${EDITA ? "" : "disabled"}></td>
        <td><input type="number" class="form-control form-control-sm" min="1" max="24"
                   value="${f.hora_hasta}" data-campo="hora_hasta" data-i="${i}" ${EDITA ? "" : "disabled"}></td>
        <td><div class="d-flex align-items-center gap-1">
          <input type="number" class="form-control form-control-sm" step="0.01" min="0.01" max="1"
                 value="${f.factor}" data-campo="factor" data-i="${i}" ${EDITA ? "" : "disabled"}>
          ${f.origen === "manual"
            ? '<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle" title="Cargado a mano">a mano</span>'
            : (f.origen === "medido"
              ? '<span class="badge bg-light text-muted border" title="Medido de los datos">medido</span>'
              : "")}
        </div></td>
        <td>${EDITA ? `<button class="btn btn-sm btn-outline-danger" data-borrar="${i}">
               <i class="bi bi-trash"></i></button>` : ""}</td>
      </tr>`).join("") ||
      '<tr><td colspan="5" class="text-muted small">Sin franjas: se asume 100% de disponibilidad.</td></tr>';

    $("tabla-franjas").querySelectorAll("[data-campo]").forEach((el) => {
      el.addEventListener("change", (e) => {
        const { campo, i } = e.target.dataset;
        estado.franjas[i][campo] = Number(e.target.value);
      });
    });
    $("tabla-franjas").querySelectorAll("[data-borrar]").forEach((el) => {
      el.addEventListener("click", (e) => {
        estado.franjas.splice(Number(e.currentTarget.dataset.borrar), 1);
        pintarFranjas();
      });
    });
  }

  async function guardarCampana() {
    const vacio = (v) => v === "" ? null : Number(v);
    const cuerpo = {
      intervalo_min: vacio($("c-intervalo").value),
      max_ocupacion: vacio($("c-ocupacion").value),
      shrinkage: vacio($("c-shrinkage").value),
      // null explícito = "usá el general", que no es lo mismo que no mandar la
      // clave. Por eso van siempre y no sólo cuando tienen valor.
      shrinkage_feriado: vacio($("c-shrinkage-feriado").value),
      shrinkage_no_habil: vacio($("c-shrinkage-no-habil").value),
      break_min_por_hora: vacio($("c-break").value) ?? 0,
      // Van siempre, en null las dos para apagarlo.
      redondeo_abajo_desde: vacio($("c-redondeo-desde").value),
      redondeo_abajo_hasta: vacio($("c-redondeo-hasta").value),
      // Va siempre: null lo apaga.
      intradia_desde_hora: vacio($("c-intradia").value),
      ancla_mensual_peso: vacio($("c-ancla-peso").value) ?? 0,
      ancla_mensual_desde_dias: vacio($("c-ancla-desde").value) ?? 7,
      paciencia_seg: vacio($("c-paciencia").value),
      paciencia_horizonte_seg: vacio($("c-horizonte").value),
    };
    await conBoton($("btn-guardar-campana"), "Guardando…", async () => {
      try {
        await pedir(`config/campana?campana_id=${estado.campana}`,
                    { method: "PUT", body: JSON.stringify(cuerpo) });
        await cargarTodo();
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudo guardar: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  // Los parámetros del modelo de pronóstico se guardan aparte de los de la
  // operación: viven en Laboratorio y se tocan después de probarlos. El PUT sólo
  // escribe las claves que vienen, así que un botón no pisa lo del otro.
  async function guardarModelo() {
    const vacio = (v) => v === "" ? null : Number(v);
    const cuerpo = {
      semanas_base: vacio($("c-semanas").value),
      dias_nivel: vacio($("c-dias-nivel").value),
      nivel_por_tipo_de_dia: $("c-nivel-tipo-dia").checked,
      reparto_deriva_dias: vacio($("c-deriva-dias").value),
      reparto_deriva_tope: vacio($("c-deriva-tope").value),
      reparto_tipo_dia_dias: vacio($("c-tipo-dia-dias").value),
      reparto_tipo_dia_tope: vacio($("c-tipo-dia-tope").value),
      combinar_cliente: $("c-combinar-cliente").checked,
    };
    await conBoton($("btn-guardar-modelo"), "Guardando…", async () => {
      try {
        await pedir(`config/campana?campana_id=${estado.campana}`,
                    { method: "PUT", body: JSON.stringify(cuerpo) });
        await cargarTodo();
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudo guardar el modelo: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  async function guardarFranjas() {
    await conBoton($("btn-guardar-franjas"), "Guardando…", async () => {
      try {
        await pedir(`config/disponibilidad?campana_id=${estado.campana}`,
                    { method: "PUT", body: JSON.stringify(estado.franjas) });
        await cargarTodo();
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudo guardar: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  // -------------------------------------------------------------- calibración

  function filaCurva(datos) {
    const t = [10, 20, 30, 60, 120, 180, 300];
    return `
      <table class="table table-sm curva-paciencia mb-1">
        <thead class="table-light"><tr><th class="small">Esperó</th>
          ${t.map((x) => `<th class="text-end small">${x}s</th>`).join("")}</tr></thead>
        <tbody><tr><td class="small text-muted">sigue esperando</td>
          ${t.map((x) => `<td class="text-end small">${pct(datos.curva?.[String(x)])}</td>`).join("")}
        </tr></tbody>
      </table>`;
  }

  async function medir() {
    await conBoton($("btn-calibrar"), "Midiendo…", async () => {
      try {
        const c = await pedir(`calibracion?campana_id=${estado.campana}`);
        estado.calibracion = c;
        pintarCalibracion(c);
      } catch (e) {
        $("calibracion").innerHTML = aviso(`No se pudo medir: ${esc(e.message)}`,
                                           "danger", "x-octagon");
      }
    });
  }

  function pintarCalibracion(c) {
    const pac = c.paciencia || {};
    const porSkill = Object.entries(pac.por_skill || {})
      .sort((a, b) => (b[1].llamadas || 0) - (a[1].llamadas || 0))
      .map(([skill, d]) => `
        <div class="mb-2">
          <div class="small"><strong>${esc(skill)}</strong>${d.skill_id ? "" :
            ' <span class="text-warning-emphasis">(sin skill equivalente en la ' +
            'configuración: no se puede aplicar)</span>'}${(d.skill_id && !d.aplicable)
            ? ` <span class="text-warning-emphasis">(${d.abandonos} abandonos:
                 hacen falta ${d.abandonos_minimos} para aplicarla, usa la de la
                 campaña)</span>` : ""}
            — ${(d.llamadas || 0).toLocaleString()} llamadas,
            ${pct(d.abandono_observado, 2)} de abandono,
            paciencia equivalente <strong>${d.paciencia_seg ?? "—"}s</strong></div>
          ${filaCurva(d)}
        </div>`).join("") || '<div class="small text-muted">Sin datos suficientes.</div>';

    const sh = c.shrinkage || {};
    const pres = presenciaMedida(sh);
    // Lo que se aplica va primero cuando está medido. Los códigos de RRHH quedan
    // abajo, plegados: dicen quién faltó, pero no quién cubrió.
    const motivoPresencia = !pres && sh.presencia?.motivo
      ? `<div class="small text-warning-emphasis mb-2">${esc(sh.presencia.motivo)}
           Mientras tanto se propone el de los códigos de RRHH.</div>` : "";
    const bloquePresencia = pres ? `
      <div class="small mb-1">Medido como presencia en la línea
        (${pres.semanas} semanas${pres.medido_en ? `, al ${dia(pres.medido_en)}` : ""}):
        hábil <strong>${pct(pres.habil)}</strong> ·
        sábado y domingo <strong>${pct(pres.no_habil)}</strong>
        <span class="text-muted">· el feriado queda como está
        (${estado.config?.shrinkage_feriado === null || estado.config?.shrinkage_feriado === undefined
          ? "usa el hábil" : pct(estado.config.shrinkage_feriado)}): con uno cada tantas
        semanas no hay medición.</span></div>
      <div class="porque mb-2">Es la gente con turno de las telefónicas contra los
        conectados de las telefónicas <em>y de otras sub-campañas</em> (Gestión SVP,
        Anfitrión, BackOffice…), sin ${esc(REF())}. Se mide así y no por los códigos de
        RRHH porque la disponibilidad —el escalón anterior— ya se mide contra todos los
        conectados: el faltante que otra sub-campaña cubre no hay que volver a citarlo.
        ${esc(REF())} queda afuera porque es la palanca y el plan la muestra aparte.</div>` : "";
    const porPool = Object.entries(sh.medido || {}).map(([pool, d]) => `
      <li class="small">${esc(pool)}: ${d.shrinkage !== null && d.shrinkage !== undefined
        ? `<strong>${pct(d.shrinkage)}</strong>
           <span class="text-muted">(${pct(d.ausentismo)} ausentismo
           + ${pct(d.dentro_del_turno)} dentro del turno sobre
           ${(d.horas_universo_malla || 0).toLocaleString()} h de malla)</span>
           <br><span class="text-muted">Aparte, <strong>${pct(d.capacitacion)}</strong>
           de capacitación: ${pct(d.shrinkage_nomina)} sobre las
           ${(d.horas_programadas || 0).toLocaleString()} h programadas. Ese número
           es de <strong>nómina</strong> y no se descuenta del intervalo —el que
           está en capacitación no figura en los citados por RRHH, así que
           descontárselo a la malla sería contarlo dos veces—.</span>`
        : `<span class="text-muted">${esc(d.motivo)}</span>`}</li>
      ${tablaTipoDeDia(d)}`).join("");

    const disp = Object.entries(c.disponibilidad?.medida || {}).map(([pool, d]) => `
      <li class="small">${esc(pool)}: ${d.factor
        ? `<strong>${pct(d.factor, 0)}</strong>
           <span class="text-muted">(${d.muestras} intervalos, p25 ${pct(d.p25, 0)} – p75 ${pct(d.p75, 0)})</span>`
          + (d.sin_explicar
             ? `<br><span class="text-muted">En el ${pct(d.sin_explicar, 0)} de esos
                intervalos el modelo necesita más operadores de los que estuvieron
                logueados para explicar el servicio que igual se logró. No es
                disponibilidad de más del 100% —eso no existe—: es Erlang, que con
                pocos operadores es pesimista. Por eso el factor va topado en 100%
                y esos intervalos no lo empujan para arriba.</span>`
             : "")
          + (d.conflicto
             ? `<br><span class="text-warning-emphasis">${esc(d.conflicto)}</span>`
             : `<br><span class="text-muted">Se guarda
                <strong>${pct(d.factor_aplicable, 0)}</strong>, que es la
                disponibilidad real sin el break${d.break
                  ? ` (${pct(d.factor, 0)} ÷ (1 − ${pct(d.break, 1)}))` : ""}:
                el break se descuenta aparte, así que si mañana cambia se toca esa
                sola perilla.</span>`)
          + (d.por_hora?.length
             ? `<br><span class="text-muted">Medida hora por hora en
                ${d.por_hora.length} de 24 horas (${d.por_hora[0].hora}:00 a
                ${d.por_hora[d.por_hora.length - 1].hora}:00). Las que no llegan a
                ${esc(String(d.por_hora[0].muestras && 30))} intervalos útiles
                conservan lo que ya tenían.</span>`
             : "")
        : `<span class="text-muted">${esc(d.motivo)}</span>`}</li>`).join("");

    // Sobre qué población se midió. Un 4,6% y un 13,6% son los dos correctos y
    // miden cosas distintas; sin decirlo, el número no se puede interpretar.
    const soloMalla = Object.values(sh.medido || {})
      .some((d) => d.solo_puestos_de_malla);
    const poblacion = Object.keys(sh.medido || {}).length === 0 ? "" : (soloMalla
      ? `<div class="porque mb-3">Medido sólo sobre los puestos que atienden el
           teléfono, que son los mismos que cuentan en la malla. Contar también a
           supervisores y coordinadores lo inflaba: a la estructura la nómina la
           carga con otra lógica.</div>`
      : `<div class="porque mb-3 text-warning-emphasis">Medido sobre
           <strong>toda la nómina</strong> de las sub-campañas, incluidos
           supervisores y el puesto Operador Capacitación, que nunca atienden.
           Falta correr <code>2026-09-09e_planificador_puestos_malla.sql</code>;
           hasta entonces este número sobreestima el shrinkage de los operadores.
         </div>`);

    const primerPool = Object.keys(sh.medido || {})[0];
    $("calibracion").innerHTML = `
      <div class="small text-muted mb-2">Ausentismo y disponibilidad medidos entre
        ${dia(c.desde)} y ${dia(c.hasta)}${pac.desde
          ? ` · paciencia entre ${dia(pac.desde)} y ${dia(c.hasta)} (${pac.dias} días)`
          : ""}</div>

      <h6 class="small fw-semibold mb-1">Paciencia del cliente</h6>
      <div class="small mb-1">Vigente: <strong>${pac.vigente_seg ?? "—"}s</strong>
        <span class="text-muted">(${esc(pac.origen || "sin medir")})</span> ·
        sugerida: <strong>${pac.sugerida_seg ?? "—"}s</strong></div>
      ${porSkill}
      <div class="porque mb-3">
        La curva es la lectura honesta: cuánta gente sigue esperando a cada tiempo de espera.
        La <em>paciencia equivalente</em> es el número que necesita Erlang A para
        reproducir ese abandono; no significa que la gente espere ese tiempo.
      </div>

      <h6 class="small fw-semibold mb-1">Ausentismo (shrinkage de nómina)</h6>
      <div class="small mb-1">Vigente: <strong>${pct(sh.vigente)}</strong>
        <span class="text-muted">(${esc(sh.origen || "sin medir")})</span></div>
      ${pres ? `${bloquePresencia}
      <details class="porque mb-3"><summary>Ausentismo según los códigos de RRHH (no se aplica)</summary>
        <ul class="ps-3 mb-2 mt-1">${porPool || '<li class="small text-muted">Sin datos.</li>'}</ul>
        ${poblacion}
      </details>` : `
      ${motivoPresencia}
      <ul class="ps-3 mb-2">${porPool || '<li class="small text-muted">Sin datos.</li>'}</ul>
      ${poblacion}`}

      <h6 class="small fw-semibold mb-1">Disponibilidad</h6>
      <ul class="ps-3 mb-2">${disp || '<li class="small text-muted">Sin datos.</li>'}</ul>

      ${EDITA ? `<button id="btn-aplicar-calibracion" class="btn btn-sm btn-outline-primary"
                   data-pool="${esc(primerPool || "")}">
                   Dejar vigentes los valores medidos</button>
                 <div class="fuente small text-muted mt-1">
                   Pisa también lo que esté puesto a mano, y avisa cuál pisó.</div>` : ""}`;

    const btn = $("btn-aplicar-calibracion");
    if (btn) btn.addEventListener("click", () => aplicarCalibracion(btn.dataset.pool));
  }

  // El shrinkage partido por tipo de día. Reusa el TIPO_DIA que ya nombra los
  // tipos en la pestaña Comparación: son los mismos cuatro.
  // Se muestra CON su error estándar
  // porque es lo único que deja distinguir una diferencia real del vaivén de un
  // día cualquiera: medido, el domingo da 8,7% contra 8,6% de un hábil, pero un
  // domingo suelto puede dar 0% o 20%.
  function tablaTipoDeDia(d) {
    const filas = d.por_tipo_de_dia || [];
    if (filas.length < 2) return "";
    const habil = filas.find((f) => f.tipo === "habil");
    // Dos errores estándar de la diferencia: la regla de bolsillo para no leer
    // ruido como señal. Se compara el promedio DÍA A DÍA, que es el que tiene
    // dispersión medida; el ponderado por horas no la tiene.
    const distinto = (f) => {
      if (!habil || f.tipo === "habil") return false;
      const e = Math.sqrt((f.error_estandar || 0) ** 2 + (habil.error_estandar || 0) ** 2);
      return e > 0 && Math.abs(f.promedio_diario - habil.promedio_diario) > 2 * e;
    };
    const hay = filas.some(distinto);
    // No se dicta capacitación en sábado, domingo ni feriado. Que dé cero no es
    // casualidad del promedio: es cómo funciona la operación, y si alguna vez no
    // diera cero hay una fila mal cargada.
    const capaRara = filas.filter((f) => f.tipo !== "habil" && f.capacitacion > 0);
    return `
      <table class="table table-sm table-borderless small mb-1 ms-3" style="width:auto">
        <thead class="text-muted"><tr>
          <th class="fw-normal">Tipo de día</th><th class="text-end fw-normal">Días</th>
          <th class="text-end fw-normal">Ausentismo</th>
          <th class="text-end fw-normal">Capacit.</th>
          <th class="text-end fw-normal">En el turno</th>
          <th class="text-end fw-normal">Se aplica</th>
          <th class="text-end fw-normal">Nómina</th>
          <th class="text-end fw-normal">± error</th><th></th>
        </tr></thead>
        <tbody>${filas.map((f) => `
          <tr>
            <td>${esc(TIPO_DIA[f.tipo] || f.tipo)}</td>
            <td class="text-end">${f.dias}</td>
            <td class="text-end">${pct(f.ausentismo)}</td>
            <td class="text-end ${f.capacitacion ? "" : "text-muted"}">${pct(f.capacitacion)}</td>
            <td class="text-end">${pct(f.dentro_del_turno)}</td>
            <td class="text-end fw-semibold">${pct(f.shrinkage)}</td>
            <td class="text-end text-muted">${pct(f.shrinkage_nomina)}</td>
            <td class="text-end text-muted">${pct(f.error_estandar)}</td>
            <td class="small">${distinto(f)
              ? '<span class="text-warning-emphasis">distinto de un hábil</span>' : ""}</td>
          </tr>`).join("")}</tbody>
      </table>
      <div class="porque ms-3 mb-2">
        <strong>Se aplica</strong> es ausentismo + faltante dentro del turno sobre
        el universo de la malla (las horas de piso y de ausente), que es la misma
        población que los "citados por RRHH" de la tabla del plan.
        <strong>Nómina</strong> suma además la capacitación, sobre todas las horas
        programadas: sirve para dimensionar la nómina, no el intervalo.<br>
        La <strong>capacitación</strong> sólo existe en día hábil: no se dicta en
        sábado, domingo ni feriado. Lo que se ahorra ahí no baja el total, se va
        en <strong>faltante dentro del turno</strong> —el que llegó tarde o se fue
        antes, que ningún código de RRHH informa—.<br>
        ${hay
          ? `Los tipos marcados se apartan del día hábil más de dos errores
             estándar: ahí un solo número para todos los días pide gente de más o
             de menos.`
          : `Ninguno se aparta del día hábil más de dos errores estándar, así que
             un solo número alcanza. Ojo con la lectura fácil: el fin de semana
             <em>varía</em> mucho más que un hábil, pero en promedio da lo mismo.`}
        ${capaRara.length ? `<br><span class="text-warning-emphasis">Hay
          capacitación cargada en ${capaRara.map((f) => esc(TIPO_DIA[f.tipo])).join(", ")},
          donde no debería haberla: revisar esas filas de payroll.</span>` : ""}
      </div>`;
  }

  // El shrinkage medido del pool, con los dos por tipo de día ya armados. Sin
  // esto el botón dejaba el general nuevo al lado de un feriado viejo, que es
  // peor que no tener ninguno de los dos.
  // La presencia medida, en los dos escalones que tiene la config. Null si todavía
  // no se midió (sin la migración 2026-09-15b o sin correr el script del perfil).
  function presenciaMedida(sh) {
    const p = sh?.presencia?.por_tipo;
    if (!p || !p.habil) return null;
    return {
      habil: p.habil.faltante,
      no_habil: p.no_habil ? p.no_habil.faltante : null,
      medido_en: sh.presencia.medido_en,
      semanas: sh.presencia.semanas,
    };
  }

  function shrinkageAAplicar(pool) {
    // Con la presencia medida se aplica ésa: el faltante que cubren otras
    // sub-campañas no se vuelve a citar (ver `presenciaMedida`). El feriado se
    // deja como está.
    const pres = presenciaMedida(estado.calibracion?.shrinkage);
    if (pres) {
      return {
        shrinkage: pres.habil,
        no_habil: pres.no_habil,
        feriado: estado.config?.shrinkage_feriado ?? null,
        ausentismo: null,
        capacitacion: null,
        origen: "presencia",
      };
    }
    const d = estado.calibracion?.shrinkage?.medido?.[pool];
    if (!d || d.shrinkage === null || d.shrinkage === undefined) return null;
    const tipos = {};
    (d.por_tipo_de_dia || []).forEach((t) => { tipos[t.tipo] = t; });
    // Sábado y domingo comparten una sola perilla, así que se combinan
    // ponderando por horas y no promediando: un sábado tiene más gente que un
    // domingo y pesa más.
    const finde = ["sabado", "domingo"].map((k) => tipos[k]).filter(Boolean);
    // Pondera por las horas del UNIVERSO DE LA MALLA y no por las programadas:
    // es el denominador del `shrinkage` que se está combinando, y mezclarlos
    // sesga el promedio hacia el tipo de día con más capacitación.
    const horas = finde.reduce((a, t) => a + (t.horas_universo_malla || 0), 0);
    return {
      shrinkage: d.shrinkage,
      ausentismo: d.ausentismo ?? null,
      capacitacion: d.capacitacion ?? null,
      no_habil: horas > 0
        ? Number((finde.reduce((a, t) => a + t.shrinkage * t.horas_universo_malla, 0) / horas)
            .toFixed(4))
        : null,
      feriado: tipos.feriado ? tipos.feriado.shrinkage : null,
    };
  }

  // La paciencia medida de cada skill. No es una sola por campaña: el que se
  // quedó sin luz espera mucho más que un electrodependiente, y son colas
  // distintas. La de la campaña queda de valor por defecto.
  function pacienciaPorSkill() {
    // El `skill_id` lo resuelve el backend con UPPER(): el informe de IVR dice
    // "Emergencias" y la config dice "EMERGENCIAS", así que cruzar los nombres
    // literalmente sólo acertaba con TOC —el único en mayúsculas de los dos
    // lados— y la paciencia medida terminaba aplicada a ese skill y a ninguno más.
    const salida = {};
    Object.values(estado.calibracion?.paciencia?.por_skill || {}).forEach((d) => {
      // `aplicable` ya contempla el piso de abandonos: la paciencia sale de los
      // que abandonaron, y con ocho de ellos el número es ruido — y ruido que
      // REBAJA la dotación, porque una paciencia enorme le dice a Erlang A que
      // van a esperar.
      if (!d.skill_id) return;
      // El no aplicable se manda en null A PROPÓSITO: limpia lo que hubiera
      // quedado de una medición anterior y lo devuelve a la paciencia de la
      // campaña. Sin esto, TOC se quedaba con los 5.520s que le dejó la versión
      // con el bug de nombres, para siempre.
      salida[d.skill_id] = d.aplicable ? d.paciencia_seg : null;
    });
    return salida;
  }

  async function aplicarCalibracion(pool) {
    const btn = $("btn-aplicar-calibracion");
    await conBoton(btn, "Aplicando…", async () => {
      try {
        // Se manda LO YA MEDIDO en vez de pedirle al backend que vuelva a medir:
        // es lo que está en pantalla, y remedir son decenas de segundos para
        // escribir cuatro números.
        const c = await pedir(`calibracion/aplicar?campana_id=${estado.campana}`, {
          method: "POST",
          body: JSON.stringify({
            paciencia_seg: estado.calibracion?.paciencia?.sugerida_seg || null,
            shrinkage_pool: pool || null,
            shrinkage: shrinkageAAplicar(pool),
            paciencia_por_skill: pacienciaPorSkill(),
            disponibilidad: estado.calibracion?.disponibilidad?.medida?.[pool] || null,
          }),
        });
        await cargarTodo();
        // `c` ya no trae la medición —el botón no vuelve a medir— así que se
        // sigue mostrando la que estaba, que es la que se acaba de aplicar.
        if (estado.calibracion) pintarCalibracion(estado.calibracion);
        if (c.franjas_disponibilidad) {
          $("avisos").innerHTML += aviso(
            `Se cargaron ${c.franjas_disponibilidad} franjas de disponibilidad
             medidas hora por hora, ya netas de break. Las horas sin muestra
             suficiente conservaron su valor.`, "success", "check-circle");
        }
        if (c.skills_con_paciencia) {
          $("avisos").innerHTML += aviso(
            `Se actualizó la paciencia de ${c.skills_con_paciencia} skill(s). Los que
             no llegan al mínimo de abandonos quedan en blanco y usan la de la
             campaña: con pocos abandonos el número es ruido, y ruido que rebaja
             la dotación.`,
            "success", "check-circle");
        }
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudo aplicar: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  // --------------------------------------------------------------- recalcular

  async function recalcular() {
    const dias = $("f-dias").value;
    const nombre = (estado.config && estado.config.nombre) || "esta campaña";
    const qol = window.PlanificadorQoL;
    const pendientes = qol && qol.cambiosSinGuardar ? qol.cambiosSinGuardar() : [];
    const texto =
      `Se arma un plan nuevo de ${dias} días para ${nombre} y queda vigente en lugar ` +
      "del actual: lo ven todos los que abren el planificador, el Excel y los " +
      "refuerzos.\n\n" +
      (pendientes.length
        ? `OJO: hay cambios sin guardar en ${pendientes.join(", ")}. El recálculo usa ` +
          "la configuración GUARDADA y, al recargar la pantalla, esos cambios se pierden. " +
          "Guardalos primero si querés que entren.\n\n"
        : "") +
      "¿Recalcular?";
    if (!window.confirm(texto)) return;
    await conBoton($("btn-recalcular"), "Calculando…", async () => {
      try {
        await pedir(
          `recalcular?campana_id=${estado.campana}&dias=${$("f-dias").value}`,
          { method: "POST" });
        await cargarTodo();
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudo recalcular: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  // ---------------------------------------------------------------- refuerzos

  const ACCION = {
    malla: { texto: "Mover la malla", clase: "text-secondary", icono: "calendar-week" },
    horas_extra: { texto: "Horas extra", clase: "brecha-falta", icono: "clock-history" },
    convocatoria: { texto: "Convocatoria HOY", clase: "brecha-falta fw-bold",
                    icono: "megaphone" },
  };

  async function cargarRefuerzos() {
    try {
      const r = await pedir(`necesidades?campana_id=${estado.campana}`);
      estado.refuerzos = r;
      $("btn-excel-refuerzos").href = `${DESCARGA}/refuerzos?campana_id=${estado.campana}`;
      pintarRefuerzos(r);
    } catch (e) {
      $("r-kpis").innerHTML = "";
    }
  }

  function pintarRefuerzos(r) {
    const s = r.resumen || {};
    const acc = s.por_accion || {};
    const hoy = (acc.convocatoria || {}).horas_operador || 0;
    const conSeguimiento = s.seguimiento_disponible !== false;

    const avisoMigracion = $("aviso-refuerzos-migracion");
    if (avisoMigracion) avisoMigracion.hidden = conSeguimiento;

    document.querySelectorAll(".th-refuerzo-seguimiento").forEach((el) => {
      el.hidden = !conSeguimiento;
    });

    const kpis = [
      kpi(num(s.horas_operador) + " h", "Horas-operador que faltan",
          `${num(s.tramos)} bloques en ${num(s.dias)} días`),
      kpi(num(s.horas_operador_alto) + " h", "Si el volumen viene alto",
          "pronóstico + su error típico"),
      kpi(num(s.faltante_pico), "Faltante máximo en un intervalo", "operadores"),
      kpi(num((acc.malla || {}).horas_operador || 0) + " h", "Todavía se mueve la malla",
          "4 días o más de antelación"),
      kpi(num((acc.horas_extra || {}).horas_operador || 0) + " h", "Horas extra programadas",
          "entre 1 y 3 días"),
      kpi(num(hoy) + " h", "Convocatoria del mismo día",
          hoy > 0 ? "lo más caro: mirar esto antes" : "nada pendiente para hoy"),
    ];

    if (conSeguimiento) {
      kpis.push(
        kpi(num(s.ultimos_14d_horas_pedidas || 0) + " h", "Pedido a RRHH (14 días cerrados)",
            `cubierto ${num(s.ultimos_14d_horas_cubiertas || 0)} h`)
      );
    }
    $("r-kpis").innerHTML = kpis.join("");

    // Un gráfico por día y no por intervalo: lo que se pide son bloques, y el
    // detalle intervalo por intervalo ya está en la tabla de abajo.
    const porDia = {};
    (r.refuerzos || []).forEach((f) => {
      const d = dia(f.dia);
      porDia[d] = porDia[d] || { h: 0, alto: 0 };
      porDia[d].h += f.horas_operador;
      porDia[d].alto += f.horas_operador_alto || f.horas_operador;
    });
    const dias = Object.keys(porDia).sort();
    dibujar("chart-refuerzos", dias, [
      { label: "Horas que faltan", data: dias.map((d) => porDia[d].h),
        borderColor: COLOR.planificar },
      { label: "Si el volumen viene alto", data: dias.map((d) => porDia[d].alto),
        borderColor: COLOR.ingenuo, borderDash: [5, 4] },
    ], true, (t) => t[0].label);

    const tbody = $("tabla-refuerzos").querySelector("tbody");
    const colCount = conSeguimiento ? 10 : 8;

    tbody.innerHTML = (r.refuerzos || []).map((f, idx) => {
      const a = ACCION[f.accion] || { texto: f.accion, clase: "", icono: "dot" };
      const ped = f.pedido;
      const est = ped ? ped.estado : "pendiente";

      let badgeHtml = "";
      let accionesHtml = "";
      if (conSeguimiento) {
        if (est === "cubierto") {
          const cub = ped.horas_cubiertas !== null && ped.horas_cubiertas !== undefined ? num(ped.horas_cubiertas, 1) : "0";
          badgeHtml = `<span class="badge bg-success-subtle text-success border">cubierto ${cub} de ${num(f.horas_operador, 1)} h</span>`;
        } else if (est === "cubierto_parcial") {
          const cub = ped.horas_cubiertas !== null && ped.horas_cubiertas !== undefined ? num(ped.horas_cubiertas, 1) : "0";
          badgeHtml = `<span class="badge bg-info-subtle text-info-emphasis border">cubierto ${cub} de ${num(f.horas_operador, 1)} h</span>`;
        } else if (est === "no_cubierto") {
          badgeHtml = `<span class="badge bg-danger-subtle text-danger border">no cubierto</span>`;
        } else if (est === "descartado") {
          badgeHtml = `<span class="badge bg-light text-muted border">descartado</span>`;
        } else if (est === "pedido") {
          badgeHtml = `<span class="badge bg-warning-subtle text-warning-emphasis border">pedido</span>`;
        } else {
          badgeHtml = `<span class="badge bg-secondary-subtle text-secondary border">pendiente</span>`;
        }

        if (EDITA) {
          if (!ped || est === "pendiente") {
            accionesHtml = `
              <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 btn-marcar-refuerzo" data-idx="${idx}" title="Marcar como pedido a RRHH">
                <i class="bi bi-send me-1"></i>Marcar pedido
              </button>
              <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-2 ms-1 btn-descartar-refuerzo" data-idx="${idx}" title="Descartar este bloque">
                <i class="bi bi-x-circle me-1"></i>Descartar
              </button>
            `;
          } else if (est === "pedido") {
            accionesHtml = `
              <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-2 btn-descartar-refuerzo" data-idx="${idx}" title="Descartar pedido">
                <i class="bi bi-x-circle me-1"></i>Descartar
              </button>
            `;
          } else if (est === "descartado") {
            accionesHtml = `
              <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 btn-marcar-refuerzo" data-idx="${idx}" title="Volver a pedir a RRHH">
                <i class="bi bi-arrow-counterclockwise me-1"></i>Marcar pedido
              </button>
            `;
          } else {
            accionesHtml = `<span class="text-muted small">—</span>`;
          }
        } else {
          accionesHtml = `<span class="text-muted small">—</span>`;
        }
      }

      const celdasSeguimiento = conSeguimiento ? `
        <td>${badgeHtml}</td>
        <td class="text-end text-nowrap">${accionesHtml}</td>
      ` : "";

      const sug = f.turnos_sugeridos || {};
      const sugTexto = sug.texto || "Sin refuerzos necesarios";
      const sugDetalle = sug.propuesta && sug.propuesta.length
        ? `Propuesta: ${esc(sugTexto)} · Horas propuestas: <strong>${num(sug.horas_propuestas, 1)} h</strong> (faltantes netas: ${num(sug.horas_faltantes, 1)} h, sobrecobertura: ${num(sug.sobrecobertura_h, 1)} h)`
        : "Sin turnos necesarios.";

      return `
        <tr class="fila-refuerzo" data-estado="${est}" data-idx="${idx}">
          <td>${dia(f.dia)}</td>
          <td>${hora(f.desde)}–${hora(f.hasta)}</td>
          <td class="small">${esc(f.pool)}</td>
          <td class="text-end brecha-falta">${num(f.faltante_pico)}</td>
          <td class="text-end">${num(f.horas_operador, 1)}</td>
          <td class="text-end text-muted">${f.faltante_pico_alto === null ? "—"
            : num(f.faltante_pico_alto) + " · " + num(f.horas_operador_alto, 1) + " h"}</td>
          <td class="text-end">${f.antelacion_dias === 0 ? "hoy" : f.antelacion_dias + " d"}</td>
          <td class="${a.clase}"><i class="bi bi-${a.icono} me-1"></i>${a.texto}</td>
          ${celdasSeguimiento}
        </tr>
        <tr class="fila-sugerencia bg-light-subtle small" data-estado="${est}">
          <td colspan="${colCount}" class="py-1 px-3 text-muted">
            <details class="porque">
              <summary class="fw-semibold text-primary" style="cursor: pointer;">
                <i class="bi bi-lightbulb me-1"></i>Cómo pedirlo: ${esc(sug.resumen || sugTexto)}
              </summary>
              <div class="mt-1 ps-3 text-secondary">
                ${sugDetalle}
              </div>
            </details>
          </td>
        </tr>
      `;
    }).join("");

    if (window.PlanificadorRefuerzos && typeof window.PlanificadorRefuerzos.aplicarFiltro === "function") {
      window.PlanificadorRefuerzos.aplicarFiltro();
    }
  }

  // -------------------------------------------------------------- comparación

  const DESCARGA = "/planificador/descargar";

  function fechaISO(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function paramsBacktest() {
    const clima = $("b-clima") ? $("b-clima").value : "";
    const nivel = $("b-nivel") ? $("b-nivel").value : "";
    const elast = $("b-elast") ? $("b-elast").value : "";
    return `campana_id=${estado.campana}&desde=${$("b-desde").value}` +
           `&hasta=${$("b-hasta").value}&antelacion=${$("b-antelacion").value}` +
           (clima ? `&clima=${clima}` : "") +
           (nivel ? `&nivel=${nivel}` : "") +
           (elast ? `&elasticidad=${elast}` : "");
  }

  function fechasPorDefecto() {
    // Cuatro semanas terminando ayer: hoy está a medias y arrastraría el promedio
    // hacia abajo sin que se note por qué.
    const ayer = new Date();
    ayer.setDate(ayer.getDate() - 1);
    const arranque = new Date(ayer);
    arranque.setDate(arranque.getDate() - 27);
    $("b-hasta").value = fechaISO(ayer);
    $("b-desde").value = fechaISO(arranque);
    $("b-hasta").max = fechaISO(new Date());
    $("b-desde").max = fechaISO(new Date());
  }

  async function comparar() {
    if (!$("b-desde").value || !$("b-hasta").value) return;
    await conBoton($("btn-comparar"), "Comparando…", async () => {
      $("b-avisos").innerHTML = "";
      try {
        const btn = $("btn-comparar");
        const bt = await pedirBacktest(paramsBacktest(), (seg) => {
          btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Comparando… ${Math.round(seg)} s`;
        });
        estado.backtest = bt;
        $("btn-excel-comparacion").href = `${DESCARGA}/comparacion?${paramsBacktest()}`;
        $("btn-excel-comparacion").classList.remove("disabled");
        pintarBacktest(bt);
        guardarComparacion(bt);
      } catch (e) {
        estado.backtest = null;
        $("btn-excel-comparacion").classList.add("disabled");
        $("b-avisos").innerHTML = aviso(
          `No se pudo comparar: ${esc(e.message)}`, "danger", "x-octagon");
      }
    });
  }

  // La comparación tarda minutos y se perdía al recargar la página: la última de
  // cada campaña queda en el navegador y se muestra marcada como guardada.
  const CLAVE_COMPARACION = "planificador.comparacion.";
  const CAMPOS_COMPARACION = ["b-desde", "b-hasta", "b-antelacion", "b-clima", "b-nivel", "b-elast"];

  function guardarComparacion(bt) {
    const campos = {};
    CAMPOS_COMPARACION.forEach((id) => { if ($(id)) campos[id] = $(id).value; });
    try {
      const texto = JSON.stringify({ guardado: Date.now(), campos, bt });
      // Un período muy largo no entra en el almacenamiento del navegador: se
      // resigna el guardado antes que llenarlo.
      if (texto.length > 3_000_000) return;
      window.localStorage.setItem(CLAVE_COMPARACION + estado.campana, texto);
    } catch (e) { /* sin almacenamiento local o lleno: no se guarda */ }
  }

  function restaurarComparacion() {
    if (estado.backtest && estado.backtestCampana === estado.campana) return;
    let g = null;
    try { g = JSON.parse(window.localStorage.getItem(CLAVE_COMPARACION + estado.campana) || "null"); }
    catch (e) { g = null; }
    if (!g || !g.bt) return;
    Object.entries(g.campos || {}).forEach(([id, v]) => {
      const el = $(id);
      if (!el) return;
      if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === v)) return;
      el.value = v;
    });
    try {
      estado.backtest = g.bt;
      pintarBacktest(g.bt);
    } catch (e) {
      // Guardada con una versión anterior de la pantalla: se descarta.
      estado.backtest = null;
      try { window.localStorage.removeItem(CLAVE_COMPARACION + estado.campana); } catch (e2) { /* idem */ }
      return;
    }
    $("btn-excel-comparacion").href = `${DESCARGA}/comparacion?${paramsBacktest()}`;
    $("btn-excel-comparacion").classList.remove("disabled");
    const cuando = new Date(g.guardado);
    const fecha = `${etiquetaDia(fechaISO(cuando))} ${String(cuando.getHours()).padStart(2, "0")}:` +
                  String(cuando.getMinutes()).padStart(2, "0");
    $("b-avisos").insertAdjacentHTML("afterbegin", aviso(
      `Es la última comparación, guardada en este navegador el <strong>${esc(fecha)}</strong>. ` +
      "Los datos reales pueden haber cambiado desde entonces: <strong>Comparar</strong> la vuelve a correr.",
      "secondary", "clock-history"));
  }

  function pintarBacktest(bt) {
    estado.backtestCampana = estado.campana;
    const r = bt.resumen || {};
    const intervalo = r.intervalo || {};
    const diario = r.diario || {};
    const total = r.total_cliente;

    // El WAPE va primero porque es el que pondera por volumen, o sea el que dice
    // cuánto se erró DONDE hay gente atendiendo. El MAPE diario va al lado porque
    // es el número que todo el mundo está acostumbrado a citar.
    // Tres números arriba y el resto plegado. Los tres son los que deciden algo:
    // cuánto erramos, cuánto erra el que se usa hoy, y para qué lado erramos.
    // El sesgo está entre ellos porque es lo que se ve de un vistazo en el
    // gráfico —una línea que corre por debajo se nota— y porque sub-dotar
    // sistemáticamente es peor que errar para los dos lados.
    const arriba = [
      kpi(pct(intervalo.wape), "Nuestro error por media hora",
          `sobre ${num(intervalo.n)} intervalos cerrados`),
    ];
    if (r.produccion) {
      arriba.push(kpi(pct(r.produccion.wape), "El del cliente (dbo.Forecast)",
                      "el que usa la operación hoy"));
    }
    arriba.push(kpi(pct(diario.sesgo), "Sesgo",
        diario.sesgo === null || diario.sesgo === undefined ? "" :
        (diario.sesgo > 0 ? "nos quedamos cortos" : "nos pasamos")));

    const detalle = [
      kpi(pct(diario.mape), "Error del total diario (MAPE)",
          `${bt.dias_evaluados} días evaluados`),
      kpi(pct((r.forma || {}).wape), "Error de la curva intradía",
          "lo que quedaría si se acertara el total del día"),
      kpi(pct(r.mejora_vs_ingenuo), "Mejora vs. repetir la semana anterior",
          (r.ingenuo || {}).wape ? `la referencia erraba ${pct((r.ingenuo || {}).wape)}` : "sin referencia"),
    ];
    if (total) {
      detalle.push(kpi(pct(total.mape), "Error sobre la demanda del cliente",
                       "el modelo, sin el reparto"));
    }
    $("b-kpis").innerHTML = arriba.join("") +
      `<div class="col-12"><details><summary class="small text-muted"
         style="cursor:pointer">Más detalle del error</summary>
         <div class="row g-2 mt-1">${detalle.join("")}</div></details></div>`;

    const avisos = (bt.avisos || []).map((a) => aviso(esc(a), "light", "info-circle")).join("");
    const alertas = [];
    if (r.mejora_vs_ingenuo !== null && r.mejora_vs_ingenuo !== undefined
        && r.mejora_vs_ingenuo <= 0) {
      alertas.push(aviso("<strong>El modelo no le está ganando a repetir el mismo día de la " +
                         "semana pasada.</strong> Con esta antelación y este período, la línea " +
                         "de base estacional no aporta sobre la referencia mínima.",
                         "warning", "exclamation-triangle"));
    }
    // Lo que decide si conviene cambiar de pronóstico no es el error absoluto:
    // es si le gana al que la operación ya está usando.
    if (r.mejora_vs_produccion !== null && r.mejora_vs_produccion !== undefined) {
      const gana = r.mejora_vs_produccion > 0;
      alertas.push(aviso(
        // "gana por X%" se leía como un error de X%: es cuánto MENOS erramos, en
        // proporción al error del cliente (1 - nuestro/cliente).
        `<strong>Contra dbo.Forecast, el que usa la operación hoy: nuestro error por media ` +
        `hora es ${pct(Math.abs(r.mejora_vs_produccion))} ${gana ? "menor" : "mayor"}.</strong> ` +
        `${pct((r.intervalo || {}).wape)} contra ${pct(r.produccion.wape)}.` +
        (gana ? "" : " Mientras siga así no hay motivo para cambiar de pronóstico."),
        gana ? "success" : "warning", gana ? "check-circle" : "exclamation-triangle"));
    }
    $("b-avisos").innerHTML = alertas.join("") + avisos;
    if ($("b-clima-modelo")) $("b-clima-modelo").innerHTML = tarjetaClima(bt.clima);
    if ($("b-nivel-modelo")) $("b-nivel-modelo").innerHTML = tarjetaNivel(bt.nivel);

    const dias = bt.por_dia || [];
    dibujar("chart-backtest-dia",
      dias.map((d) => dia(d.dia)),
      [
        { label: "Real", data: dias.map((d) => d.real), borderColor: COLOR.real },
        { label: "Pronosticado", data: dias.map((d) => d.pronosticado), borderColor: COLOR.pronost },
        { label: "Semana anterior", data: dias.map((d) => d.ingenuo),
          borderColor: COLOR.ingenuo, borderDash: [5, 4] },
        { label: "dbo.Forecast (el de hoy)", data: dias.map((d) => d.produccion),
          borderColor: COLOR.produccion, borderDash: [2, 3] },
        // Sólo si la combinación llegó a tener peso: una serie idéntica a la
        // nuestra en el gráfico se lee como si aportara algo.
        ...(dias.some((d) => d.peso_cliente)
          ? [{ label: "Combinado", data: dias.map((d) => d.combinado),
               borderColor: COLOR.combinado, borderDash: [6, 2] }] : []),
      ], true, (t) => t[0].label);

    $("b-dia").innerHTML = dias.map((d) =>
      `<option value="${dia(d.dia)}">${etiquetaDia(dia(d.dia))}${d.evento ? " — " + esc(d.evento) : ""}</option>`
    ).join("");
    const peor = bt.peor_dia ? dia(bt.peor_dia.dia) : null;
    if (peor) $("b-dia").value = peor;   // el día que más se erró, que es el que hay que mirar
    dibujarIntradia();

    pintarTablaBacktest(dias);
    pintarTipoDeDia(bt.por_tipo_de_dia || []);
    pintarDotacion(bt.dotacion || {});
  }

  const TIPO_DIA = { habil: "Hábil", sabado: "Sábado", domingo: "Domingo",
                     feriado: "Feriado" };

  // Lo mismo que el cotejo de llamadas pero en GENTE, que es lo que se negocia.
  // La gracia está en dimensionar dos veces —con el pronóstico y con la demanda
  // que realmente entró— porque así la brecha contra la malla se parte en error de
  // pronóstico y error de dimensionamiento, que se arreglan en lugares distintos.
  function pintarDotacion(d) {
    const cuerpo = $("tabla-dotacion").querySelector("tbody");
    const porTipo = $("tabla-dotacion-tipo").querySelector("tbody");
    const dias = d.por_dia || [];
    if (!dias.length) {
      estado.dotacion = null;
      $("d-dia").innerHTML = "";
      $("tabla-dotacion-intervalos").querySelector("tbody").innerHTML = "";
      $("d-kpis").innerHTML = "";
      porTipo.innerHTML = "";
      cuerpo.innerHTML = `<tr><td colspan="16" class="text-muted small p-3">
        ${esc(d.motivo || "Todavía no hay días cerrados para comparar la dotación.")}</td></tr>`;
      return;
    }
    estado.dotacion = d;
    armarSelectorDotacion(dias);

    const g = d.resumen;
    // Los tres números que deciden algo: cuánto pedimos de más, cuánto de eso es
    // del pronóstico, y si el servicio se cumplió igual con la gente que hubo.
    // Ese tercero es el que impide leer la brecha al revés.
    const objetivo = d.objetivo_nds;
    if (g) {
      const cumple = objetivo && g.nds_real !== null && g.nds_real >= objetivo;
      $("d-kpis").innerHTML = [
        kpi(signoPct(g.pron_vs_citados), "Pedíamos, vs los que hubo",
            `${num(g.a_planificar_pron)} contra ${num(g.citados)} intervalos-operador`),
        kpi(signoPct(g.real_vs_citados), "Hacía falta, vs los que hubo",
            "con la demanda que realmente entró"),
        kpi(pct(g.nds_real), "Nivel de servicio logrado",
            objetivo
              ? `con la gente que hubo; el objetivo es ${pct(objetivo)}`
                + (cumple ? " y se cumplió" : " y NO se cumplió")
              : "con la gente que hubo"),
        kpi(num(g.de_mas_por_pronostico), "De más por el pronóstico",
            `intervalos-operador de ${num(g.a_planificar_pron - g.citados)} de brecha`),
        kpi(pct(g.abandono_real), "Abandono real", `${g.dias} días con volumen`),
        // La malla contra el registro: son las horas extras y los cambios de
        // último momento, que es lo que hace que a veces haya más conectados que
        // gente con turno. Va a la vista y no escondido porque cambia la lectura
        // de la brecha de los fines de semana.
        kpi(signoPct(g.registro_vs_malla), "Turnos de más que la malla",
            `${num(g.citados)} en el registro contra ${num(g.malla)} prometidos`),
        // Los telefónicos contra los que tenían turno, que son la misma gente. Lo que
        // suman el refuerzo y otras va en cantidades: el refuerzo en porcentaje ya
        // tiene su propio KPI (qué parte de su turno estuvo en la línea).
        // En línea (sin pausas) cuando está: la distancia contra los que tenían
        // turno es ausentismo + pausas, y los logueados al lado dicen cuánto es cada
        // cosa.
        conectadosPartidos(g) && g.citados
          ? kpi(signoPct(conectadosPartidos(g).tel / g.citados - 1),
                conectadosPartidos(g).sinPausas
                  ? "Telefónicos en línea, vs los que tenían turno"
                  : "Conectados telefónicos, vs los que tenían turno",
                (conectadosPartidos(g).sinPausas
                  ? `logueados ${signoPct(g.conectados_pool / g.citados - 1)}; lo demás son pausas`
                  : `${num(g.conectados_pool)} de ${num(g.citados)}`)
                + ` · además ${num(conectadosPartidos(g).ref + conectadosPartidos(g).dig)}`
                + ` de ${REF()} y otras digitales`
                + (g.citados_parciales
                  ? ` · tenían turno incluye ${num(g.citados_parciales)} de Contingencia, SVP y Anfitrión`
                  : ""))
          : kpi(signoPct(g.conectados_vs_citados), "Conectados, vs los que tenían turno",
                "el resto son logueos de otras sub-campañas"),
        // La palanca: cuánto de lo que faltó podía tapar Digital con la gente que
        // tenía turno, y cuánto de su turno ya estuvo en la línea.
        ...(g.cubre_refuerzo !== null && g.cubre_refuerzo !== undefined
          ? [kpi(pct(g.parte_cubre_refuerzo), `Del faltante, lo cubría ${REF()}`,
                 `${num(g.cubre_refuerzo)} de ${num(g.faltante)} intervalos-operador`
                 + (conValor(g.conectados_refuerzo_en_linea) && g.refuerzo_turno
                   ? `; ${REF()} estuvo ${pct(g.conectados_refuerzo_en_linea / g.refuerzo_turno)}`
                     + " de su turno en la línea, sin pausas"
                   : conValor(g.refuerzo_en_linea)
                   ? `; ${REF()} estuvo ${pct(g.refuerzo_en_linea)} de su turno en la línea` : ""))]
          : []),
        // El número que cierra "cumplimos el NDS, ¿por qué pide más gente?":
        // porque en esa parte del requerimiento no manda el NDS.
        // Cada intervalo se dimensiona contra cuatro restricciones y queda la más
        // exigente. Esta es la proporción del pedido que sale del techo de
        // ocupación —condiciones de trabajo— y no del nivel de servicio: es la
        // respuesta a "cumplimos el NDS, por qué pide más gente".
        kpi(esc((g.motivos[0] || {}).motivo || "—"), "La restricción que más manda",
            (g.motivos || []).map((m) => `${m.motivo} ${pct(m.parte, 0)}`).join(" · ")),
        kpi(pct(g.ocupacion_modelo), "Ocupación del modelo",
            `contra ${pct(g.ocupacion_real)} real; el techo configurado es `
            + `${pct(g.max_ocupacion, 0)}`),
      ].join("");
    }

    cuerpo.innerHTML = dias.map((f) => {
      // El día de poco volumen se muestra pero no promedia: con doscientas
      // llamadas manda la cobertura mínima del pool y el cociente no dice nada
      // sobre si el dimensionamiento acierta.
      const flojo = f.poco_volumen;
      const malNds = objetivo && f.nds_real !== null && f.nds_real < objetivo;
      return `<tr class="fila-dia ${flojo ? "text-muted" : ""}" data-dia="${esc(dia(f.dia))}"
                  title="Ver este día intervalo por intervalo">
        <td>${esc(String(f.dia))}
          <small class="text-muted">${esc(TIPO_DIA[f.tipo_dia] || f.tipo_dia)}</small>
          ${flojo ? '<small class="text-muted">· poco volumen, no promedia</small>' : ""}</td>
        <td class="text-end">${num(f.llamadas)}</td>
        <td class="text-end">${num(f.a_planificar_pron)}</td>
        <td class="text-end col-detalle">${num(f.a_planificar_real)}</td>
        <td class="text-end" title="${esc(notaParciales(f).trim())}">${num(f.citados)}</td>
        <td class="text-end col-detalle ${claseBrecha(brechaMalla(f))}">${num(f.malla)}
          <small class="text-muted">${signoPct(brechaMalla(f))}</small></td>
        ${celdaConectados(f, 0, "text-end col-detalle", origenConectados(f))}
        <td class="text-end text-nowrap col-detalle">${num(f.refuerzo_turno)}
          <small class="text-muted">/ ${conectadosPartidos(f) ? num(conectadosPartidos(f).ref) : "—"}</small></td>
        <td class="text-end ${claseBrecha(f.pron_vs_citados)}">${signoPct(f.pron_vs_citados)}</td>
        <td class="text-end ${claseBrecha(f.real_vs_citados)}">${signoPct(f.real_vs_citados)}</td>
        <td class="text-end text-nowrap col-detalle ${f.faltante_neto > 0 ? "brecha-falta" : ""}">${num(f.faltante_neto)}
          <small class="text-muted fw-normal">de ${num(f.faltante)}</small></td>
        <td class="text-end ${malNds ? "text-warning-emphasis fw-semibold" : ""}">${pct(f.nds_real)}</td>
        <td class="text-end col-detalle">${pct(f.abandono_real, 2)}</td>
        <td class="text-end text-nowrap col-detalle">${num(f.cph_pron, 1)}
          <small class="text-muted">/ ${num(f.cph_real, 1)}</small></td>
        <td class="text-end col-detalle">${pct(f.ocupacion_modelo)}
          <small class="text-muted">/ ${pct(f.ocupacion_real)}</small></td>
        <td>${barraMando(f.motivos)}</td>
      </tr>`;
    }).join("");

    porTipo.innerHTML = (d.por_tipo_de_dia || []).map((t) => `
      <tr>
        <td>${esc(TIPO_DIA[t.tipo] || t.tipo)}</td>
        <td class="text-end">${t.dias}</td>
        <td class="text-end ${claseBrecha(t.pron_vs_citados)}">${signoPct(t.pron_vs_citados)}</td>
        <td class="text-end ${claseBrecha(t.real_vs_citados)}">${signoPct(t.real_vs_citados)}</td>
        <td class="text-end">${pct(t.nds_real)}</td>
        <td class="text-end">${pct(t.abandono_real, 2)}</td>
        <td class="text-end text-nowrap">${num(t.cph_pron, 1)}
          <small class="text-muted">/ ${num(t.cph_real, 1)}</small></td>
        <td class="text-end">${pct(t.ocupacion_modelo)}
          <small class="text-muted">/ ${pct(t.ocupacion_real)}</small></td>
        <td>${barraMando(t.motivos)}</td>
      </tr>`).join("");
  }

  // El color va FIJO por familia y nunca por ranking: si se reordena o filtra la
  // tabla, los colores no se mueven. La familia que no esté en el mapa se pinta
  // como "otro" en vez de inventarle un color, para que una nueva se vea.
  const MANDO_CLASE = {
    "nivel de servicio": "mando-nds",
    "techo de ocupación": "mando-ocup",
    "abandono": "mando-abandono",
    "cobertura mínima": "mando-minima",
  };

  // Qué restricción fijó la dotación. `dimensionar_intervalo` evalúa varias y se
  // queda con la MÁS EXIGENTE: ésa es la que mandó. Como en un día mandan varias
  // —el techo en el pico, el nivel de servicio en el resto— no hay un único valor:
  // va el reparto, con la que más pesa en texto al lado para que el dato no esté
  // sólo en el color, y el detalle completo en el `title`.
  function barraMando(motivos) {
    if (!motivos || !motivos.length) return '<span class="text-muted">—</span>';
    const detalle = motivos
      .map((m) => `${m.motivo}: ${pct(m.parte, 0)} (${m.intervalos} interv.)`)
      .join(" · ");
    const tramos = motivos.map((m) => `<span class="${
      MANDO_CLASE[m.motivo] || "mando-otro"}" style="width:${(m.parte * 100).toFixed(1)}%"
      ></span>`).join("");
    const principal = motivos[0];
    return `<div class="d-flex align-items-center gap-2" title="${esc(detalle)}">
              <div class="mando-barra flex-grow-1">${tramos}</div>
              <small class="text-nowrap">${esc(principal.motivo)}
                <span class="text-muted">${pct(principal.parte, 0)}</span></small>
            </div>`;
  }

  // El selector del detalle por intervalo. Arranca en el día con la brecha más
  // grande entre los que tienen volumen —el que hay que mirar—, igual que el
  // detalle de llamadas arranca en el día que más se erró.
  function armarSelectorDotacion(dias) {
    const actual = $("d-dia").value;
    $("d-dia").innerHTML = dias.map((f) =>
      `<option value="${esc(dia(f.dia))}">${esc(etiquetaDia(dia(f.dia)))} · ${
        esc(TIPO_DIA[f.tipo_dia] || f.tipo_dia)} · hacía falta ${
        signoPct(f.real_vs_citados)}</option>`).join("");
    const conVolumen = dias.filter((f) => !f.poco_volumen && f.real_vs_citados !== null);
    const peor = conVolumen.reduce((a, f) =>
      (!a || Math.abs(f.real_vs_citados) > Math.abs(a.real_vs_citados) ? f : a), null);
    const sigue = dias.some((f) => dia(f.dia) === actual);
    $("d-dia").value = sigue ? actual : (peor ? dia(peor.dia) : dia(dias[0].dia));
    dibujarDotacionDia();
  }

  // Un día de operadores, media hora por media hora. Los colores siguen a la
  // entidad y son los mismos del plan: naranja lo que se pide (sólido con la
  // demanda real, punteado con el pronóstico), violeta la malla (sólido lo que
  // hubo, punteado lo prometido) y azul los conectados.
  function dibujarDotacionDia() {
    const d = estado.dotacion;
    if (!d) return;
    const elegido = $("d-dia").value;
    const filas = (d.por_intervalo || []).filter((f) => dia(f.momento) === elegido);
    const delDia = (d.por_dia || []).find((f) => dia(f.dia) === elegido);

    document.querySelectorAll("#tabla-dotacion tbody tr.fila-dia").forEach((tr) =>
      tr.classList.toggle("elegido", tr.dataset.dia === elegido));

    if (delDia) {
      const objetivo = d.objetivo_nds;
      $("d-dia-resumen").innerHTML = `
        <strong>${esc(elegido)}</strong> · ${esc(TIPO_DIA[delDia.tipo_dia] || delDia.tipo_dia)}
        · ${num(delDia.llamadas)} llamadas
        · en línea pedíamos <strong>${num(filas.reduce((a, f) => a + (f.en_linea_pron || 0), 0))}</strong>
          y hacía falta <strong>${num(filas.reduce((a, f) => a + (f.en_linea_real || 0), 0))}</strong>
        · NDS ${pct(delDia.nds_real)}${objetivo
            ? (delDia.nds_real >= objetivo ? " (cumplió)" : ' <span class="brecha-falta">(no cumplió)</span>')
            : ""}
        <span class="text-muted">· en intervalos-operador</span>`;
    }

    dibujar("chart-dotacion-intra",
      filas.map((f) => hora(f.momento)),
      // Todo en operadores EN LÍNEA, para que se pueda comparar sin que la cadena
      // de descuentos (disponibilidad, shrinkage, break) se meta en el medio: lo que
      // pedíamos con el pronóstico, lo que hacía falta con lo que entró y los que
      // estuvieron. Tenían turno va aparte, en gente a citar.
      [
        { label: "Pedíamos en línea (pronóstico)", data: filas.map((f) => f.en_linea_pron),
          borderColor: COLOR.planificar, borderDash: [5, 4] },
        { label: "Hacía falta en línea (lo que entró)", data: filas.map((f) => f.en_linea_real),
          borderColor: COLOR.planificar },
        // Conectados partidos como los citados: telefónicos solos (sólido) y
        // sumando al refuerzo (punteado). Los de otras sub-campañas no van en
        // ninguna de las dos; se ven en la tabla, pasando el mouse.
        ...(filas.some((f) => conectadosPartidos(f))
          ? [{ label: filas.some((f) => (conectadosPartidos(f) || {}).sinPausas)
                 ? "Conectados telefónicos (en línea, sin pausas)" : "Conectados telefónicos",
               data: filas.map((f) => (conectadosPartidos(f) || {}).tel ?? null),
               borderColor: COLOR.real },
             { label: `Conectados telefónicos + ${REF()}`,
               data: filas.map((f) => (conectadosPartidos(f) || {}).conRef ?? null),
               borderColor: COLOR.real, borderDash: [5, 4] }]
          : [{ label: "Conectados", data: filas.map((f) => f.conectados),
               borderColor: COLOR.real }]),
        { label: "Tenían turno", data: filas.map((f) => f.citados),
          borderColor: COLOR.citados },
        ...(filas.some((f) => f.refuerzo_turno !== null && f.refuerzo_turno !== undefined)
          ? [{ label: `Tenían turno + ${REF()}`,
               data: filas.map((f) => f.citados + (f.refuerzo_turno || 0)),
               borderColor: COLOR.refuerzo, borderDash: [2, 3] }]
          : []),
      ], true, (t) => `${elegido} ${t[0].label}`);

    const cuerpo = $("tabla-dotacion-intervalos").querySelector("tbody");
    if (!filas.length) {
      cuerpo.innerHTML = `<tr><td colspan="16" class="text-muted small p-3">
        No hay intervalos para ese día.</td></tr>`;
      return;
    }
    const objetivo = d.objetivo_nds;
    cuerpo.innerHTML = filas.map((f) => {
      const diferencia = f.citados - f.a_planificar_real;
      // Con menos de diez llamadas el NDS de media hora no dice nada: se muestra
      // igual, atenuado, en vez de esconderlo.
      const pocas = (f.entrantes || 0) < 10;
      const malNds = !pocas && objetivo && f.nds_real !== null && f.nds_real < objetivo;
      // "Faltó" tiene dos lecturas muy distintas y se pintan distinto. Si los que
      // tenían turno alcanzaban para los operadores EN LÍNEA que pedía el modelo, lo
      // único que faltó es el margen de la cadena (disponibilidad, shrinkage, break):
      // atenuado. Si ni siquiera alcanzaban para los de en línea, faltó gente para
      // atender: rojo. Es lo que evita un "−1" en rojo al lado de un NDS de 100% en
      // una madrugada de una o dos llamadas.
      const soloMargen = diferencia < 0 && f.citados >= f.en_linea_real;
      const contraPiso = f.motivo === "cobertura mínima";
      const claseDif = diferencia < 0 ? (soloMargen ? "text-muted" : "brecha-falta")
                                      : (diferencia > 0 ? "brecha-sobra" : "");
      const porQueDif = diferencia >= 0 ? ""
        : (soloMargen
            ? `Los ${f.citados} con turno cubrían los ${f.en_linea_real} en línea que pedía el modelo`
              + (contraPiso ? " (el piso de cobertura del pool)" : "")
              + ": lo que faltó es sólo el margen por disponibilidad, shrinkage y break."
            : `Los ${f.citados} con turno no cubrían ni los ${f.en_linea_real} en línea que pedía el modelo.`)
          + (f.refuerzo_turno
            ? ` ${REF()} tenía ${f.refuerzo_turno} con turno: cubría ${f.cubre_refuerzo}`
              + (f.faltante_neto ? ` y faltaban igual ${f.faltante_neto}.` : " y alcanzaba.")
            : "");
      // Pedíamos y Hacía falta se muestran EN LÍNEA, así se comparan entre sí sin
      // los descuentos de por medio. La misma cadena, en el tooltip de cada uno, dice
      // a cuánta gente a citar equivalían.
      const cadena = (enLinea, llamadas, deQue, aCitar) => {
        if (!enLinea || !f.disponibilidad) return "";
        const br = f.factor_break || 0;
        const bruto = enLinea / f.disponibilidad / (1 - (f.shrinkage || 0)) / (1 - br);
        return `Con ${num(llamadas)} llamadas ${deQue}: ${num(enLinea)} en línea.`
          + ` Para tenerlos había que citar ${num(enLinea)} ÷ disponibilidad ${pct(f.disponibilidad, 0)}`
          + ` ÷ (1 − shrinkage ${pct(f.shrinkage, 1)}) ÷ (1 − break ${pct(br, 1)})`
          + ` = ${num(bruto, 2)} → ${num(aCitar)} a citar`
          + (f.redondeo_abajo ? " (redondeado para abajo en esta franja)." : ".");
      };
      const tituloTurno = (f.en_linea_real
        ? `Gente con turno, en personas a citar (incluye al que faltó). Para los ${num(f.en_linea_real)}`
          + ` en línea que hacían falta había que citar ${num(f.a_planificar_real)}.`
        : "Gente con turno, en personas a citar (incluye al que faltó).") + notaParciales(f);
      const cadenaPron = cadena(f.en_linea_pron, f.llamadas_pron, "pronosticadas", f.a_planificar_pron);
      const cadenaReal = cadena(f.en_linea_real, f.llamadas_real, "reales", f.a_planificar_real);
      // Menos conectados que los que hacían falta en la línea: faltó gente atendiendo,
      // aun contando las pausas como si fueran tiempo en la cola. Se mira contra
      // telefónicos + refuerzo, que es el número de la celda: contra el total, una
      // celda que muestra menos que «hacía falta» podía quedar sin marcar.
      const partidos = conectadosPartidos(f);
      const conectadosCelda = partidos ? partidos.conRef : f.conectados;
      const faltoEnLinea = !pocas && f.en_linea_real && conectadosCelda < f.en_linea_real;
      const tituloCon = origenConectados(f)
        + (faltoEnLinea ? ` — menos conectados${partidos ? ` telefónicos + ${REF()}` : ""}`
                          + " que los que hacían falta en la línea." : "");
      return `<tr>
        <td class="text-nowrap">${hora(f.momento)}</td>
        <td class="text-end text-nowrap">${num(f.llamadas_pron)}
          <small class="text-muted">/ ${num(f.llamadas_real)}</small></td>
        <td class="text-end"><span class="con-cuenta" title="${esc(cadenaPron)}">${num(f.en_linea_pron)}</span></td>
        <td class="text-end fw-semibold"><span class="con-cuenta" title="${esc(cadenaReal)}">${num(f.en_linea_real)}</span></td>
        ${celdaConectados(f, 1, `text-end ${faltoEnLinea ? "brecha-falta" : ""}`, tituloCon)}
        <td class="text-end text-muted" title="${esc(tituloTurno)}">${num(f.citados)}</td>
        <td class="text-end text-muted col-detalle">${num(f.malla)}</td>
        <td class="text-end text-nowrap col-detalle">${num(f.refuerzo_turno)}
          <small class="text-muted">/ ${conectadosPartidos(f) ? num(conectadosPartidos(f).ref, 1) : "—"}</small></td>
        <td class="text-end col-detalle ${claseDif}" title="${esc(porQueDif)}">${
          diferencia > 0 ? "+" : ""}${diferencia}</td>
        <td class="text-end col-detalle ${f.faltante_neto > 0 ? "brecha-falta" : "text-muted"}">${
          f.faltante_neto === null || f.faltante_neto === undefined || diferencia >= 0
            ? "—" : (f.faltante_neto > 0 ? `−${f.faltante_neto}` : "cubre")}</td>
        <td class="text-end ${pocas ? "text-muted" : ""} ${malNds ? "brecha-falta" : ""}">${pct(f.nds_real, 0)}</td>
        <td class="text-end col-detalle ${pocas ? "text-muted" : ""}">${pct(f.abandono_real, 1)}</td>
        <td class="text-end text-nowrap col-detalle">${num(f.cph_pron, 1)}
          <small class="text-muted">/ ${num(f.cph_real, 1)}</small></td>
        <td class="text-end text-nowrap col-detalle">${pct(f.ocupacion_modelo, 0)}
          <small class="text-muted">/ ${pct(f.ocupacion_real, 0)}</small></td>
        <td class="text-nowrap" title="${esc(f.motivo_detalle || "")}">${f.motivo
          ? `<i class="mando-punto ${MANDO_CLASE[f.motivo] || "mando-otro"}"></i>${esc(f.motivo)}`
          : '<span class="text-muted">—</span>'}</td>
      </tr>`;
    }).join("");
  }

  // Cuántos turnos de más que la malla hubo ese día: horas extras y cambios de
  // último momento. Se mide el registro CONTRA la malla porque la pregunta es
  // cuánto le faltaba a lo prometido, no cuánto sobraba en lo que pasó.
  function brechaMalla(f) {
    return f.malla ? f.citados / f.malla - 1 : null;
  }

  // Los conectados partidos igual que los citados del plan: los de las telefónicas
  // solos y sumando a los del refuerzo. Así cada uno se compara con su par —tenían
  // turno contra conectados telefónicos, turno + Digital contra conectados +
  // Digital— sin que los de otras sub-campañas se metan en el medio. Null si no se
  // pudo partir por origen: ahí queda el total, como antes.
  //
  // EN LÍNEA, SIN PAUSAS: el que está en break (o en pausa activa, administrativa,
  // etc.) está logueado pero no atiende, así que sale de la línea. Es lo que se
  // compara contra «hacía falta en línea». Si la lectura no trajo las pausas, se
  // cae a los logueados y `sinPausas` queda en false para decirlo.
  const conValor = (v) => v !== null && v !== undefined;
  //
  // QUIÉN ES QUÉ (migración 2026-09-16, respuesta de la operación): «telefónicos»
  // incluye a Contingencia, Gestión SVP y BU - Anfitrión cuando están en la línea;
  // «+ Digital» suma la palanca (`ref`) y las digitales que no se pasan al teléfono
  // (`dig`: BackOffice, RRSS…). `ref` va aparte porque es lo que se compara con la
  // gente de Digital con turno.
  function conectadosPartidos(f) {
    if (!conValor(f.conectados_pool)) return null;
    const armar = (tel, ref, dig, sinPausas) =>
      ({ tel, ref: ref || 0, dig: dig || 0, conRef: tel + (ref || 0) + (dig || 0), sinPausas });
    if (conValor(f.conectados_pool_en_linea)) {
      return armar(f.conectados_pool_en_linea, f.conectados_refuerzo_en_linea,
                   f.conectados_digital_en_linea, true);
    }
    return armar(f.conectados_pool, f.conectados_refuerzo, f.conectados_digital, false);
  }

  // La celda de conectados: telefónicos y, al lado, telefónicos + refuerzo.
  function celdaConectados(f, d, clase, titulo) {
    const c = conectadosPartidos(f);
    if (!c) return `<td class="${clase}" title="${esc(titulo)}">${num(f.conectados, d)}</td>`;
    return `<td class="${clase} text-nowrap" title="${esc(titulo)}">${num(c.tel, d)}` +
      ` <small class="text-muted">/ ${num(c.conRef, d)}</small></td>`;
  }

  // De dónde era la gente conectada, para el `title` de la celda.
  function origenConectados(f) {
    if (!conValor(f.conectados_pool)) return "";
    // Las digitales que no son palanca y las sin clasificar sólo se nombran si hay.
    const partes = (tel, ref, dig, ded, otras, d) =>
      `${num(tel, d)} telefónicos · ${num(ref, d)} de ${REF()}`
      + (dig ? ` · ${num(dig, d)} de otras digitales` : "")
      + (ded ? ` · ${num(ded, d)} de colas dedicadas (T1 - Consumo), que no cuentan` : "")
      + (otras ? ` · ${num(otras, d)} sin clasificar` : "");
    const logueados = partes(f.conectados_pool, f.conectados_refuerzo, f.conectados_digital,
                             f.conectados_dedicada, f.conectados_otras, 1)
      + ` (${num(f.conectados, 1)} en total)`;
    if (!conValor(f.conectados_pool_en_linea)) return `Logueados: ${logueados}.`;
    const pausa = f.conectados - f.conectados_pool_en_linea
      - (f.conectados_refuerzo_en_linea || 0) - (f.conectados_digital_en_linea || 0)
      - (f.conectados_dedicada_en_linea || 0) - (f.conectados_otras_en_linea || 0);
    return `En línea, sin pausas: ${partes(f.conectados_pool_en_linea, f.conectados_refuerzo_en_linea,
                                           f.conectados_digital_en_linea, f.conectados_dedicada_en_linea,
                                           f.conectados_otras_en_linea, 1)}.`
      + ` Logueados: ${logueados}; ${num(pausa, 1)} en break u otras pausas.`
      + " Telefónicos incluye a Contingencia, Gestión SVP y BU - Anfitrión cuando atienden.";
  }

  // «Tenían turno» con cuántos eran de las sub-campañas que atienden a veces.
  function notaParciales(f) {
    return f.citados_parciales
      ? ` Incluye ${num(f.citados_parciales)} de Contingencia, Gestión SVP y BU - Anfitrión,`
        + " contados sólo en las medias horas de su turno en que estuvieron en la línea."
      : "";
  }

  // Las dos tablas de operadores abren con lo que se lee de un vistazo (8
  // columnas); el resto —malla, Digital, abandono, CPH, ocupación— queda detrás
  // de un interruptor. Un solo estado para las dos tablas, recordado en el
  // navegador: quien lo prende es porque quiere el detalle en las dos.
  const CLAVE_DETALLE = "planificador.operadores.todas_las_columnas";

  function mostrarDetalleDotacion(todas) {
    ["tabla-dotacion", "tabla-dotacion-intervalos"].forEach((id) => {
      if ($(id)) $(id).classList.toggle("tabla-resumida", !todas);
    });
    ["d-detalle", "d-detalle-dia"].forEach((id) => { if ($(id)) $(id).checked = todas; });
    try { localStorage.setItem(CLAVE_DETALLE, todas ? "1" : "0"); } catch (e) { /* sin storage */ }
  }

  function detalleGuardado() {
    try { return localStorage.getItem(CLAVE_DETALLE) === "1"; } catch (e) { return false; }
  }

  // La tabla de intervalos del plan abre resumida (visibles las 9 columnas clave);
  // el resto de métricas y referencias queda detrás de un interruptor recordado.
  const CLAVE_DETALLE_PLAN = "planificador.plan.todas_las_columnas";

  function mostrarDetallePlan(todas) {
    if ($("tabla-intervalos")) $("tabla-intervalos").classList.toggle("tabla-resumida", !todas);
    if ($("p-detalle")) $("p-detalle").checked = todas;
    try { localStorage.setItem(CLAVE_DETALLE_PLAN, todas ? "1" : "0"); } catch (e) { /* sin storage */ }
  }

  function detallePlanGuardado() {
    try { return localStorage.getItem(CLAVE_DETALLE_PLAN) === "1"; } catch (e) { return false; }
  }

  // Un cociente con signo: el "+" importa tanto como el número, porque pedir 10%
  // de más y 10% de menos son problemas opuestos.
  function signoPct(x) {
    if (x === null || x === undefined) return "—";
    return (x > 0 ? "+" : "") + pct(x);
  }

  // Se pinta sólo lo grande. Un 8% de diferencia entre el plan y la malla es
  // ruido de redondeo de cuarenta y ocho intervalos; un 60% es otra cosa.
  function claseBrecha(x) {
    if (x === null || x === undefined) return "";
    if (Math.abs(x) >= 0.30) return "text-danger-emphasis fw-semibold";
    if (Math.abs(x) >= 0.15) return "text-warning-emphasis";
    return "";
  }

  // Quién acierta más en cada tipo de día. Es la tabla que contesta la pregunta
  // que se hace todo el mundo mirando el tablero de la operación: el promedio de
  // la semana esconde que el domingo es otro problema.
  function pintarTipoDeDia(filas) {
    const cuerpo = $("tabla-tipo-dia").querySelector("tbody");
    if (!filas.length) {
      cuerpo.innerHTML = `<tr><td colspan="8" class="text-muted small p-3">
        Todavía no hay días cerrados para partir por tipo.</td></tr>`;
      return;
    }
    cuerpo.innerHTML = filas.map((f) => {
      const mape = (x) => (x ? pct(x.mape) : "—");
      // "Gana" compara sólo las dos series que existen de verdad en producción:
      // la nuestra y la del cliente. El combinado no compite, es la mezcla.
      const nos = f.modelo ? f.modelo.mape : null;
      const suyo = f.produccion ? f.produccion.mape : null;
      let gana = '<span class="text-muted">—</span>';
      if (nos !== null && suyo !== null) {
        const dif = Math.abs(nos - suyo);
        gana = dif < 0.02
          ? '<span class="text-muted">empatan</span>'
          : (nos < suyo
            ? '<span class="badge bg-success-subtle text-success-emphasis">el nuestro</span>'
            : '<span class="badge bg-warning-subtle text-warning-emphasis">el del cliente</span>')
            + ` <small class="text-muted">por ${pct(dif)}</small>`;
      }
      return `<tr>
        <td>${TIPO_DIA[f.tipo] || esc(f.tipo)}</td>
        <td class="text-end">${num(f.n)}</td>
        <td class="text-end">${num(f.real)}</td>
        <td class="text-end">${mape(f.modelo)}</td>
        <td class="text-end">${mape(f.produccion)}</td>
        <td class="text-end">${mape(f.combinado)}</td>
        <td class="text-end">${mape(f.ingenuo)}</td>
        <td>${gana}</td>
      </tr>`;
    }).join("");
  }

  // ------------------------------------------- peso del pronóstico del cliente

  async function medirCombinacion() {
    await conBoton($("btn-medir-combinacion"), "Midiendo…", async () => {
      const p = new URLSearchParams({ campana_id: estado.campana,
                                      dias: $("c-dias").value,
                                      antelacion: ($("l-antelacion") || $("b-antelacion")).value });
      try {
        pintarCombinacion(await pedir(`combinacion?${p}`));
      } catch (e) {
        $("c-resultado").innerHTML = aviso(
          `No se pudo medir: ${esc(e.message)}`, "warning", "exclamation-triangle");
      }
    });
  }

  function pintarCombinacion(r) {
    const fila = (t) => {
      const x = (r.por_tipo_de_dia || []).find((v) => v.tipo === t);
      if (!x) return "";
      const w = (r.pesos || {})[t];
      const nombre = { habil: "Hábiles", sabado: "Sábados (no se combinan)",
                       no_habil: "Domingos y feriados" }[t] || t;
      return `<tr>
        <td>${nombre}</td>
        <td class="text-end">${num(x.n)}</td>
        <td class="text-end">${pct(x.nuestro.mape)}</td>
        <td class="text-end">${pct(x.cliente.mape)}</td>
        <td class="text-end">${pct(x.combinado.mape)}</td>
        <td class="text-end fw-semibold">${w ? w.peso.toFixed(3) : "0"}</td>
      </tr>`;
    };
    const noHabil = (r.pesos || {}).no_habil;
    const gana = r.total.combinado.mape < r.total.nuestro.mape;
    $("c-resultado").innerHTML = `
      <table class="table table-sm align-middle">
        <thead class="table-light"><tr>
          <th>Días</th><th class="text-end">n</th><th class="text-end">Nuestro</th>
          <th class="text-end">Del cliente</th><th class="text-end">Combinado</th>
          <th class="text-end">Peso</th>
        </tr></thead>
        <tbody>${fila("habil")}${fila("sabado")}${fila("no_habil")}</tbody>
      </table>
      <div class="alert ${gana ? "alert-success" : "alert-warning"} py-2 px-3 small">
        Sobre ${num(r.dias)} días (${r.desde} al ${r.hasta}, antelación
        ${r.antelacion}): el nuestro erró ${pct(r.total.nuestro.mape)}, el del
        cliente ${pct(r.total.cliente.mape)} y la mezcla
        ${pct(r.total.combinado.mape)}.
        <strong>${gana ? "Combinar mejora." : "Combinar no mejora: conviene dejarlo apagado."}</strong>
      </div>
      ${EDITA ? `<button class="btn btn-sm btn-outline-primary" id="btn-aplicar-combinacion"
          data-habil="${(r.pesos.habil || {}).peso || 0}"
          data-nohabil="${(noHabil || {}).peso || 0}">
          <i class="bi bi-check2 me-1"></i>Dejar estos pesos vigentes
        </button>
        <div class="form-text">Guardar el peso no prende la combinación: la llave
        está arriba, en «Modelo de pronóstico vigente».</div>` : ""}`;
    const btn = $("btn-aplicar-combinacion");
    if (btn) btn.addEventListener("click", () => aplicarCombinacion(btn));
  }

  async function aplicarCombinacion(btn) {
    await conBoton(btn, "Guardando…", async () => {
      try {
        await pedir(`combinacion/aplicar?campana_id=${estado.campana}`, {
          method: "POST",
          body: JSON.stringify({ peso_habil: Number(btn.dataset.habil),
                                 peso_no_habil: Number(btn.dataset.nohabil) }),
        });
        await cargarTodo();
        btn.outerHTML = `<span class="text-success small">
          <i class="bi bi-check2-circle me-1"></i>Pesos guardados. Para que muevan
          el pronóstico hay que activar la combinación arriba, en «Modelo de
          pronóstico vigente».</span>`;
      } catch (e) {
        $("avisos").innerHTML = aviso(`No se pudieron guardar los pesos: ${esc(e.message)}`,
                                      "danger", "x-octagon");
      }
    });
  }

  function dibujarIntradia() {
    const bt = estado.backtest;
    if (!bt) return;
    const elegido = $("b-dia").value;
    const filas = (bt.por_intervalo || []).filter((f) => dia(f.momento) === elegido);
    dibujar("chart-backtest-intra",
      filas.map((f) => hora(f.momento)),
      [
        { label: "Real", data: filas.map((f) => f.real), borderColor: COLOR.real },
        { label: "Pronosticado", data: filas.map((f) => f.pronosticado), borderColor: COLOR.pronost },
        { label: "dbo.Forecast (el de hoy)", data: filas.map((f) => f.produccion),
          borderColor: COLOR.produccion, borderDash: [2, 3] },
        ...(filas.some((f) => f.combinado !== null && f.combinado !== f.pronosticado)
          ? [{ label: "Combinado", data: filas.map((f) => f.combinado),
               borderColor: COLOR.combinado, borderDash: [6, 2] }] : []),
      ], true, (t) => `${elegido} ${t[0].label}`);
  }

  const RASGO = {
    cdd: "calor (grados sobre 24 aparentes)",
    cdd2: "calor, al cuadrado",
    hdd: "frío (grados bajo 14 aparentes)",
    hdd2: "frío, al cuadrado",
    cdd_ayer: "calor del día anterior",
    hdd_ayer: "frío del día anterior",
    lluvia: "lluvia (log de los mm)",
    rafaga: "ráfagas sobre 40 km/h",
    cdd_3d: "calor acumulado de 3 días",
    hdd_3d: "frío acumulado de 3 días",
    cdd_7d: "calor acumulado de 7 días",
    hdd_7d: "frío acumulado de 7 días",
    humedad: "humedad (sobre 60%)",
    viento: "viento sostenido",
    amplitud: "amplitud térmica del día",
    anual_sin: "estacionalidad anual (seno)",
    anual_cos: "estacionalidad anual (coseno)",
    anual_sin2: "estacionalidad semestral (seno)",
    anual_cos2: "estacionalidad semestral (coseno)",
    dia_mes_sin: "ciclo de facturación (seno)",
    dia_mes_cos: "ciclo de facturación (coseno)",
    vispera: "víspera de feriado",
    post_feriado: "día después de un feriado",
    finde_largo: "fin de semana largo",
    vacaciones: "enero o vacaciones de invierno",
  };
;

  function tarjetaNivel(n) {
    // La segunda opinión del nivel diario: un modelo de árboles que estima el
    // total del día y se promedia en logaritmo con el de clima. Se informa
    // siempre, prendida o apagada: "está prendida y no le alcanzaron los datos"
    // y "está apagada" son dos cosas distintas.
    if (!n) return "";
    if (!n.usado) {
      return `<div class="alert alert-light border py-2 px-3 small mb-0">
                <i class="bi bi-diagram-3 me-1"></i>
                La segunda opinión del nivel diario no se usó: ${esc(n.motivo || "")}.
                Se puede probar en «Probar un cambio» para medir si conviene activarla.
              </div>`;
    }
    return `<div class="alert alert-light border py-2 px-3 small mb-0">
        <i class="bi bi-diagram-3 me-1"></i>
        <strong>Segunda opinión del nivel diario</strong> aplicada con peso
        ${(n.peso * 100).toFixed(0)}%: el total de cada día es el promedio
        geométrico entre lo que dice el modelo de siempre y lo que dice un modelo
        de árboles con ${num(n.rasgos)} rasgos de clima y calendario.
        Entrenado con ${num(n.filas)} días-cola sobre ${num(n.skills)} colas,
        reajustado en ${num(n.cortes_con_modelo)} de ${num(n.cortes)} fechas de corte.
        <details class="porque mt-1"><summary>Por qué mezclar y no reemplazar</summary><div>
          El modelo de árboles <strong>solo</strong> pierde contra la regresión que ya
          está: con pocos miles de filas aprende ruido que una regresión de doce rasgos
          no puede aprender. Mezclado gana porque los dos se equivocan en lugares
          distintos. A cambio el pronóstico tiende a quedarse un poco corto; corregir
          ese sesgo se probó y empeora todo lo demás.
        </div></details>
      </div>`;
  }

  function tarjetaClima(c) {
    if (!c) return "";
    if (!c.usado) {
      return `<div class="alert alert-light border py-2 px-3 small mb-0">
                <i class="bi bi-cloud-slash me-1"></i>
                El modelo de clima no se usó en esta comparación: ${esc(c.motivo || "")}.
                Se puede probar en «Probar un cambio» para medir si conviene activarlo.
              </div>`;
    }
    const m = c.modelo || {};
    const co = m.coeficientes || {};
    const porSkill = c.por_skill || {};
    // Se ajusta un modelo POR SKILL: las colas no responden igual al tiempo, y
    // promediarlas diluía justo la que más importa el fin de semana.
    const filasSkill = Object.entries(porSkill).map(([k, v]) =>
      `<tr><td>${esc(estado.nombreSkill ? estado.nombreSkill(k) : k)}</td>
           <td class="text-end">${pct(v.r2)}</td>
           <td class="text-end">${num(v.n)}</td></tr>`).join("");
    // Los coeficientes se muestran como efecto multiplicativo por unidad, que es
    // lo único que alguien puede leer: "cada grado de frío suma 1,3%".
    const filas = Object.keys(RASGO).filter((k) => co[k] !== undefined).map((k) =>
      `<tr><td>${esc(RASGO[k])}</td>
           <td class="text-end">${((Math.exp(co[k]) - 1) * 100).toFixed(2)}%</td></tr>`).join("");
    return `<div class="card shadow-sm">
      <div class="card-header bg-white d-flex justify-content-between align-items-center flex-wrap gap-2">
        <span class="fw-semibold"><i class="bi bi-thermometer-half me-1"></i>Clima</span>
        <small class="text-muted">
          explica el ${pct(m.r2)} del nivel diario · factor x${(c.factor_min || 0).toFixed(2)}
          a x${(c.factor_max || 0).toFixed(2)} en estos días
        </small>
      </div>
      <details><summary class="px-3 py-2 small" style="cursor:pointer">Ver el modelo</summary>
      <div class="card-body pt-0">
        <div class="row g-3">
          <div class="col-md-5">
            <div class="small">
              Entrenado con ${num(m.n)} días observados hasta el ${dia(m.entrenado_hasta)}.
              Baja el desvío del residuo un <strong>${pct(m.reduccion_residuo)}</strong>
              y se aplicó a ${num(c.dias_con_factor)} de ${num(c.dias_evaluados)} días
              y a <strong>${num(c.skills_con_factor)} skill(s)</strong>.
            </div>
            ${filasSkill ? `<table class="table table-sm mt-2 mb-0">
              <thead class="table-light"><tr><th>Skill</th>
                <th class="text-end">Explica</th><th class="text-end">Días</th></tr></thead>
              <tbody>${filasSkill}</tbody></table>
              <div class="porque mt-1">
                Los skills que no llegan al ${pct(0.10, 0)} no reciben factor: aplicarles
                uno que no explica nada les mete ruido y —peor— diluye el de los que sí.
                COMERCIAL es facturación y trámites, el clima no la mueve; EMERGENCIAS sí.
              </div>` : ""}
            <div class="porque mt-2">
              El factor multiplica el perfil estacional <em>antes</em> de la corrección
              de nivel, y la corrección se calcula sobre lo que el clima no explicó.
              Al revés, las dos estarían explicando lo mismo dos veces.
            </div>
          </div>
          <div class="col-md-7">
            <table class="table table-sm mb-0">
              <thead class="table-light"><tr>
                <th>Rasgo</th><th class="text-end">Efecto por unidad</th>
              </tr></thead>
              <tbody>${filas}</tbody>
            </table>
          </div>
        </div>
      </div></details></div>`;
  }

  function celdaDesvio(desvio) {
    if (desvio === null || desvio === undefined) return '<td class="text-end">—</td>';
    // Se marca en rojo sólo lo que pasa el 10%, que es el mismo umbral con el que
    // avisa el seguimiento intradía. Dos umbrales distintos para la misma idea
    // haría que la pantalla se contradiga sola.
    const clase = Math.abs(desvio) >= 0.10 ? "brecha-falta" : "brecha-sobra";
    const signo = desvio > 0 ? "+" : "";
    return `<td class="text-end ${clase}">${signo}${(desvio * 100).toFixed(1)}%</td>`;
  }

  function pintarTablaBacktest(dias) {
    $("tabla-backtest").querySelector("tbody").innerHTML = dias.map((d) => `
      <tr${d.parcial ? ' class="table-light"' : ""}>
        <td>${dia(d.dia)}${d.parcial ? ' <span class="badge bg-secondary">en curso</span>' : ""}</td>
        <td class="text-end">${num(d.real)}</td>
        <td class="text-end">${num(d.pronosticado)}</td>
        <td class="text-end">${d.real === null || d.pronosticado === null ? "—"
                               : num(d.real - d.pronosticado)}</td>
        ${celdaDesvio(d.desvio)}
        <td class="text-end">${num(d.ingenuo)}</td>
        <td class="text-end">${num(d.produccion)}</td>
        ${celdaDesvio(d.desvio_produccion)}
        <td class="text-end">${num(d.combinado)}${d.peso_cliente
            ? ` <small class="text-muted">x${d.peso_cliente.toFixed(2)}</small>` : ""}</td>
        <td class="text-end">${d.factor_clima ? "x" + d.factor_clima.toFixed(2) : "—"}</td>
        <td class="text-end">${num(d.real_total)}</td>
        <td class="text-end">${pct(d.share_real)}</td>
        <td class="text-end">${pct(d.share_aplicado)}</td>
        <td class="small text-muted">${esc(d.evento || "")}</td>
      </tr>`).join("");
  }

  // ------------------------------------------------------- módulos de la pantalla

  // Las partes nuevas de la pantalla viven en sus propios archivos
  // (planificador_salud.js, _insumos, _vista, _refuerzos, _escenarios) y llegan a
  // lo de acá por este objeto, en vez de hacer crecer este archivo sin fin. Los
  // eventos `planificador:config` y `planificador:plan` avisan cuándo hay datos
  // nuevos: se disparan en cada carga, también al cambiar de campaña.
  window.Planificador = {
    estado, pedir, pedirBacktest, aviso, kpi, esc, pct, num, hora, dia, dibujar, conBoton,
    redibujar, recargar: cargarTodo, cargarRefuerzos, puedeEditar: EDITA, COLOR, api: API,
    descarga: "/planificador/descargar",
    tarjetaClima, tarjetaNivel, refuerzo: REF, tipoDia: TIPO_DIA, tipoDeDiaPlan,
  };

  // ------------------------------------------------------------------- inicio

  document.addEventListener("DOMContentLoaded", () => {
    estado.campana = campanaInicial();
    $("f-campana").addEventListener("change", (e) => {
      estado.campana = Number(e.target.value);
      recordarCampana();
      enlaceExcelPlan();
      cargarTodo();
    });
    ["f-dia", "d-dia", "b-dia"].forEach(conFlechas);
    $("f-pool").addEventListener("change", redibujar);
    $("f-dia").addEventListener("change", redibujar);
    $("btn-refrescar").addEventListener("click", cargarTodo);
    fechasPorDefecto();
    $("btn-comparar").addEventListener("click", comparar);
    if ($("btn-medir-combinacion"))
      $("btn-medir-combinacion").addEventListener("click", medirCombinacion);
    if ($("btn-guardar-clima")) $("btn-guardar-clima").addEventListener("click", guardarClima);
    $("b-dia").addEventListener("change", dibujarIntradia);
    $("d-dia").addEventListener("change", dibujarDotacionDia);
    ["d-detalle", "d-detalle-dia"].forEach((id) => {
      if ($(id)) $(id).addEventListener("change", (ev) => mostrarDetalleDotacion(ev.target.checked));
    });
    mostrarDetalleDotacion(detalleGuardado());
    if ($("p-detalle")) {
      $("p-detalle").addEventListener("change", (ev) => mostrarDetallePlan(ev.target.checked));
    }
    mostrarDetallePlan(detallePlanGuardado());
    // Clic en un día de la tabla de operadores: se abre ese día abajo. Delegado en
    // el tbody porque las filas se regeneran en cada comparación.
    $("tabla-dotacion").querySelector("tbody").addEventListener("click", (ev) => {
      const fila = ev.target.closest("tr.fila-dia");
      if (!fila) return;
      $("d-dia").value = fila.dataset.dia;
      dibujarDotacionDia();
      $("card-dotacion-dia").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("btn-calibrar").addEventListener("click", medir);
    if ($("btn-recalcular")) $("btn-recalcular").addEventListener("click", recalcular);
    if ($("btn-guardar-campana")) $("btn-guardar-campana").addEventListener("click", guardarCampana);
    if ($("btn-guardar-franjas")) $("btn-guardar-franjas").addEventListener("click", guardarFranjas);
    if ($("btn-agregar-franja")) $("btn-agregar-franja").addEventListener("click", () => {
      estado.franjas.push({ dia_semana: 0, hora_desde: 0, hora_hasta: 24, factor: 1 });
      pintarFranjas();
    });
    if ($("btn-guardar-modelo")) $("btn-guardar-modelo").addEventListener("click", guardarModelo);
    cargarCampanas().then(() => {
      enlaceExcelPlan();
      cargarTodo();
    });
  });

  // --------------------------------------------------------------- campañas

  // La de la URL (?campana=), la última elegida en este navegador, o Voltara, que
  // era la única cuando el selector estaba escrito en el HTML.
  const CLAVE_CAMPANA = "planificador.campana";

  function campanaInicial() {
    const deUrl = Number(new URLSearchParams(window.location.search).get("campana"));
    if (deUrl) return deUrl;
    try {
      const guardada = Number(window.localStorage.getItem(CLAVE_CAMPANA));
      if (guardada) return guardada;
    } catch (e) { /* sin almacenamiento local: se usa la de siempre */ }
    return 20;
  }

  function recordarCampana() {
    try { window.localStorage.setItem(CLAVE_CAMPANA, String(estado.campana)); } catch (e) { /* idem */ }
    const url = new URL(window.location.href);
    url.searchParams.set("campana", estado.campana);
    window.history.replaceState(null, "", url);
  }

  // El selector sale de planificacion.Campana con el alcance por empresa del
  // usuario. Si la lista no llega, queda la opción del HTML.
  async function cargarCampanas() {
    const sel = $("f-campana");
    try {
      const lista = (await pedir("campanas")).campanas || [];
      if (lista.length) {
        if (!lista.some((c) => c.campana_id === estado.campana)) {
          estado.campana = (lista.find((c) => c.activa && c.con_fuente) || lista[0]).campana_id;
        }
        sel.innerHTML = lista.map((c) =>
          `<option value="${c.campana_id}">${esc(c.nombre)}` +
          `${c.con_fuente ? "" : " (sin fuente de datos)"}${c.activa ? "" : " (inactiva)"}</option>`
        ).join("");
        // Con una sola campaña el selector no elige nada.
        sel.disabled = lista.length === 1;
      }
    } catch (e) { /* queda la opción del HTML */ }
    sel.value = String(estado.campana);
  }

  function enlaceExcelPlan() {
    $("btn-excel-plan").href = `${DESCARGA}/plan?campana_id=${estado.campana}`;
  }
})();
