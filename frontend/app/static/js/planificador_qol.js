/* Planificador — comodidades de la pantalla que no son del plan.
 *
 * Todo lo de acá es genérico sobre el DOM que arman los otros módulos, y por eso
 * vive aparte: no calcula nada ni pide nada al backend.
 *
 *  - La pestaña abierta va en la URL (#comparacion): sobrevive a recargar y se
 *    puede pasar un link directo.
 *  - Tablas ordenables con clic en el encabezado (asc → desc → como venía). El
 *    orden se mantiene cuando la tabla se vuelve a pintar.
 *  - «Copiar» en las tablas de datos: HTML + texto con tabulaciones, así pega
 *    bien en Excel y en un mail. Sólo las columnas visibles.
 *  - Cambios sin guardar en Configuración / Laboratorio: se marcan, y se avisa
 *    antes de cambiar de campaña, volver a leer o cerrar la página.
 *  - Atajos rápidos de período en Comparación y Laboratorio.
 *  - Atajos de teclado (← → día, 1…6 pestaña, R volver a leer, ? ayuda).
 *  - Índice fijo arriba de cada pestaña con sus bloques.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const P = () => window.Planificador || {};

  // ------------------------------------------------------ pestaña en la URL

  const PESTANAS = () => [...document.querySelectorAll('.viz-root .nav-tabs [data-bs-toggle="tab"]')];
  const nombrePestana = (btn) => btn.dataset.bsTarget.replace(/^#tab-/, "");

  function mostrarPestana(nombre) {
    const btn = PESTANAS().find((b) => nombrePestana(b) === nombre);
    if (btn && window.bootstrap) window.bootstrap.Tab.getOrCreateInstance(btn).show();
    return !!btn;
  }

  function iniciarPestanas() {
    const inicial = window.location.hash.replace(/^#/, "");
    if (inicial) mostrarPestana(inicial);
    PESTANAS().forEach((btn) => btn.addEventListener("shown.bs.tab", () => {
      const url = new URL(window.location.href);
      url.hash = nombrePestana(btn) === "plan" ? "" : nombrePestana(btn);
      // replaceState y no pushState: el «atrás» del navegador sale de la página,
      // no recorre cada pestaña que se miró.
      window.history.replaceState(null, "", url);
      programarIndice();
    }));
    window.addEventListener("hashchange", () => {
      mostrarPestana(window.location.hash.replace(/^#/, "") || "plan");
    });
  }

  // ------------------------------------------------------- tablas ordenables

  // Qué tablas se copian: las de datos, no las de configuración.
  const COPIABLES = ["tabla-intervalos", "tabla-refuerzos", "tabla-eventos", "tabla-ajustes",
    "tabla-avisos-corte", "tabla-backtest", "tabla-tipo-dia", "tabla-dotacion",
    "tabla-dotacion-intervalos", "tabla-escenario-dias", "tabla-puestos", "tabla-codigos"];
  const ordenes = {};   // id de tabla → { col, dir }

  const tablasCandidatas = () => [...document.querySelectorAll(".viz-root table.table[id]")];

  // El texto de la celda, pasado a algo comparable. Los números salen en dos
  // formatos en la pantalla (toFixed con punto y toLocaleString con puntos de
  // miles), así que se desarma a mano.
  function valorCelda(td) {
    if (!td) return { vacio: true };
    if (td.dataset.orden !== undefined) {
      const n = Number(td.dataset.orden);
      return Number.isFinite(n) ? { n } : { t: td.dataset.orden };
    }
    const txt = td.textContent.replace(/\s+/g, " ").trim();
    if (!txt || txt === "—" || txt === "-") return { vacio: true };
    let m = txt.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return { n: Number(m[1] + m[2] + m[3]) };
    m = txt.match(/(\d{1,2})\/(\d{1,2})(?:\/(\d{2,4}))?/);
    if (m && /^[a-záéíóú]{3} \d/i.test(txt) || (m && txt.indexOf(m[0]) === 0)) {
      return { n: Number(m[3] || 0) * 10000 + Number(m[2]) * 100 + Number(m[1]) };
    }
    m = txt.match(/^(\d{1,2}):(\d{2})/);
    if (m) return { n: Number(m[1]) * 60 + Number(m[2]) };
    if (/^hoy\b/i.test(txt)) return { n: 0 };
    m = txt.replace(/−/g, "-").match(/[-+]?\d[\d.,]*/);
    if (m && txt.indexOf(m[0]) <= 2) {
      let s = m[0];
      if (s.includes(".") && s.includes(",")) {
        s = s.lastIndexOf(",") > s.lastIndexOf(".")
          ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
      } else if (s.includes(",")) {
        s = s.replace(",", ".");
      } else if (/^[-+]?\d{1,3}(\.\d{3})+$/.test(s)) {
        s = s.replace(/\./g, "");
      }
      const n = Number(s);
      if (Number.isFinite(n)) return { n };
    }
    return { t: txt.toLocaleLowerCase("es") };
  }

  function comparar(a, b) {
    if (a.vacio || b.vacio) return (a.vacio ? 1 : 0) - (b.vacio ? 1 : 0);
    if (a.n !== undefined && b.n !== undefined) return a.n - b.n;
    if (a.n !== undefined) return -1;
    if (b.n !== undefined) return 1;
    return a.t.localeCompare(b.t, "es");
  }

  // Filas agrupadas: una fila que es una sola celda a lo ancho (la sugerencia de
  // un refuerzo) viaja pegada a la de arriba.
  function grupos(tabla, nCols) {
    const out = [];
    for (const tr of tabla.tBodies[0].rows) {
      const unaSola = tr.cells.length === 1 && tr.cells[0].colSpan > 1;
      if (tr.cells.length === nCols) out.push([tr]);
      else if (unaSola && out.length) out[out.length - 1].push(tr);
      else return null;     // mensaje de «sin datos» o forma rara: no se ordena
    }
    return out;
  }

  function filaCabecera(tabla) {
    const filas = tabla.tHead ? tabla.tHead.rows : [];
    return filas.length === 1 ? filas[0] : null;
  }

  function ordenable(tabla) {
    const cab = filaCabecera(tabla);
    if (!cab || !tabla.tBodies.length) return false;
    if (tabla.tBodies[0].querySelector("input, select, textarea")) return false;
    const g = grupos(tabla, cab.cells.length);
    return !!(g && g.length > 1);
  }

  function aplicarOrden(tabla) {
    const cab = filaCabecera(tabla);
    const estado = ordenes[tabla.id];
    if (!cab) return;
    [...cab.cells].forEach((th, i) => {
      if (estado && estado.col === i) th.dataset.orden = estado.dir;
      else delete th.dataset.orden;
    });
    const g = grupos(tabla, cab.cells.length);
    if (!g) return;
    if (!estado) {
      g.sort((a, b) => Number(a[0].dataset.qolI) - Number(b[0].dataset.qolI));
    } else {
      const signo = estado.dir === "asc" ? 1 : -1;
      g.sort((a, b) => {
        const va = valorCelda(a[0].cells[estado.col]);
        const vb = valorCelda(b[0].cells[estado.col]);
        // Los vacíos siempre al final, en los dos sentidos.
        if (va.vacio || vb.vacio) return comparar(va, vb);
        return signo * comparar(va, vb) ||
               Number(a[0].dataset.qolI) - Number(b[0].dataset.qolI);
      });
    }
    const cuerpo = tabla.tBodies[0];
    g.flat().forEach((tr) => cuerpo.appendChild(tr));
  }

  function alClicCabecera(ev) {
    const th = ev.target.closest("th");
    const tabla = th && th.closest("table.ordenable");
    // Un clic en un control dentro del encabezado (un switch) no ordena.
    if (!tabla || ev.target.closest("input, button, a, label, select")) return;
    const col = th.cellIndex;
    const actual = ordenes[tabla.id];
    // Números: primero de mayor a menor, que es lo que se busca (el peor día,
    // la brecha más grande). Texto y fechas: primero ascendente.
    const primera = (() => {
      const cab = filaCabecera(tabla);
      const g = grupos(tabla, cab.cells.length) || [];
      const muestra = g.map((x) => valorCelda(x[0].cells[col])).find((v) => !v.vacio);
      const esFechaOHora = g.length && /^(\d{4}-|\d{1,2}[:/]|[a-záéíóú]{3} \d)/i
        .test((g[0][0].cells[col] || {}).textContent?.trim() || "");
      return muestra && muestra.n !== undefined && !esFechaOHora ? "desc" : "asc";
    })();
    const otra = primera === "asc" ? "desc" : "asc";
    if (!actual || actual.col !== col) ordenes[tabla.id] = { col, dir: primera };
    else if (actual.dir === primera) ordenes[tabla.id] = { col, dir: otra };
    else delete ordenes[tabla.id];
    aplicarOrden(tabla);
    tomarRegistros();
  }

  // ------------------------------------------------------------ copiar tabla

  function celdasVisibles(fila) {
    return [...fila.cells].filter((c) => getComputedStyle(c).display !== "none");
  }

  // Lo que se ve: de un selector la opción elegida (no todas), de un campo su
  // valor, y los botones afuera.
  function textoCelda(c) {
    if (!c.querySelector("input, select, textarea, button")) {
      return c.textContent.replace(/\s+/g, " ").trim();
    }
    const copia = c.cloneNode(true);
    const originales = c.querySelectorAll("input, select, textarea");
    copia.querySelectorAll("input, select, textarea").forEach((el, i) => {
      const o = originales[i];
      const v = o.tagName === "SELECT" ? (o.selectedOptions[0] || {}).textContent || ""
        : o.type === "checkbox" ? (o.checked ? "sí" : "no") : o.value;
      el.replaceWith(document.createTextNode(` ${v} `));
    });
    copia.querySelectorAll("button").forEach((b) => b.remove());
    return copia.textContent.replace(/\s+/g, " ").trim();
  }

  function copiarTabla(tabla, boton) {
    const filas = [...(tabla.tHead ? tabla.tHead.rows : []), ...tabla.tBodies[0].rows]
      // Las filas de sugerencia plegadas no son datos de la tabla.
      .filter((tr) => !(tr.cells.length === 1 && tr.cells[0].colSpan > 1))
      .filter((tr) => getComputedStyle(tr).display !== "none");
    const matriz = filas.map((tr) => celdasVisibles(tr).map(textoCelda));
    const tsv = matriz.map((f) => f.join("\t")).join("\n");
    const html = "<table border=\"1\" cellspacing=\"0\" cellpadding=\"3\" " +
      "style=\"border-collapse:collapse;font-family:sans-serif;font-size:12px\">" +
      matriz.map((f, i) => `<tr>${f.map((v) => i === 0 && tabla.tHead
        ? `<th style="background:#f1f1ef">${esc(v)}</th>` : `<td>${esc(v)}</td>`).join("")}</tr>`)
        .join("") + "</table>";
    // execCommand y no navigator.clipboard: la intranet es http y el
    // portapapeles moderno sólo existe en https.
    const alCopiar = (e) => {
      e.clipboardData.setData("text/plain", tsv);
      e.clipboardData.setData("text/html", html);
      e.preventDefault();
    };
    document.addEventListener("copy", alCopiar);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.removeEventListener("copy", alCopiar);
    const original = boton.innerHTML;
    boton.innerHTML = ok ? '<i class="bi bi-check2 me-1"></i>Copiada'
                         : '<i class="bi bi-x me-1"></i>No se pudo copiar';
    setTimeout(() => { boton.innerHTML = original; }, 1600);
  }

  function asegurarBotonCopiar(tabla) {
    if (!COPIABLES.includes(tabla.id)) return;
    if (document.querySelector(`[data-copiar="${tabla.id}"]`)) return;
    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "btn btn-outline-secondary btn-copiar-tabla";
    boton.dataset.copiar = tabla.id;
    boton.title = "Copiar la tabla (las columnas visibles) para pegarla en Excel o en un mail";
    boton.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copiar';
    boton.addEventListener("click", () => copiarTabla(tabla, boton));
    const card = tabla.closest(".card");
    const cabecera = card && card.querySelector(":scope > .card-header");
    const unaSolaTabla = card && card.querySelectorAll("table.table[id]").length === 1;
    if (cabecera && unaSolaTabla) {
      // Junto a los otros controles del encabezado (a la derecha), sin meterse
      // entre el título y ellos.
      const ultimo = cabecera.lastElementChild;
      const conControles = cabecera.children.length > 1 && ultimo;
      if (conControles && ultimo.matches("div.d-flex")) {
        ultimo.appendChild(boton);
      } else if (conControles && ultimo.matches(".form-check, .btn, select, .input-group")) {
        const grupo = document.createElement("div");
        grupo.className = "d-flex align-items-center gap-2";
        ultimo.replaceWith(grupo);
        grupo.append(ultimo, boton);
      } else {
        cabecera.classList.add("d-flex", "justify-content-between", "align-items-center",
                               "flex-wrap", "gap-2");
        cabecera.appendChild(boton);
      }
      return;
    }
    const barra = document.createElement("div");
    barra.className = "d-flex justify-content-end mb-1";
    barra.appendChild(boton);
    const bloque = tabla.parentElement.classList.contains("table-responsive") ||
                   tabla.parentElement.style.maxHeight ? tabla.parentElement : tabla;
    bloque.before(barra);
  }

  // ---------------------------------------------- procesar lo que se repinta

  let observador = null;
  const tomarRegistros = () => { if (observador) observador.takeRecords(); };

  function procesarTablas() {
    tablasCandidatas().forEach((tabla) => {
      const cuerpo = tabla.tBodies[0];
      if (!cuerpo) return;
      const puede = ordenable(tabla);
      tabla.classList.toggle("ordenable", puede);
      const cab = filaCabecera(tabla);
      if (cab && puede) {
        [...cab.cells].forEach((th) => {
          if (!th.dataset.qolTitulo) {
            th.dataset.qolTitulo = "1";
            th.title = (th.title ? th.title + "\n\n" : "") + "Clic para ordenar";
          }
        });
      }
      // Filas nuevas (la tabla se volvió a pintar): se numeran en el orden en que
      // llegaron, que es al que vuelve el tercer clic, y se reaplica el orden.
      const filas = [...cuerpo.rows];
      if (filas.some((tr) => tr.dataset.qolI === undefined)) {
        filas.forEach((tr, i) => { tr.dataset.qolI = String(i); });
        if (puede && ordenes[tabla.id]) aplicarOrden(tabla);
      }
      // Sólo con datos: una tabla con el cartel de «sin datos» no tiene qué copiar.
      const g = cab ? grupos(tabla, cab.cells.length) : null;
      if (g && g.length) asegurarBotonCopiar(tabla);
    });
    tomarRegistros();
  }

  let pendiente = false;
  function programar() {
    if (pendiente) return;
    pendiente = true;
    requestAnimationFrame(() => {
      pendiente = false;
      procesarTablas();
      construirIndices();
    });
  }
  const programarIndice = programar;

  // ------------------------------------------------- cambios sin guardar

  // Sólo lo que se guarda con un botón: los parámetros de «probar» del
  // Laboratorio (l-*) y el período de «medir» (c-dias) no se pierden de nada.
  const EDITABLES = '[id^="c-"]:not(#c-dias), [data-skill-campo], #tabla-franjas input, #tabla-franjas select';
  const sucias = new Set();

  function marcarSucia(ev) {
    const el = ev.target;
    if (!el.matches || !el.matches(EDITABLES)) return;
    const card = el.closest(".card");
    if (!card || sucias.has(card)) return;
    // El nombre se toma antes de sumar el cartel, para que no quede adentro.
    const t = card.querySelector(".card-header .fw-semibold, .card-header h6, .card-header");
    card.dataset.qolNombre = t ? t.textContent.replace(/\s+/g, " ").trim().slice(0, 60) : "Configuración";
    sucias.add(card);
    card.classList.add("sin-guardar");
    const cab = card.querySelector(":scope > .card-header");
    if (cab && !cab.querySelector(".badge-sin-guardar")) {
      cab.insertAdjacentHTML("beforeend",
        '<span class="badge badge-sin-guardar"><i class="bi bi-pencil me-1"></i>Sin guardar</span>');
    }
    pintarPuntosPestana();
  }

  function limpiarSucias() {
    sucias.forEach((card) => {
      card.classList.remove("sin-guardar");
      const b = card.querySelector(".badge-sin-guardar");
      if (b) b.remove();
    });
    sucias.clear();
    pintarPuntosPestana();
  }

  function pintarPuntosPestana() {
    PESTANAS().forEach((btn) => {
      const pane = document.querySelector(btn.dataset.bsTarget);
      const hay = [...sucias].some((c) => pane && pane.contains(c));
      const punto = btn.querySelector(".punto-sin-guardar");
      if (hay && !punto) {
        btn.insertAdjacentHTML("beforeend",
          '<span class="punto-sin-guardar" title="Hay cambios sin guardar"></span>');
      } else if (!hay && punto) punto.remove();
    });
  }

  // Nombres legibles de dónde hay cambios, para los avisos.
  function cambiosSinGuardar() {
    return [...sucias].filter((c) => document.body.contains(c))
      .map((c) => `«${c.dataset.qolNombre || "Configuración"}»`);
  }

  function confirmarDescarte(accion) {
    const lista = cambiosSinGuardar();
    if (!lista.length) return true;
    const ok = window.confirm(
      `Hay cambios sin guardar en ${lista.join(", ")}.\n\n` +
      `Si seguís (${accion}) se pierden. ¿Seguir igual?`);
    if (ok) limpiarSucias();
    return ok;
  }

  function iniciarSinGuardar() {
    document.addEventListener("input", marcarSucia, true);
    document.addEventListener("change", marcarSucia, true);
    // Toda carga de la configuración la vuelve a pintar desde lo guardado.
    document.addEventListener("planificador:config", limpiarSucias);

    const campana = $("f-campana");
    if (campana) {
      // En captura: corre antes que el listener de planificador.js y puede frenarlo.
      campana.addEventListener("change", (ev) => {
        if (confirmarDescarte("cambiar de campaña")) return;
        ev.stopImmediatePropagation();
        campana.value = String((P().estado || {}).campana ?? campana.value);
      }, true);
    }
    const refrescar = $("btn-refrescar");
    if (refrescar) {
      refrescar.addEventListener("click", (ev) => {
        if (!confirmarDescarte("volver a leer")) ev.stopImmediatePropagation();
      }, true);
    }
    window.addEventListener("beforeunload", (ev) => {
      if (!cambiosSinGuardar().length) return;
      ev.preventDefault();
      ev.returnValue = "";
    });
  }

  // ---------------------------------------------------- períodos rápidos

  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-` +
                     String(d.getDate()).padStart(2, "0");

  // Siempre terminan ayer como tarde: hoy está a medias y tira el error para abajo.
  const RANGOS = [
    ["7 días", () => { const h = ayer(); return [menos(h, 6), h]; }],
    ["4 semanas", () => { const h = ayer(); return [menos(h, 27), h]; }],
    ["90 días", () => { const h = ayer(); return [menos(h, 89), h]; }],
    ["Este mes", () => {
      const h = ayer();
      return [new Date(h.getFullYear(), h.getMonth(), 1), h];
    }],
    ["Mes pasado", () => {
      const hoy = new Date();
      return [new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1),
              new Date(hoy.getFullYear(), hoy.getMonth(), 0)];
    }],
  ];
  function ayer() { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - 1); return d; }
  function menos(d, n) { const x = new Date(d); x.setDate(x.getDate() - n); return x; }

  function rangosRapidos(idDesde, idHasta) {
    const desde = $(idDesde);
    const hasta = $(idHasta);
    if (!desde || !hasta) return;
    // Una línea propia arriba de las fechas: al lado empujaba el resto de los
    // controles a otra fila.
    const fila = hasta.closest(".row");
    const cont = document.createElement("div");
    cont.className = "col-12 d-flex align-items-center gap-2";
    cont.innerHTML = `<span class="small text-muted">Período rápido</span>
      <div class="btn-group rango-rapido" role="group" aria-label="Período rápido">
        ${RANGOS.map(([t], i) =>
          `<button type="button" class="btn btn-outline-secondary" data-rango="${i}">${t}</button>`).join("")}
      </div>`;
    cont.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-rango]");
      if (!b) return;
      const [d, h] = RANGOS[Number(b.dataset.rango)][1]();
      desde.value = iso(d);
      hasta.value = iso(h);
      [desde, hasta].forEach((el) => el.dispatchEvent(new Event("change", { bubbles: true })));
    });
    if (fila) fila.prepend(cont);
    else (hasta.closest(".col-auto") || hasta.parentElement).after(cont);
  }

  // ------------------------------------------------------------- atajos

  const enCampo = (el) => el && (el.isContentEditable ||
    /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName));

  // El selector de día más a la vista en la pestaña abierta.
  function selectorDeDiaVisible() {
    const candidatos = ["f-dia", "d-dia", "b-dia"].map($).filter((s) => s && s.offsetParent);
    let mejor = null;
    let area = 0;
    candidatos.forEach((s) => {
      const card = s.closest(".card") || s;
      const r = card.getBoundingClientRect();
      const visible = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
      if (visible > area) { area = visible; mejor = s; }
    });
    return mejor;
  }

  function moverDia(paso) {
    const sel = selectorDeDiaVisible();
    if (!sel) return;
    const i = sel.selectedIndex + paso;
    if (i < 0 || i >= sel.options.length) return;
    sel.selectedIndex = i;
    sel.dispatchEvent(new Event("change"));
  }

  let modalAtajos = null;
  function mostrarAtajos() {
    if (!window.bootstrap) return;
    if (!modalAtajos) {
      const nombres = PESTANAS().map((b, i) => `<kbd class="atajo">${i + 1}</kbd> ${esc(b.textContent.trim())}`);
      const el = document.createElement("div");
      el.className = "modal fade";
      el.tabIndex = -1;
      el.innerHTML = `<div class="modal-dialog modal-dialog-centered"><div class="modal-content">
        <div class="modal-header py-2"><h6 class="modal-title"><i class="bi bi-keyboard me-2"></i>Atajos de teclado</h6>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
        <div class="modal-body small">
          <table class="table table-sm mb-2"><tbody>
            <tr><td class="text-nowrap"><kbd class="atajo">←</kbd> <kbd class="atajo">→</kbd></td>
                <td>Día anterior / siguiente (el selector de día del bloque que estás mirando)</td></tr>
            <tr><td class="text-nowrap"><kbd class="atajo">1</kbd>…<kbd class="atajo">${PESTANAS().length}</kbd></td>
                <td>${nombres.join(" · ")}</td></tr>
            <tr><td><kbd class="atajo">R</kbd></td><td>Volver a leer el plan</td></tr>
            <tr><td><kbd class="atajo">Esc</kbd></td><td>Cerrar un gráfico ampliado</td></tr>
            <tr><td><kbd class="atajo">?</kbd></td><td>Esta ayuda</td></tr>
          </tbody></table>
          <div class="text-muted"><strong>Gráficos:</strong> arrastrá para acercar un tramo,
            <kbd class="atajo">Ctrl</kbd> + rueda para acercar o alejar,
            <kbd class="atajo">Shift</kbd> + arrastrar para moverte y doble clic para verlo entero.
            <strong>Tablas:</strong> clic en un encabezado para ordenar (el tercer clic la deja como venía).</div>
        </div></div></div>`;
      document.body.appendChild(el);
      modalAtajos = window.bootstrap.Modal.getOrCreateInstance(el);
    }
    modalAtajos.show();
  }

  function alTeclado(ev) {
    if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.defaultPrevented) return;
    if (enCampo(document.activeElement)) return;
    if (document.querySelector(".modal.show") && ev.key !== "?") return;
    if (document.querySelector(".plan-chart.expandido") && ev.key !== "?") return;
    if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      moverDia(ev.key === "ArrowLeft" ? -1 : 1);
      ev.preventDefault();
    } else if (/^[1-9]$/.test(ev.key)) {
      const btn = PESTANAS()[Number(ev.key) - 1];
      if (btn) { mostrarPestana(nombrePestana(btn)); ev.preventDefault(); }
    } else if (ev.key === "r" || ev.key === "R") {
      const b = $("btn-refrescar");
      if (b && !b.disabled) { b.click(); ev.preventDefault(); }
    } else if (ev.key === "?") {
      mostrarAtajos();
      ev.preventDefault();
    }
  }

  // ------------------------------------------------------ índice por pestaña

  function tituloDe(bloque) {
    const t = bloque.querySelector(".card-header .fw-semibold, .card-header h6, h6.fw-semibold");
    return t ? t.textContent.replace(/\s+/g, " ").trim() : "";
  }

  const oculto = (el) => !!el.closest("[hidden]") || getComputedStyle(el).display === "none";

  // Bloques de la pestaña: tarjetas de primer nivel (las que no están dentro de
  // otra) con título. El seguimiento de hoy va primero, con nombre propio.
  function bloquesDe(pane) {
    const out = [];
    const seg = pane.querySelector("#seguimiento");
    if (seg && seg.children.length) out.push({ el: seg, texto: "Hoy" });
    pane.querySelectorAll(".card").forEach((card) => {
      if (card.parentElement.closest(".card") || oculto(card)) return;
      if (seg && seg.contains(card)) return;
      const texto = tituloDe(card);
      if (texto) out.push({ el: card, texto });
    });
    return out;
  }

  const firmas = new WeakMap();
  function construirIndices() {
    document.querySelectorAll(".viz-root .tab-content > .tab-pane").forEach((pane) => {
      const bloques = bloquesDe(pane);
      let barra = pane.querySelector(":scope > .plan-indice");
      const firma = bloques.map((b) => b.texto).join("|");
      if (bloques.length < 4) {
        if (barra) barra.remove();
        return;
      }
      if (barra && firmas.get(barra) === firma) return;
      if (!barra) {
        barra = document.createElement("nav");
        barra.className = "plan-indice";
        barra.setAttribute("aria-label", "Bloques de esta pestaña");
        pane.prepend(barra);
      }
      firmas.set(barra, firma);
      barra._bloques = bloques;
      barra.innerHTML = bloques.map((b, i) => {
        const corto = b.texto.length > 38 ? b.texto.slice(0, 36) + "…" : b.texto;
        return `<a href="#" data-i="${i}" title="${esc(b.texto)}">${esc(corto)}</a>`;
      }).join("");
    });
    marcarActivo();
  }

  function marcarActivo() {
    const pane = document.querySelector(".viz-root .tab-content > .tab-pane.active");
    const barra = pane && pane.querySelector(":scope > .plan-indice");
    if (!barra || !barra._bloques) return;
    const alto = barra.getBoundingClientRect().height + 24;
    let activo = 0;
    barra._bloques.forEach((b, i) => {
      if (b.el.getBoundingClientRect().top <= alto) activo = i;
    });
    barra.querySelectorAll("a").forEach((a, i) => a.classList.toggle("activo", i === activo));
    const a = barra.querySelector("a.activo");
    // Que el enlace activo quede a la vista si el índice scrollea de costado.
    if (a && (a.offsetLeft < barra.scrollLeft ||
              a.offsetLeft + a.offsetWidth > barra.scrollLeft + barra.clientWidth)) {
      barra.scrollLeft = a.offsetLeft - 16;
    }
  }

  function alClicIndice(ev) {
    const a = ev.target.closest(".plan-indice a[data-i]");
    if (!a) return;
    ev.preventDefault();
    const bloque = a.closest(".plan-indice")._bloques[Number(a.dataset.i)];
    if (bloque) bloque.el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ inicio

  function iniciar() {
    iniciarPestanas();
    iniciarSinGuardar();
    rangosRapidos("b-desde", "b-hasta");
    rangosRapidos("l-desde", "l-hasta");
    document.addEventListener("click", alClicCabecera);
    document.addEventListener("click", alClicIndice);
    document.addEventListener("keydown", alTeclado);
    if ($("btn-atajos")) $("btn-atajos").addEventListener("click", mostrarAtajos);
    let scrollPendiente = false;
    window.addEventListener("scroll", () => {
      if (scrollPendiente) return;
      scrollPendiente = true;
      requestAnimationFrame(() => { scrollPendiente = false; marcarActivo(); });
    }, { passive: true });

    const raiz = document.querySelector(".viz-root");
    if (raiz) {
      observador = new MutationObserver(programar);
      observador.observe(raiz, { childList: true, subtree: true,
                                 attributes: true, attributeFilter: ["hidden"] });
    }
    programar();
  }

  window.PlanificadorQoL = { cambiosSinGuardar, confirmarDescarte };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
