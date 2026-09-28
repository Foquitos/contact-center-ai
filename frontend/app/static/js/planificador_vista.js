/* Planificador — vista del plan (semana de un vistazo).
 *
 * Grilla compacta de día (filas, hasta 30) × intervalo (columnas) para el pool
 * seleccionado, coloreada según la brecha de operadores (citados − a planificar).
 *
 * Las dos puntas se colorean: ROJOS cuando falta gente y AZULES cuando sobra.
 * Faltar deja llamadas sin atender y sobrar es hora paga que no hacía falta, así
 * que ninguna de las dos puede quedar en un gris que no se lee. La escala de
 * cortes es la misma para los dos lados, para que un azul fuerte y un rojo
 * fuerte signifiquen lo mismo.
 *
 * Se puede pintar en PERSONAS o en PORCENTAJE de los que hay que citar. Lo
 * segundo lo pidió Ignacio: una persona que falta a las 03, cuando hay que citar
 * 2, es media línea; una que falta a las 14, cuando hay que citar 55, no se
 * nota. El número de gente sigue estando en el título de cada celda.
 *
 * La barra de botones cambia sólo cómo se ve: la métrica (brecha o lo que falta
 * tras el refuerzo), el orden de los días, los números dentro de la celda, si se
 * muestra la madrugada y el CSV de lo que está en pantalla. Todo eso se recuerda
 * en localStorage, porque es preferencia de quien mira y no dato del plan.
 *
 * Al hacer clic en un día navega a ese día y abre el gráfico; al hacer clic en
 * una celda, además salta a esa media hora en la tabla de detalle.
 */
