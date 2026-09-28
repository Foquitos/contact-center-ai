/* Planificador — laboratorio: probar un cambio del modelo de pronóstico.
 *
 * Corre la misma comparación dos veces —como está configurado y con el cambio— y
 * pone los errores uno al lado del otro. Medir no guarda nada; «Dejar vigente»
 * escribe sólo los parámetros que se cambiaron y el próximo recálculo los usa.
 *
 * Parte de la pantalla en archivo propio: usa window.Planificador.
 */
(function () {
  "use strict";

  const P = () => window.Planificador;
  const $ = (id) => document.getElementById(id);

  // Llaves de sí/no: parámetro de GET /backtest -> clave de PUT /config/campana,
  // y cómo se lee lo vigente en la configuración.
  const LLAVES = [
    { id: "l-clima", param: "clima", clave: "clima_activo", nombre: "clima",
      vigente: (c) => !!(c.clima || {}).activo },
    { id: "l-elast", param: "elasticidad", clave: "clima_elasticidad_tipo_dia",
      nombre: "atenuación del domingo", vigente: (c) => !!c.clima_elasticidad_tipo_dia },
    // Igual que el backtest: prendida con peso cero es apagada.
    { id: "l-nivel", param: "nivel", clave: "nivel_gbdt", nombre: "segunda opinión del nivel",
      vigente: (c) => !!c.nivel_gbdt && Number(c.nivel_gbdt_peso || 0) > 0 },
    { id: "l-nivel-tipo-dia", param: "nivel_por_tipo_de_dia", clave: "nivel_por_tipo_de_dia",
      nombre: "nivel por tipo de día", vigente: (c) => c.nivel_por_tipo_de_dia !== false },
    { id: "l-feriado-sabado", param: "feriado_como_sabado", clave: "feriado_como_sabado",
      nombre: "feriados como sábado", vigente: (c) => !!c.feriado_como_sabado },
    { id: "l-pers-eventos", param: "persistencia_saltea_eventos",
      clave: "persistencia_saltea_eventos", nombre: "no arrastrar los días atípicos",
      vigente: (c) => !!c.persistencia_saltea_eventos },
  ];
  // Números: el parámetro del backtest y la clave de la configuración son el mismo.
  const NUMEROS = [
    { id: "l-semanas", clave: "semanas_base", nombre: "semanas de historia" },
    { id: "l-dias-nivel", clave: "dias_nivel", nombre: "días de nivel" },
    { id: "l-deriva-dias", clave: "reparto_deriva_dias", nombre: "días de deriva" },
    { id: "l-deriva-tope", clave: "reparto_deriva_tope", nombre: "tope de la deriva" },
    { id: "l-tipo-dia-dias", clave: "reparto_tipo_dia_dias", nombre: "días del reparto por tipo de día" },
    { id: "l-tipo-dia-tope", clave: "reparto_tipo_dia_tope", nombre: "tope del reparto por tipo de día" },
    { id: "l-puente-factor", clave: "puente_factor", nombre: "factor del puente" },
    { id: "l-pers-hoy", clave: "persistencia_peso_hoy", nombre: "persistencia de hoy" },
    { id: "l-pers-resto", clave: "persistencia_peso_resto", nombre: "persistencia de los días siguientes" },
    { id: "l-pers-dias", clave: "persistencia_dias", nombre: "días de persistencia" },
    { id: "l-forma-dias", clave: "forma_dias", nombre: "días de la forma del día" },
  ];

  // Debajo de esto, dos errores se leen iguales: la diferencia es ruido de redondeo.
  const EMPATE = 0.002;

  let campanaMostrada = null;

  function fechaISO(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function fechasPorDefecto() {
    // Cuatro semanas terminando ayer, como Comparación. Para decidir, más.
    const ayer = new Date();
    ayer.setDate(ayer.getDate() - 1);
    const arranque = new Date(ayer);
    arranque.setDate(arranque.getDate() - 27);
    $("l-hasta").value = fechaISO(ayer);
    $("l-desde").value = fechaISO(arranque);
    $("l-hasta").max = fechaISO(new Date());
    $("l-desde").max = fechaISO(new Date());
  }

  function alRecibirConfig(ev) {
    const cfg = ev.detail || {};
    // Lo vigente adentro de cada casilla: así «como está» dice qué es.
    LLAVES.forEach((k) => {
      const sel = $(k.id);
      if (sel && sel.options.length) {
        sel.options[0].textContent = `Como está (${k.vigente(cfg) ? "sí" : "no"})`;
      }
    });
    NUMEROS.forEach((k) => {
      const inp = $(k.id);
      if (inp) inp.placeholder = cfg[k.clave] ?? "";
    });
    if (!$("l-desde").value) fechasPorDefecto();
    // Otra campaña: el resultado anterior no es de ésta.
    if (campanaMostrada !== null && campanaMostrada !== cfg.campana_id) {
      $("l-resultado").innerHTML = "";
      $("l-modelos").innerHTML = "";
    }
    campanaMostrada = cfg.campana_id;
  }

  // Lo que cambia contra lo vigente. Elegir el mismo valor que ya rige no es un
  // cambio: correrlo daría dos veces la misma comparación.
  function cambiosElegidos(cfg) {
    const cambios = [];
    LLAVES.forEach((k) => {
      const v = $(k.id).value;
      if (v === "") return;
      const valor = v === "true";
      if (valor === k.vigente(cfg)) return;
      cambios.push({ param: k.param, clave: k.clave, valor,
                     texto: `${k.nombre}: ${valor ? "sí" : "no"}` });
    });
    NUMEROS.forEach((k) => {
      const v = $(k.id).value;
      if (v === "" || Number(v) === Number(cfg[k.clave])) return;
      cambios.push({ param: k.clave, clave: k.clave, valor: Number(v),
                     texto: `${k.nombre}: ${cfg[k.clave] ?? "—"} → ${v}` });
    });
    return cambios;
  }

  async function medir() {
    const p = P();
    const cfg = p.estado.config || {};
    const btn = $("btn-lab-medir");
    if (!$("l-desde").value || !$("l-hasta").value) return;

    const cambios = cambiosElegidos(cfg);
    if (!cambios.length) {
      $("l-resultado").innerHTML = p.aviso(
        "Elegí al menos un valor distinto del vigente: con todo «como está» las dos " +
        "comparaciones darían lo mismo.", "info", "info-circle");
      return;
    }
    const base = new URLSearchParams({
      campana_id: p.estado.campana, desde: $("l-desde").value,
      hasta: $("l-hasta").value, antelacion: $("l-antelacion").value,
    });
    const variante = new URLSearchParams(base);
    cambios.forEach((c) => variante.set(c.param, String(c.valor)));

    const avisos = [];
    if (cambios.some((c) => c.clave === "nivel_gbdt" && c.valor)
        && !(Number(cfg.nivel_gbdt_peso || 0) > 0)) {
      avisos.push(p.aviso(
        "La segunda opinión tiene peso 0: prenderla no cambia nada. Cargale un peso en " +
        "«Modelo de pronóstico vigente» y volvé a medir.", "warning", "exclamation-triangle"));
    }

    await p.conBoton(btn, "Midiendo…", async () => {
      $("l-resultado").innerHTML = avisos.join("") +
        '<div class="small text-muted">Corriendo la comparación dos veces…</div>';
      $("l-modelos").innerHTML = "";
      try {
        const [antes, despues] = await Promise.all([
          p.pedirBacktest(base), p.pedirBacktest(variante)]);
        $("l-resultado").innerHTML = avisos.join("") + tablaComparada(antes, despues, cambios);
        $("l-modelos").innerHTML = modelosDelCambio(despues);
        const aplicar = $("btn-lab-aplicar");
        if (aplicar) aplicar.addEventListener("click", () => dejarVigente(aplicar, cambios));
      } catch (e) {
        $("l-resultado").innerHTML = p.aviso(`No se pudo medir: ${p.esc(e.message)}`,
                                             "danger", "x-octagon");
      }
    });
  }

  // Diferencia en puntos, coloreada por si mejora. `mejor`: "menor" para los
  // errores; "cero" para el sesgo, que mejora cuando se acerca a cero.
  function celdaDiferencia(a, b, mejor) {
    const p = P();
    if (a === null || a === undefined || b === null || b === undefined) {
      return '<td class="text-end text-muted">—</td>';
    }
    const dif = b - a;
    const ganancia = mejor === "cero" ? Math.abs(a) - Math.abs(b) : a - b;
    if (Math.abs(ganancia) < EMPATE) {
      return `<td class="text-end text-muted">igual</td>`;
    }
    const clase = ganancia > 0 ? "text-success-emphasis" : "text-danger-emphasis";
    const signo = dif > 0 ? "+" : "−";
    return `<td class="text-end ${clase}">${signo}${p.num(Math.abs(dif) * 100, 1)} pp</td>`;
  }

  function tablaComparada(antes, despues, cambios) {
    const p = P();
    const ra = antes.resumen || {};
    const rb = despues.resumen || {};
    const filas = [
      { nombre: "Error por media hora", nota: "WAPE, pondera por volumen",
        a: (ra.intervalo || {}).wape, b: (rb.intervalo || {}).wape, mejor: "menor", decide: true },
      { nombre: "Error del total del día", nota: "MAPE",
        a: (ra.diario || {}).mape, b: (rb.diario || {}).mape, mejor: "menor", decide: true },
      { nombre: "Sesgo del día", nota: "positivo = nos quedamos cortos",
        a: (ra.diario || {}).sesgo, b: (rb.diario || {}).sesgo, mejor: "cero" },
      { nombre: "Error de la curva intradía", nota: "sabiendo el total del día",
        a: (ra.forma || {}).wape, b: (rb.forma || {}).wape, mejor: "menor" },
    ];
    const tiposB = {};
    (despues.por_tipo_de_dia || []).forEach((t) => { tiposB[t.tipo] = t; });
    (antes.por_tipo_de_dia || []).forEach((t) => {
      const otro = tiposB[t.tipo] || {};
      filas.push({
        nombre: `Error del día — ${(p.tipoDia || {})[t.tipo] || t.tipo}`,
        nota: `${p.num(t.n)} días`,
        a: (t.modelo || {}).mape, b: (otro.modelo || {}).mape, mejor: "menor",
      });
    });

    const cuerpo = filas.map((f) => `
      <tr${f.decide ? ' class="fw-semibold"' : ""}>
        <td>${p.esc(f.nombre)} <small class="text-muted fw-normal">${p.esc(f.nota)}</small></td>
        <td class="text-end">${p.pct(f.a)}</td>
        <td class="text-end">${p.pct(f.b)}</td>
        ${celdaDiferencia(f.a, f.b, f.mejor)}
      </tr>`).join("");

    const [wape, mape] = filas;
    const mejora = (f) => (f.a ?? null) !== null && (f.b ?? null) !== null
      ? Math.sign(Math.abs(f.a - f.b) < EMPATE ? 0 : f.a - f.b) : 0;
    const veredicto = mejora(wape) + mejora(mape);
    const [clase, icono, texto] =
      mejora(wape) >= 0 && mejora(mape) >= 0 && veredicto > 0
        ? ["success", "check-circle", "Mejora sin empeorar ninguna de las dos medidas que deciden."]
      : mejora(wape) <= 0 && mejora(mape) <= 0 && veredicto < 0
        ? ["warning", "exclamation-triangle", "Empeora: conviene dejarlo como está."]
      : veredicto === 0 && mejora(wape) === 0
        ? ["light", "dash-circle", "No mueve el error: el cambio no pesa en esta ventana."]
        : ["info", "info-circle", "Mixto: mejora una medida y empeora la otra. Miralo por tipo de día antes de decidir."];

    const puede = p.puedeEditar;
    return `
      <div class="small text-muted mb-2">
        ${p.num(antes.dias_evaluados)} días cerrados del ${p.esc(String(antes.desde))} al
        ${p.esc(String(antes.hasta))}, antelación ${p.num(antes.antelacion)} ·
        cambio: <strong>${cambios.map((c) => p.esc(c.texto)).join(" · ")}</strong>
      </div>
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-2" style="max-width: 760px;">
          <thead class="table-light"><tr>
            <th></th><th class="text-end">Como está</th><th class="text-end">Con el cambio</th>
            <th class="text-end">Diferencia</th>
          </tr></thead>
          <tbody>${cuerpo}</tbody>
        </table>
      </div>
      <div class="alert alert-${clase} py-2 px-3 small d-flex align-items-center gap-2 flex-wrap">
        <i class="bi bi-${icono}"></i><div class="flex-grow-1">${texto}</div>
        ${puede ? `<button id="btn-lab-aplicar" class="btn btn-sm btn-outline-dark">
            <i class="bi bi-check2 me-1"></i>Dejar vigente</button>` : ""}
      </div>`;
  }

  function modelosDelCambio(despues) {
    const p = P();
    const partes = [p.tarjetaClima ? p.tarjetaClima(despues.clima) : "",
                    p.tarjetaNivel ? p.tarjetaNivel(despues.nivel) : ""].filter(Boolean);
    if (!partes.length) return "";
    return `<div class="small fw-semibold text-muted mb-1">Los modelos con el cambio</div>` +
      partes.map((h) => `<div class="mb-2">${h}</div>`).join("");
  }

  async function dejarVigente(btn, cambios) {
    const p = P();
    const cuerpo = {};
    cambios.forEach((c) => { cuerpo[c.clave] = c.valor; });
    await p.conBoton(btn, "Guardando…", async () => {
      try {
        await p.pedir(`config/campana?campana_id=${p.estado.campana}`,
                      { method: "PUT", body: JSON.stringify(cuerpo) });
        await p.recargar();
        btn.outerHTML = `<span class="text-success-emphasis small">
          <i class="bi bi-check2-circle me-1"></i>Vigente. Entra en el próximo recálculo
          (06:00 y 14:00) o con <strong>Recalcular</strong>.</span>`;
        // Lo que se dejó vigente ya es «como está».
        LLAVES.concat(NUMEROS).forEach((k) => { if ($(k.id)) $(k.id).value = ""; });
      } catch (e) {
        $("l-resultado").insertAdjacentHTML("beforeend", p.aviso(
          `No se pudo guardar: ${p.esc(e.message)}`, "danger", "x-octagon"));
      }
    });
  }

  document.addEventListener("planificador:config", alRecibirConfig);
  document.addEventListener("DOMContentLoaded", () => {
    if ($("btn-lab-medir")) $("btn-lab-medir").addEventListener("click", medir);
  });
})();
