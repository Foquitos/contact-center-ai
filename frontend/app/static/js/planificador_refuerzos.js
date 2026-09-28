/* Planificador — Seguimiento de los pedidos de refuerzo y turnos sugeridos.
 *
 * Se integra con window.Planificador (planificador.js).
 * Maneja el filtrado de la tabla (Pendientes | Pedidos | Todos) y las acciones
 * operativas de 'Marcar pedido' y 'Descartar'.
 */
(function () {
  "use strict";

  let filtroActual = "todos";

  function aplicarFiltro(tipo) {
    if (tipo) filtroActual = tipo;

    // Actualizar botones de filtro
    const grupo = document.getElementById("filtro-refuerzos");
    if (grupo) {
      grupo.querySelectorAll("button[data-filtro]").forEach((btn) => {
        if (btn.dataset.filtro === filtroActual) {
          btn.classList.add("active", "btn-secondary");
          btn.classList.remove("btn-outline-secondary");
        } else {
          btn.classList.remove("active", "btn-secondary");
          btn.classList.add("btn-outline-secondary");
        }
      });
    }

    const tabla = document.getElementById("tabla-refuerzos");
    if (!tabla) return;

    const filas = tabla.querySelectorAll("tbody tr");
    filas.forEach((tr) => {
      const est = tr.dataset.estado;
      if (!est) return;

      if (filtroActual === "todos") {
        tr.hidden = false;
      } else if (filtroActual === "pendientes") {
        tr.hidden = (est !== "pendiente");
      } else if (filtroActual === "pedidos") {
        tr.hidden = (est === "pendiente" || est === "descartado");
      }
    });
  }

  async function marcarPedido(idx) {
    const P = window.Planificador;
    if (!P || !P.estado || !P.estado.refuerzos) return;
    const refuerzos = P.estado.refuerzos.refuerzos || [];
    const f = refuerzos[idx];
    if (!f) return;

    const nota = prompt("Nota opcional para el pedido a RRHH (ej. motivo, área que autoriza):", "");
    if (nota === null) return; // cancelado por el usuario

    const campanaId = P.estado.campana;
    const ped = f.pedido;

    try {
      if (ped && ped.id && ped.estado === "descartado") {
        // Si ya existía y estaba descartado, reactivar vía PUT
        await P.pedir(`refuerzos/pedidos/${ped.id}?campana_id=${campanaId}`, {
          method: "PUT",
          body: JSON.stringify({
            estado: "pedido",
            nota: nota.trim() || undefined,
          }),
        });
      } else {
        // Alta de nuevo pedido
        await P.pedir(`refuerzos/pedidos?campana_id=${campanaId}`, {
          method: "POST",
          body: JSON.stringify({
            pool_id: f.pool_id,
            dia: f.dia,
            desde: f.desde,
            hasta: f.hasta,
            faltante_pico: f.faltante_pico,
            horas_operador: f.horas_operador,
            accion: f.accion,
            nota: nota.trim() || null,
            estado: "pedido",
          }),
        });
      }

      if (typeof P.cargarRefuerzos === "function") {
        await P.cargarRefuerzos();
      } else {
        P.recargar();
      }
    } catch (e) {
      document.getElementById("avisos").innerHTML =
        P.aviso(`No se pudo registrar el pedido: ${P.esc(e.message)}`, "danger", "x-octagon");
    }
  }

  async function descartarPedido(idx) {
    const P = window.Planificador;
    if (!P || !P.estado || !P.estado.refuerzos) return;
    const refuerzos = P.estado.refuerzos.refuerzos || [];
    const f = refuerzos[idx];
    if (!f) return;

    if (!confirm("¿Descartar este bloque de refuerzo?")) return;

    const campanaId = P.estado.campana;
    const ped = f.pedido;

    try {
      if (ped && ped.id) {
        await P.pedir(`refuerzos/pedidos/${ped.id}?campana_id=${campanaId}`, {
          method: "PUT",
          body: JSON.stringify({ estado: "descartado" }),
        });
      } else {
        await P.pedir(`refuerzos/pedidos?campana_id=${campanaId}`, {
          method: "POST",
          body: JSON.stringify({
            pool_id: f.pool_id,
            dia: f.dia,
            desde: f.desde,
            hasta: f.hasta,
            faltante_pico: f.faltante_pico,
            horas_operador: f.horas_operador,
            accion: f.accion,
            estado: "descartado",
          }),
        });
      }

      if (typeof P.cargarRefuerzos === "function") {
        await P.cargarRefuerzos();
      } else {
        P.recargar();
      }
    } catch (e) {
      document.getElementById("avisos").innerHTML =
        P.aviso(`No se pudo descartar el pedido: ${P.esc(e.message)}`, "danger", "x-octagon");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Delegación de eventos para botones de filtro
    const grupoFiltro = document.getElementById("filtro-refuerzos");
    if (grupoFiltro) {
      grupoFiltro.addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-filtro]");
        if (!btn) return;
        aplicarFiltro(btn.dataset.filtro);
      });
    }

    // Delegación de eventos sobre la tabla de refuerzos
    const tabla = document.getElementById("tabla-refuerzos");
    if (tabla) {
      tabla.addEventListener("click", (ev) => {
        const btnMarcar = ev.target.closest(".btn-marcar-refuerzo");
        if (btnMarcar) {
          const idx = parseInt(btnMarcar.dataset.idx, 10);
          if (!isNaN(idx)) marcarPedido(idx);
          return;
        }

        const btnDescartar = ev.target.closest(".btn-descartar-refuerzo");
        if (btnDescartar) {
          const idx = parseInt(btnDescartar.dataset.idx, 10);
          if (!isNaN(idx)) descartarPedido(idx);
          return;
        }
      });
    }
  });

  window.PlanificadorRefuerzos = {
    aplicarFiltro,
  };
})();