(function () {
  "use strict";

  // Preferencias de visualización (no son datos del plan: van al navegador).
  const CLAVE = "planificador.vista";
  const vista = {
    modo: "brecha",       // brecha | neto (lo que falta después del refuerzo)
    unidad: "personas",   // personas | porcentaje (de los que hay que citar)
    orden: "fecha",       // fecha | falta (peor día primero)
    numeros: false,       // el número dentro de cada celda
    madrugada: true,      // mostrar las horas de 00 a 08
  };

  function leerPreferencias() {
    try {
      const guardado = JSON.parse(window.localStorage.getItem(CLAVE) || "{}");
      Object.keys(vista).forEach((k) => {
        if (guardado[k] !== undefined) vista[k] = guardado[k];
      });
    } catch (e) { /* sin storage: quedan los valores por defecto */ }
  }

  function guardarPreferencias() {
    try { window.localStorage.setItem(CLAVE, JSON.stringify(vista)); } catch (e) { /* idem */ }
  }

  function inyectarEstilos() {
    if (document.getElementById("estilos-planificador-vista")) return;
    const style = document.createElement("style");
    style.id = "estilos-planificador-vista";
    style.textContent = `
      .semana-card {
        border-radius: 0.375rem;
      }
      .semana-grid-wrap {
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
      }
      .tabla-semana {
        border-collapse: separate;
        border-spacing: 2px;
        font-size: 11px;
        margin-bottom: 0;
        table-layout: fixed;
      }
      .tabla-semana th, .tabla-semana td {
        padding: 0;
        vertical-align: middle;
        line-height: 1;
      }
      .tabla-semana .th-dia {
        font-size: 11px;
        font-weight: 600;
        white-space: nowrap;
        padding-right: 8px;
        text-align: right;
        width: 76px;
        cursor: pointer;
        user-select: none;
      }
      .tabla-semana .th-dia:hover {
        color: #0d6efd;
      }
      .tabla-semana .dia-no-habil {
        font-style: italic;
        color: #64748b;
      }
      .tabla-semana .th-hora {
        font-size: 10px;
        color: #64748b;
        font-weight: 500;
        text-align: center;
        border-left: 1px solid #e2e8f0;
        height: 18px;
        user-select: none;
      }
      .tabla-semana .th-horas-falta, .tabla-semana .th-horas-sobra {
        font-size: 11px;
        font-weight: 600;
        color: #475569;
        text-align: right;
        white-space: nowrap;
        padding-left: 8px;
        padding-right: 4px;
        width: 58px;
        user-select: none;
      }
      .tabla-semana .fila-promedio td {
        border-top: 1px solid #cbd5e1;
        padding-top: 3px;
      }
      .tabla-semana .fila-promedio .th-dia {
        cursor: default;
        color: #475569;
      }
      .tabla-semana .fila-promedio .th-dia:hover {
        color: #475569;
      }
      /* La fila del promedio no lleva a ningún día: no se clickea. */
      .tabla-semana .fila-promedio .celda-semana {
        cursor: default;
      }
      .tabla-semana .fila-promedio .celda-semana:hover {
        transform: none;
        outline: none;
      }
      .tabla-semana .celda-semana {
        width: 12px;
        min-width: 12px;
        max-width: 12px;
        height: 15px;
        border-radius: 2px;
        cursor: pointer;
        transition: transform 0.1s, outline 0.1s;
      }
      .tabla-semana .celda-semana:hover {
        transform: scale(1.3);
        z-index: 10;
        outline: 1px solid #0f172a;
      }
      /* Con números la celda tiene que dar el ancho del texto: la grilla se
         agranda y aparece el scroll horizontal, que es mejor que un número
         cortado. */
      .tabla-semana.tabla-semana-num .celda-semana {
        width: 24px;
        min-width: 24px;
        max-width: 24px;
        height: 16px;
        font-size: 9px;
        font-weight: 600;
        text-align: center;
        overflow: hidden;
        color: #0f172a;
      }
      .tabla-semana.tabla-semana-num .celda-semana:hover {
        transform: none;
      }
      .tabla-semana .celda-sin-malla {
        background: repeating-linear-gradient(45deg, #f1f3f5, #f1f3f5 2px, #dee2e6 2px, #dee2e6 4px);
      }
      /* Justo: ni falta ni sobra. */
      .tabla-semana .celda-justo {
        background-color: #eef2f6;
      }
      /* Faltaba y lo tapa el refuerzo: no es lo mismo que sobrar, y en el color
         del refuerzo para que se lea junto con el resto de la pantalla. */
      .tabla-semana .celda-cubierto {
        background-color: #cdeae5;
      }
      /* Cinco tonos por lado. Qué cantidad le toca a cada tono lo decide la
         escala elegida (personas o porcentaje), no la CSS. */
      .tabla-semana .celda-falta-n1 { background-color: #fecaca; }
      .tabla-semana .celda-falta-n2 { background-color: #f87171; }
      .tabla-semana .celda-falta-n3 { background-color: #dc2626; }
      .tabla-semana .celda-falta-n4 { background-color: #b91c1c; }
      .tabla-semana .celda-falta-n5 { background-color: #7f1d1d; }
      .tabla-semana .celda-sobra-n1 { background-color: #dbeafe; }
      .tabla-semana .celda-sobra-n2 { background-color: #93c5fd; }
      .tabla-semana .celda-sobra-n3 { background-color: #60a5fa; }
      .tabla-semana .celda-sobra-n4 { background-color: #2563eb; }
      .tabla-semana .celda-sobra-n5 { background-color: #1e3a8a; }
      .tabla-semana.tabla-semana-num .celda-falta-n3,
      .tabla-semana.tabla-semana-num .celda-falta-n4,
      .tabla-semana.tabla-semana-num .celda-falta-n5,
      .tabla-semana.tabla-semana-num .celda-sobra-n4,
      .tabla-semana.tabla-semana-num .celda-sobra-n5 {
        color: #fff;
      }
      /* El porcentaje llega a tres cifras (el 12/10 sobra +767%): sin este ancho
         el número queda cortado. */
      .tabla-semana.tabla-semana-num.tabla-semana-pct .celda-semana {
        width: 30px;
        min-width: 30px;
        max-width: 30px;
      }
      .leyenda-semana {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
        font-size: 11px;
        color: #475569;
      }
      .leyenda-semana-item {
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }
      .leyenda-muestra {
        width: 12px;
        height: 12px;
        border-radius: 2px;
        display: inline-block;
      }
      /* El salto desde una celda cae en una fila de 48: sin marcarla hay que
         buscarla a ojo. */
      #tabla-intervalos tbody tr.fila-resaltada > td {
        background-color: #fff3cd !important;
      }
    `;
    document.head.appendChild(style);
  }

  const NOMBRES_DIAS = ["Do", "Lu", "Ma", "Mi", "Ju", "Vi", "Sá"];

  function etiquetaDeDia(diaIso) {
    const [y, m, d] = diaIso.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    const diaSem = NOMBRES_DIAS[dt.getDay()] || "";
    return `${diaSem} ${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}`;
  }

  // Sábado, domingo o feriado. El tipo de día sale de la misma función que usa el
  // resto de la pantalla (los feriados son los cargados en la campaña), así que
  // acá no hay una segunda regla que pueda quedar desfasada.
  function esNoHabil(diaIso) {
    const P = window.Planificador;
    if (!P || typeof P.tipoDeDiaPlan !== "function") return false;
    return P.tipoDeDiaPlan(diaIso) !== "habil";
  }

  function esFeriadoEnSemana(diaIso) {
    if (!esNoHabil(diaIso)) return false;
    const [y, m, d] = diaIso.split("-").map(Number);
    const w = new Date(y, m - 1, d).getDay();
    return w !== 0 && w !== 6;
  }

  /* Dos escalas, cinco tonos cada una y los mismos cortes para los dos lados: un
     azul y un rojo del mismo tono son la misma cosa.

     En PERSONAS llega hasta 20 porque el sobrante del mediodía de Voltara anda en
     17-30 y con un tramo ">10" todo eso quedaba pintado igual.

     En PORCENTAJE el faltante va sobre la gente a citar de ese intervalo, que es
     lo que pidió Ignacio: uno que falta a las 03, cuando hay que citar 2, es la
     mitad de la línea; uno que falta a las 14, cuando hay que citar 55, no se
     nota. Sobre la corrida vigente, en personas el 85% de los intervalos con
     faltante (392 de 463) cae en el tramo más flojo (1-2) y no se distingue nada;
     en porcentaje 257 pasan del 50%, y son casi todos de madrugada. */
  const ESCALAS = {
    personas: {
      etiqueta: "personas",
      cortes: [2, 5, 10, 20],
      leyenda: ["1–2", "3–5", "6–10", "11–20", "&gt;20"],
      valor: (c, que) => c[que],
      texto: (c, que) => `${que === "falta" ? "−" : "+"}${c[que]}`,
    },
    porcentaje: {
      etiqueta: "% de los que hay que citar",
      cortes: [0.05, 0.10, 0.25, 0.50],
      leyenda: ["≤5%", "5–10%", "10–25%", "25–50%", "&gt;50%"],
      valor: (c, que) => c[que === "falta" ? "faltaRel" : "sobraRel"],
      texto: (c, que) => {
        const v = Math.round(c[que === "falta" ? "faltaRel" : "sobraRel"] * 100);
        return `${que === "falta" ? "−" : "+"}${Math.min(v, 999)}`;
      },
    },
  };

  const escala = () => ESCALAS[vista.unidad] || ESCALAS.personas;

  function nivel(valor) {
    const cortes = escala().cortes;
    for (let i = 0; i < cortes.length; i++) if (valor <= cortes[i]) return i + 1;
    return cortes.length + 1;
  }

  // La leyenda sale de la misma escala que pinta las celdas: si mañana se corre
  // un corte, no puede quedar una referencia mintiendo.
  function escalaLeyenda(que) {
    return escala().leyenda.map((texto, i) =>
      `<span class="leyenda-semana-item"><span class="leyenda-muestra celda-${que}-n${i + 1}"></span> ${texto}</span>`
    ).join("");
  }

  const gente = (n, singular, plural) =>
    (n === 1 ? `${singular} 1 operador` : `${plural} ${n} operadores`);

  // Sin decimales: es gente, no una medición fina.
  const pct = (v) => window.Planificador.pct(v, 0);

  // Qué color y qué número le toca a la celda con la unidad elegida. Los
  // faltantes y sobrantes en personas se calculan una sola vez (los totales del
  // día dependen de ellos); acá sólo se traducen a la escala activa.
  function pinta(c) {
    if (c.sinMalla) return { clase: "celda-sin-malla", texto: "" };
    const e = escala();
    if (c.falta > 0) return { clase: `celda-falta-n${nivel(e.valor(c, "falta"))}`, texto: e.texto(c, "falta") };
    if (c.cubierto) return { clase: "celda-cubierto", texto: "0" };
    if (c.sobra > 0) return { clase: `celda-sobra-n${nivel(e.valor(c, "sobra"))}`, texto: e.texto(c, "sobra") };
    return { clase: "celda-justo", texto: "0" };
  }

  function botonToggle(id, contenido, activo, titulo) {
    const P = window.Planificador;
    return `<button type="button" id="${id}" class="btn btn-outline-secondary${activo ? " active" : ""}"
             aria-pressed="${activo ? "true" : "false"}" title="${P.esc(titulo)}">${contenido}</button>`;
  }

  /* Una celda de la grilla, ya resuelta: cuánto falta, cuánto sobra, con qué
     color y qué dice al pasar el mouse. Se calcula antes de dibujar porque los
     totales del día y el orden de las filas dependen de esto. */
  function resolverCelda(r, ctx, etiquetaDia, hStr) {
    if (!r || r.OperadoresPlanificados === null || r.OperadoresPlanificados === undefined) {
      return {
        sinMalla: true, falta: 0, sobra: 0, faltaRel: 0, sobraRel: 0,
        titulo: `${etiquetaDia} ${hStr}: Sin malla de turnos cargada`, r: r || null,
      };
    }

    const REF = ctx.REF;
    const brecha = r.Brecha;
    const aCitar = r.OperadoresPlanificar;
    const neto = vista.modo === "neto" && ctx.hayRefuerzo;
    let falta = 0;
    let sobra = 0;
    let cubierto = false;
    let titulo;

    // Contra la gente que hay que citar en ese intervalo: es lo que hace
    // comparable un faltante de las 03 con uno de las 14. Sin gente a citar no
    // hay proporción posible (no pasa en los datos de hoy), y ahí queda en 0 para
    // no inventar un 100%.
    const proporcion = (n) => (aCitar > 0 ? n / aCitar : 0);
    const conPct = (n) => (aCitar === 1
      ? `${pct(proporcion(n))} del único a citar`
      : `${pct(proporcion(n))} de los ${aCitar} a citar`);

    if (brecha >= 0) {
      sobra = brecha;
      titulo = sobra > 0
        ? `${etiquetaDia} ${hStr}: ${gente(sobra, "Sobra", "Sobran")}, ${conPct(sobra)} (citados ${r.OperadoresPlanificados})`
        : `${etiquetaDia} ${hStr}: Justo: ${aCitar} a citar y ${r.OperadoresPlanificados} citados`;
    } else if (neto) {
      const faltaNeto = (r.FaltanteNeto !== null && r.FaltanteNeto !== undefined)
        ? r.FaltanteNeto
        : -brecha;
      falta = faltaNeto > 0 ? faltaNeto : 0;
      cubierto = falta === 0;
      titulo = falta > 0
        ? `${etiquetaDia} ${hStr}: ${falta === 1 ? "Falta 1" : `Faltan ${falta}`} tras ${REF}, ${conPct(falta)} (brecha ${brecha}, ${REF} cubre ${r.RefuerzoCubre || 0} de ${r.RefuerzoDisponible} disponibles)`
        : `${etiquetaDia} ${hStr}: Brecha ${brecha} cubierta por ${REF} (${r.RefuerzoCubre} de ${r.RefuerzoDisponible} disponibles)`;
    } else {
      falta = -brecha;
      titulo = `${etiquetaDia} ${hStr}: ${gente(falta, "Falta", "Faltan")}, ${conPct(falta)} (citados ${r.OperadoresPlanificados})`;
    }

    // De madrugada el plan puede estar redondeando para abajo: conviene saberlo
    // antes de leer una celda de esa franja como si sobrara gente.
    if (r.Redondeo === "abajo") titulo += " · a citar redondeado para abajo";

    return {
      sinMalla: false, falta, sobra, cubierto, titulo, r, aCitar,
      faltaRel: proporcion(falta), sobraRel: proporcion(sobra),
    };
  }

  function renderSemana() {
    inyectarStylesSeguro();

    const slot = document.getElementById("slot-plan-semana");
    if (!slot) return;

    const P = window.Planificador;
    if (!P || !P.estado || !P.estado.requerimiento || !P.estado.requerimiento.length) {
      slot.hidden = true;
      slot.innerHTML = "";
      return;
    }

    const fPool = document.getElementById("f-pool");
    const poolId = fPool ? Number(fPool.value) : (P.estado.config?.pools?.[0]?.pool_id ?? null);
    if (poolId === null || isNaN(poolId)) {
      slot.hidden = true;
      slot.innerHTML = "";
      return;
    }

    const filasPool = P.estado.requerimiento.filter((r) => r.PoolID === poolId);
    if (!filasPool.length) {
      slot.hidden = true;
      slot.innerHTML = "";
      return;
    }

    // Hay corrida con requerimiento: mostramos el contenedor
    slot.hidden = false;

    // Detección de refuerzo configurado para este pool
    const hayRefuerzo = filasPool.some((r) => r.RefuerzoDisponible !== null && r.RefuerzoDisponible !== undefined);
    // El nombre del refuerzo sale de la configuración de la campaña.
    const REF = P.refuerzo ? P.refuerzo() : "Refuerzo";
    if (!hayRefuerzo && vista.modo === "neto") vista.modo = "brecha";

    const intervaloMin = Number(P.estado.config?.intervalo_min) || 30;
    const intervalosPorHora = Math.round(60 / intervaloMin);
    const intervalosPorBloque = intervalosPorHora * 2; // Bloques de 2 horas
    const horaDesde = vista.madrugada ? 0 : 8;

    // Lista de horas visibles del día
    const horasDia = [];
    for (let h = horaDesde; h < 24; h++) {
      for (let m = 0; m < 60; m += intervaloMin) {
        horasDia.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
      }
    }

    // Días únicos (hasta 30)
    const dias = [...new Set(filasPool.map((r) => String(r.Intervalo).slice(0, 10)))].sort().slice(0, 30);

    // Mapeo (dia -> hora -> fila)
    const mapaDatos = new Map();
    filasPool.forEach((r) => {
      const dStr = String(r.Intervalo).slice(0, 10);
      const hStr = P.hora(r.Intervalo);
      if (!mapaDatos.has(dStr)) mapaDatos.set(dStr, new Map());
      mapaDatos.get(dStr).set(hStr, r);
    });

    // Todas las celdas resueltas antes de dibujar: los totales por día, el orden
    // de las filas y el promedio por media hora salen de acá.
    const ctx = { hayRefuerzo, REF };
    const porDia = dias.map((diaIso) => {
      const etiquetaDia = etiquetaDeDia(diaIso);
      const horasDelDia = mapaDatos.get(diaIso) || new Map();
      const celdas = horasDia.map((hStr) => resolverCelda(horasDelDia.get(hStr), ctx, etiquetaDia, hStr));
      return {
        diaIso, etiquetaDia, celdas,
        hayMalla: celdas.some((c) => !c.sinMalla),
        falta: celdas.reduce((a, c) => a + c.falta, 0),
        sobra: celdas.reduce((a, c) => a + c.sobra, 0),
        // El denominador del porcentaje del día: la gente que hay que citar.
        aCitar: celdas.reduce((a, c) => a + (c.aCitar || 0), 0),
      };
    });

    const aHoras = (intervalos) => intervalos * (intervaloMin / 60);
    const totalFalta = porDia.reduce((a, d) => a + d.falta, 0);
    const totalSobra = porDia.reduce((a, d) => a + d.sobra, 0);
    const totalACitar = porDia.reduce((a, d) => a + d.aCitar, 0);

    /* Las dos columnas de la derecha. En personas van las horas-operador y en
       porcentaje la parte de las horas a citar; el título trae siempre las dos,
       así cambiar de unidad no esconde nada. */
    const columnasTotales = (falta, sobra, aCitar, hayMalla) => {
      const cel = (que, n) => {
        const clase = que === "falta" ? "th-horas-falta" : "th-horas-sobra";
        if (!hayMalla) return `<td class="${clase} text-muted">—</td>`;
        const color = n > 0 ? (que === "falta" ? "text-danger" : "text-primary") : "text-muted";
        const rel = aCitar > 0 ? n / aCitar : 0;
        const texto = vista.unidad === "porcentaje" ? pct(rel) : `${P.num(aHoras(n), 0)} h`;
        const titulo = `${P.num(aHoras(n), 0)} horas-operador que ${que === "falta" ? "faltan" : "sobran"}`
          + `, ${pct(rel)} de las ${P.num(aHoras(aCitar), 0)} h a citar`;
        return `<td class="${clase} ${color}" title="${P.esc(titulo)}">${texto}</td>`;
      };
      return cel("falta", falta) + cel("sobra", sobra);
    };
    const peorDia = porDia.reduce((peor, d) => (d.falta > (peor ? peor.falta : 0) ? d : peor), null);

    // El orden por fecha es el que deja ver la semana; el orden por faltante es
    // para ir a trabajar sobre los días que hay que arreglar.
    const filasOrdenadas = vista.orden === "falta"
      ? porDia.slice().sort((a, b) => (b.falta - a.falta) || a.diaIso.localeCompare(b.diaIso))
      : porDia;

    // ------------------------------------------------------- barra de botones
    const grupoModo = hayRefuerzo
      ? `<div class="btn-group btn-group-sm" role="group" aria-label="Métrica">
           ${botonToggle("vista-modo-brecha", "Brecha", vista.modo === "brecha",
              "Citados contra a planificar, sin contar el refuerzo")}
           ${botonToggle("vista-modo-neto", `Tras ${P.esc(REF)}`, vista.modo === "neto",
              `Lo que sigue faltando después de pasar gente de ${REF} a la línea`)}
         </div>`
      : "";

    const barra = `
      <div class="d-flex align-items-center gap-2 flex-wrap">
        ${grupoModo}
        <div class="btn-group btn-group-sm" role="group" aria-label="Unidad">
          ${botonToggle("vista-unidad-personas", '<i class="bi bi-people"></i>', vista.unidad === "personas",
             "Pintar por cantidad de personas")}
          ${botonToggle("vista-unidad-porcentaje", '<i class="bi bi-percent"></i>', vista.unidad === "porcentaje",
             "Pintar por porcentaje de los que hay que citar: uno que falta a las 03 (de 2) pesa mucho más que uno a las 14 (de 55)")}
        </div>
        <div class="btn-group btn-group-sm" role="group" aria-label="Orden">
          ${botonToggle("vista-orden-fecha", '<i class="bi bi-calendar3"></i>', vista.orden === "fecha",
             "Ordenar los días por fecha")}
          ${botonToggle("vista-orden-falta", '<i class="bi bi-sort-down"></i>', vista.orden === "falta",
             "Ordenar por lo que falta: el peor día arriba")}
        </div>
        <div class="btn-group btn-group-sm" role="group" aria-label="Detalle">
          ${botonToggle("vista-btn-numeros", '<i class="bi bi-123"></i>', vista.numeros,
             vista.unidad === "porcentaje"
               ? "Mostrar el porcentaje en cada celda"
               : "Mostrar el número de operadores en cada celda")}
          ${botonToggle("vista-btn-madrugada", '<i class="bi bi-moon"></i>', vista.madrugada,
             "Mostrar las horas de 00 a 08")}
        </div>
        <button type="button" id="vista-btn-peor" class="btn btn-sm btn-outline-danger"
                title="Abrir el día con más horas-operador faltantes"
                ${peorDia && peorDia.falta > 0 ? "" : "disabled"}>
          <i class="bi bi-exclamation-triangle me-1"></i>Peor día
        </button>
        <button type="button" id="vista-btn-csv" class="btn btn-sm btn-outline-success"
                title="Descargar en CSV lo que está en pantalla, intervalo por intervalo">
          <i class="bi bi-filetype-csv"></i>
        </button>
      </div>`;

    // ------------------------------------------------------------ encabezados
    let theadHoras = `<th class="th-dia"></th>`;
    for (let h = horaDesde; h < 24; h += 2) {
      theadHoras += `<th class="th-hora" colspan="${intervalosPorBloque}">${String(h).padStart(2, "0")}h</th>`;
    }
    const tituloColumnas = (que) => `${que}, en ${vista.unidad === "porcentaje"
        ? "porcentaje de las horas-operador a citar" : "horas-operador"}`
      + (vista.madrugada ? " del día." : " de 08 a 24 (la madrugada está oculta).")
      + " Al pasar el mouse, las dos unidades.";
    theadHoras += `<th class="th-horas-falta" title="${P.esc(tituloColumnas("Lo que falta"))}">Falta</th>`;
    theadHoras += `<th class="th-horas-sobra" title="${P.esc(tituloColumnas("Lo que sobra"))}">Sobra</th>`;

    // ------------------------------------------------------------- filas
    let tbodyHtml = "";
    filasOrdenadas.forEach((d) => {
      const celdasDia = d.celdas.map((c, i) => {
        const hStr = horasDia[i];
        const dataHora = c.sinMalla ? "" : ` data-hora="${hStr}"`;
        const p = pinta(c);
        return `<td class="celda-semana ${p.clase}" data-dia="${d.diaIso}"${dataHora}
                    title="${P.esc(c.titulo)}">${vista.numeros ? p.texto : ""}</td>`;
      }).join("");

      const claseDia = esNoHabil(d.diaIso) ? "th-dia dia-no-habil" : "th-dia";
      const tituloDia = `Ver el día ${d.diaIso}`
        + (esFeriadoEnSemana(d.diaIso) ? " (feriado)" : (esNoHabil(d.diaIso) ? " (no hábil)" : ""));

      tbodyHtml += `
        <tr>
          <td class="${claseDia}" data-dia="${d.diaIso}" title="${P.esc(tituloDia)}">${d.etiquetaDia}</td>
          ${celdasDia}
          ${columnasTotales(d.falta, d.sobra, d.aCitar, d.hayMalla)}
        </tr>`;
    });

    // Fila del día típico: el promedio por media hora sobre los días con malla.
    // Es lo que contesta "¿a qué hora falta siempre?", que en la grilla día por
    // día hay que reconstruir a ojo.
    const diasConMalla = porDia.filter((d) => d.hayMalla);
    const totalesHtml = columnasTotales(totalFalta, totalSobra, totalACitar, true);
    let filaPromedio = `
      <tr class="fila-promedio">
        <td class="th-dia">Total</td>
        <td colspan="${horasDia.length}"></td>
        ${totalesHtml}
      </tr>`;
    if (diasConMalla.length > 1) {
      const celdas = horasDia.map((hStr, i) => {
        const conDato = diasConMalla.map((d) => d.celdas[i]).filter((c) => !c.sinMalla);
        if (!conDato.length) {
          return `<td class="celda-semana celda-sin-malla" title="${P.esc(`${hStr}: sin malla en ningún día`)}"></td>`;
        }
        const sumaFalta = conDato.reduce((a, c) => a + c.falta, 0);
        const sumaSobra = conDato.reduce((a, c) => a + c.sobra, 0);
        const sumaACitar = conDato.reduce((a, c) => a + (c.aCitar || 0), 0);
        const falta = sumaFalta / conDato.length;
        const sobra = sumaSobra / conDato.length;
        // El porcentaje del promedio se pondera (suma sobre suma) y no es el
        // promedio de porcentajes: si no, un día flojo con poca gente a citar
        // pesaría igual que un lunes entero.
        const relFalta = sumaACitar > 0 ? sumaFalta / sumaACitar : 0;
        const relSobra = sumaACitar > 0 ? sumaSobra / sumaACitar : 0;
        const esPct = vista.unidad === "porcentaje";
        const umbral = esPct ? 0.005 : 0.5;
        const valFalta = esPct ? relFalta : falta;
        const valSobra = esPct ? relSobra : sobra;
        let clase = "celda-justo";
        let texto = "0";
        if (valFalta >= umbral) {
          clase = `celda-falta-n${nivel(valFalta)}`;
          texto = esPct ? `−${Math.min(Math.round(relFalta * 100), 999)}` : `−${P.num(falta, 0)}`;
        } else if (valSobra >= umbral) {
          clase = `celda-sobra-n${nivel(valSobra)}`;
          texto = esPct ? `+${Math.min(Math.round(relSobra * 100), 999)}` : `+${P.num(sobra, 0)}`;
        }
        const titulo = `${hStr}, promedio de ${conDato.length} día(s): faltan ${P.num(falta, 1)} (${pct(relFalta)}), `
          + `sobran ${P.num(sobra, 1)} (${pct(relSobra)}) sobre ${P.num(sumaACitar / conDato.length, 1)} a citar`;
        return `<td class="celda-semana ${clase}" title="${P.esc(titulo)}">${vista.numeros ? texto : ""}</td>`;
      }).join("");

      filaPromedio = `
        <tr class="fila-promedio">
          <td class="th-dia" title="${P.esc(`Promedio por media hora sobre los ${diasConMalla.length} días con malla cargada`)}">Promedio</td>
          ${celdas}
          ${totalesHtml}
        </tr>`;
    }

    const notaModo = (vista.modo === "neto"
      ? `Lo que falta después de pasar gente de ${P.esc(REF)}`
      : "Brecha entre dotación citada y necesaria")
      + (vista.unidad === "porcentaje" ? ", en % de los que hay que citar." : ", en personas.");

    slot.innerHTML = `
      <div class="card shadow-sm semana-card mb-3">
        <div class="card-header bg-white d-flex justify-content-between align-items-center flex-wrap gap-2 py-2">
          <div class="d-flex align-items-center gap-2 flex-wrap">
            <span class="fw-semibold">La semana de un vistazo</span>
            <small class="text-muted">${notaModo} Clic en un día abre el detalle; clic en una celda salta a esa media hora.</small>
          </div>
          ${barra}
        </div>
        <div class="card-body p-2">
          <div class="semana-grid-wrap mb-2">
            <table class="tabla-semana${vista.numeros ? " tabla-semana-num" : ""}${vista.unidad === "porcentaje" ? " tabla-semana-pct" : ""}">
              <thead><tr>${theadHoras}</tr></thead>
              <tbody id="semana-tbody">${tbodyHtml}</tbody>
              <tfoot>${filaPromedio}</tfoot>
            </table>
          </div>
          <div class="leyenda-semana border-top pt-2 mt-1">
            <span class="me-1">Cortes en <span class="fw-semibold">${escala().etiqueta}</span>.</span>
            <span class="fw-semibold me-1">Faltan:</span>
            ${escalaLeyenda("falta")}
            <span class="fw-semibold ms-2 me-1">Sobran:</span>
            ${escalaLeyenda("sobra")}
            <span class="leyenda-semana-item ms-2"><span class="leyenda-muestra celda-justo"></span> justo</span>
            ${vista.modo === "neto"
              ? `<span class="leyenda-semana-item"><span class="leyenda-muestra celda-cubierto"></span> lo cubre ${P.esc(REF)}</span>`
              : ""}
            <span class="leyenda-semana-item"><span class="leyenda-muestra celda-sin-malla"></span> sin malla cargada</span>
          </div>
        </div>
      </div>
    `;

    conectarBarra({ porDia: filasOrdenadas, horasDia, peorDia, poolId, poolNombre: filasPool[0].Pool, REF });
  }

  function conectarBarra(datos) {
    const clic = (id, fn) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("click", fn);
    };
    const cambiar = (campo, valor) => {
      if (vista[campo] === valor) return;
      vista[campo] = valor;
      guardarPreferencias();
      renderSemana();
    };

    clic("vista-modo-brecha", () => cambiar("modo", "brecha"));
    clic("vista-modo-neto", () => cambiar("modo", "neto"));
    clic("vista-unidad-personas", () => cambiar("unidad", "personas"));
    clic("vista-unidad-porcentaje", () => cambiar("unidad", "porcentaje"));
    clic("vista-orden-fecha", () => cambiar("orden", "fecha"));
    clic("vista-orden-falta", () => cambiar("orden", "falta"));
    clic("vista-btn-numeros", () => cambiar("numeros", !vista.numeros));
    clic("vista-btn-madrugada", () => cambiar("madrugada", !vista.madrugada));
    clic("vista-btn-peor", () => {
      if (datos.peorDia) irAlDia(datos.peorDia.diaIso, null);
    });
    clic("vista-btn-csv", () => descargarCSV(datos));

    // Delegación de eventos para clics en celdas o etiquetas de día
    const tbody = document.getElementById("semana-tbody");
    if (tbody) {
      tbody.addEventListener("click", (ev) => {
        const target = ev.target.closest("[data-dia]");
        if (!target || !target.dataset.dia) return;
        irAlDia(target.dataset.dia, target.dataset.hora || null);
      });
    }
  }

  /* Ir al día (y, si se pidió, a la media hora) en el resto de la pantalla. Desde
     un día se abre el gráfico, que es donde se lee la forma; desde una celda se
     salta a su fila del detalle, porque si no hay que buscarla entre 48. */
  function irAlDia(diaIso, hStr) {
    const P = window.Planificador;
    const fDia = document.getElementById("f-dia");
    if (fDia && Array.from(fDia.options).some((o) => o.value === diaIso) && fDia.value !== diaIso) {
      fDia.value = diaIso;
      if (typeof P.redibujar === "function") P.redibujar();
    }

    if (!hStr) {
      const chartLlamadas = document.getElementById("chart-llamadas");
      if (chartLlamadas) chartLlamadas.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    const tabla = document.getElementById("tabla-intervalos");
    const fila = tabla && Array.from(tabla.querySelectorAll("tbody tr"))
      .find((tr) => tr.cells.length && tr.cells[0].textContent.trim() === hStr);
    if (!fila) {
      const chartLlamadas = document.getElementById("chart-llamadas");
      if (chartLlamadas) chartLlamadas.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    tabla.querySelectorAll("tr.fila-resaltada").forEach((tr) => tr.classList.remove("fila-resaltada"));
    fila.classList.add("fila-resaltada");
    fila.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => fila.classList.remove("fila-resaltada"), 4000);
  }

  /* CSV de lo que está en pantalla: mismos días, mismo orden, mismas medias horas
     y la métrica elegida. Se arma en el navegador porque el requerimiento ya está
     entero en memoria; el Excel del plan, que trae además pronóstico y
     parámetros, sigue saliendo del backend. Separador coma y BOM para que Excel
     lo abra con acentos. */
  function descargarCSV(datos) {
    const celda = (v) => {
      const txt = String(v ?? "");
      return /[",;\n]/.test(txt) ? `"${txt.replace(/"/g, '""')}"` : txt;
    };
    const encabezados = ["dia", "hora", "no_habil", "en_linea", "a_planificar", "citados",
                         "brecha", "faltan", "faltan_pct", "sobran", "sobran_pct",
                         "refuerzo_disponible", "faltante_neto", "redondeo"];
    const filas = [];
    const comoPct = (v) => (v * 100).toFixed(1);
    datos.porDia.forEach((d) => {
      d.celdas.forEach((c, i) => {
        if (c.sinMalla) return;
        const r = c.r;
        filas.push([
          d.diaIso, datos.horasDia[i], esNoHabil(d.diaIso) ? 1 : 0,
          r.OperadoresLinea, r.OperadoresPlanificar, r.OperadoresPlanificados,
          r.Brecha, c.falta, comoPct(c.faltaRel), c.sobra, comoPct(c.sobraRel),
          r.RefuerzoDisponible ?? "", r.FaltanteNeto ?? "", r.Redondeo || "arriba",
        ]);
      });
    });

    const csv = [encabezados, ...filas].map((f) => f.map(celda).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `planificador_semana_${String(datos.poolNombre || datos.poolId).replace(/\W+/g, "_")}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function inyectarStylesSeguro() {
    if (document.head) {
      inyectarEstilos();
    } else {
      document.addEventListener("DOMContentLoaded", inyectarEstilos, { once: true });
    }
  }

  // Escuchar evento planificador:plan
  document.addEventListener("planificador:plan", () => {
    renderSemana();
  });

  // Conectar listener de cambio de pool
  function conectarPool() {
    const fPool = document.getElementById("f-pool");
    if (fPool) {
      fPool.addEventListener("change", () => {
        renderSemana();
      });
    }
  }

  leerPreferencias();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      inyectarEstilos();
      conectarPool();
    });
  } else {
    inyectarEstilos();
    conectarPool();
  }
})();
