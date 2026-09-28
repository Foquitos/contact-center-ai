/*
  Motor del tutorial guiado.

  Recorre la pantalla REAL (no una captura ni un video): resalta un control por vez,
  explica qué hace y avanza con Siguiente. Los pasos de cada pantalla no viven acá,
  sino en tutorial_pasos.js; este archivo solo sabe mostrarlos.

  Por qué un motor propio y no una librería (driver.js, shepherd, intro.js): la app
  ya trae bootstrap, jquery, select2, jstree, flatpickr, nouislider y chart.js desde
  CDN. Esto son ~350 líneas sin dependencias, y el CDN de una librería más es una
  cosa más que se puede caer en producción.

  Reglas que se banca el motor y que importan más de lo que parece:

  - LA PANTALLA SE TOCA. El tutorial no bloquea nada: la persona va eligiendo la
    empresa, la campaña y la plantilla de verdad, y así ve aparecer lo que solo
    aparece cuando hay algo elegido (los modos por operador/tipificación, el árbol
    de tipificaciones, las columnas de la plantilla, la tabla de resultados). Un
    tutorial que explica una pantalla vacía se pierde la mitad de la pantalla.
  - Lo único bloqueado son los botones que LANZAN algo (`bloquear` en la definición
    del tutorial): auditar gasta tokens y cupo de verdad. El clic se anula y se
    avisa por qué; el resto del formulario queda vivo.
  - Un paso puede ESPERAR a que la persona haga lo suyo (`interactivo: true` +
    `avanzarCuando` / `avanzarAlVer`): el tutorial sigue solo cuando el select tiene
    valor o cuando aparece lo que se estaba esperando. El botón Siguiente sigue
    estando para el que prefiere solo mirar.
  - Un paso cuyo control NO está en pantalla se saltea solo (o se muestra centrado,
    con `siFalta: 'centrar'`). Es lo que hace que el mismo tutorial sirva para
    permisos distintos: al que no tiene "Auditar ahora" no le explica ese botón,
    porque el botón no existe en su DOM.

  API pública (window.AcmeTutorial):
    registrar(id, {titulo, pasos, bloquear}) — lo llama tutorial_pasos.js
    iniciar(id, {recorrido})                  — botones y arranque automático
    hay(id)                                   — si existe tutorial para esta pantalla
*/
(function () {
    'use strict';

    var CFG = {};
    try {
        var nodoCfg = document.getElementById('tutorial-config');
        if (nodoCfg) { CFG = JSON.parse(nodoCfg.textContent || '{}'); }
    } catch (e) { CFG = {}; }

    var registro = {};
    var st = null;          // estado del tutorial en curso
    var dom = null;         // nodos del overlay, creados una sola vez

    // --------------------------------------------------------------------- //
    // Memoria: qué tutoriales ya vio esta persona en ESTE navegador.
    // Va en localStorage y no en la base porque es una preferencia de comodidad:
    // si alguien cambia de máquina y le vuelve a aparecer una vez, no pasa nada.
    // La versión permite volver a mostrarlo cuando el tutorial cambia de verdad.
    // --------------------------------------------------------------------- //
    var VERSION = String(CFG.version || '1');

    function claveVisto(id) { return 'acme.tutorial.visto.' + id; }

    function yaLoVio(id) {
        try { return localStorage.getItem(claveVisto(id)) === VERSION; }
        catch (e) { return true; }   // sin localStorage no insistimos
    }

    function marcarVisto(id) {
        try { localStorage.setItem(claveVisto(id), VERSION); } catch (e) { /* no pasa nada */ }
    }

    // Recorrido completo (varias pantallas encadenadas): vive en sessionStorage
    // porque es un viaje de una sentada, no una preferencia.
    var CLAVE_RECORRIDO = 'acme.tutorial.recorrido';

    function recorridoActivo() {
        try { return sessionStorage.getItem(CLAVE_RECORRIDO) === '1'; }
        catch (e) { return false; }
    }

    function setRecorrido(activo) {
        try {
            if (activo) { sessionStorage.setItem(CLAVE_RECORRIDO, '1'); }
            else { sessionStorage.removeItem(CLAVE_RECORRIDO); }
        } catch (e) { /* idem */ }
    }

    function siguienteDelRecorrido(id) {
        var lista = CFG.recorrido || [];
        for (var i = 0; i < lista.length; i++) {
            if (lista[i].id === id) { return lista[i + 1] || null; }
        }
        return null;
    }

    // --------------------------------------------------------------------- //
    // Utilidades de DOM                                                       //
    // --------------------------------------------------------------------- //

    function buscar(selector) {
        if (!selector) { return null; }
        var el;
        try { el = document.querySelector(selector); }
        catch (e) { return null; }
        return caraVisible(el);
    }

    /* Varios <select> de la app los reemplaza select2, que deja el original de 1x1 px
       y dibuja al lado un <span class="select2-container">. Resaltar el original sería
       resaltar un punto invisible, así que se salta al que se ve. Sin esto el tutorial
       de Auditar apuntaba a la nada en Campaña, Plantilla y Tipificaciones. */
    function caraVisible(el) {
        if (el && el.classList && el.classList.contains('select2-hidden-accessible')) {
            var s2 = el.nextElementSibling;
            if (s2 && s2.classList.contains('select2-container')) { return s2; }
        }
        return el;
    }

    function visible(el) {
        if (!el || !el.isConnected) { return false; }
        var r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) { return false; }
        var cs = getComputedStyle(el);
        return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
    }

    /* Espera hasta `ms` a que el control aparezca. Sirve para lo que se dibuja con
       JavaScript después de una llamada al backend (la lista de empresas, los KPI
       del dashboard): sin esto el tutorial saltearía pasos por llegar temprano. */
    function esperarPor(selector, ms) {
        return new Promise(function (resolve) {
            if (!selector) { return resolve(null); }
            var el = buscar(selector);
            if (visible(el)) { return resolve(el); }
            if (!ms) { return resolve(el); }
            var limite = Date.now() + ms;
            var t = setInterval(function () {
                var e2 = buscar(selector);
                if (visible(e2) || Date.now() > limite) {
                    clearInterval(t);
                    resolve(e2);
                }
            }, 120);
        });
    }

    /* Trae el control a la vista y espera a que el scroll se quede quieto: si se
       mide antes, el resaltado queda dibujado donde el control ya no está. */
    function acercar(el) {
        return new Promise(function (resolve) {
            var r = el.getBoundingClientRect();
            // Un control MÁS ALTO QUE LA PANTALLA (la tabla de resultados con sus 100
            // filas, la grilla de gráficos) no se puede "centrar": centrarlo deja su
            // medio en pantalla y su principio arriba de todo. De esos se muestra el
            // arranque, que es lo que la persona está mirando.
            var gigante = r.height > innerHeight - 120;
            var dentro = gigante
                ? (r.top >= 0 && r.top <= innerHeight - 200)
                : (r.top >= 90 && r.bottom <= innerHeight - 40);
            if (dentro) { return resolve(); }
            try { el.scrollIntoView({ behavior: 'smooth', block: gigante ? 'start' : 'center' }); }
            catch (e) { el.scrollIntoView(); }

            var ultimo = null, quietos = 0, vueltas = 0;
            var t = setInterval(function () {
                var y = Math.round(el.getBoundingClientRect().top);
                quietos = (y === ultimo) ? quietos + 1 : 0;
                ultimo = y;
                if (quietos >= 2 || ++vueltas > 30) { clearInterval(t); resolve(); }
            }, 40);
        });
    }

    // --------------------------------------------------------------------- //
    // Armado del overlay (una sola vez por página)                            //
    // --------------------------------------------------------------------- //

    function crearDom() {
        if (dom) { return dom; }
        var capa = document.createElement('div');
        capa.className = 'tour-capa';
        capa.setAttribute('aria-hidden', 'true');

        function panel(clase) {
            var d = document.createElement('div');
            d.className = clase;
            capa.appendChild(d);
            return d;
        }
        var paneles = [panel('tour-panel'), panel('tour-panel'), panel('tour-panel'), panel('tour-panel')];
        var marco = panel('tour-marco');

        var globo = document.createElement('div');
        globo.className = 'tour-globo';
        globo.setAttribute('role', 'dialog');
        globo.setAttribute('aria-live', 'polite');
        globo.setAttribute('aria-label', 'Tutorial guiado');
        globo.innerHTML =
            '<div class="tour-flecha"></div>' +
            '<div class="tour-cabecera">' +
                '<svg class="tour-pez" viewBox="0 0 64 64" aria-hidden="true" focusable="false">' +
                    '<use href="#coral-pez"></use></svg>' +
                '<h2 class="tour-titulo"></h2>' +
                '<span class="tour-paso-num"></span>' +
                '<button type="button" class="tour-cerrar" aria-label="Cerrar el tutorial">' +
                    '<i class="bi bi-x-lg"></i></button>' +
            '</div>' +
            '<div class="tour-cuerpo"></div>' +
            '<div class="tour-progreso"><div class="tour-progreso-relleno"></div></div>' +
            '<div class="tour-pie">' +
                '<button type="button" class="tour-salir">Salir del tutorial</button>' +
                '<button type="button" class="btn btn-sm btn-outline-secondary tour-anterior">Anterior</button>' +
                '<button type="button" class="btn btn-sm btn-primary tour-siguiente">Siguiente</button>' +
            '</div>';

        var aviso = document.createElement('div');
        aviso.className = 'tour-aviso';
        aviso.setAttribute('role', 'status');
        aviso.hidden = true;
        aviso.innerHTML = '<i class="bi bi-lock-fill" aria-hidden="true"></i>' +
            '<span>Ese botón está desactivado mientras dura el tutorial: lanza una ' +
            'auditoría de verdad. Salí del tutorial para usarlo.</span>';

        document.body.appendChild(capa);
        document.body.appendChild(globo);
        document.body.appendChild(aviso);

        dom = {
            capa: capa, paneles: paneles, marco: marco, globo: globo,
            flecha: globo.querySelector('.tour-flecha'),
            titulo: globo.querySelector('.tour-titulo'),
            num: globo.querySelector('.tour-paso-num'),
            cuerpo: globo.querySelector('.tour-cuerpo'),
            aviso: aviso,
            relleno: globo.querySelector('.tour-progreso-relleno'),
            anterior: globo.querySelector('.tour-anterior'),
            siguiente: globo.querySelector('.tour-siguiente'),
            salir: globo.querySelector('.tour-salir'),
            cerrar: globo.querySelector('.tour-cerrar')
        };

        dom.siguiente.addEventListener('click', function () { avanzar(1); });
        dom.anterior.addEventListener('click', function () { avanzar(-1); });
        dom.salir.addEventListener('click', function () { terminar(false); });
        dom.cerrar.addEventListener('click', function () { terminar(false); });
        // Los paneles oscuros no reciben clics (pointer-events: none en el CSS): la
        // pantalla se usa normalmente mientras el tutorial corre.

        return dom;
    }

    function ocultarCapa() {
        dom.paneles.forEach(function (p) { p.style.display = 'none'; });
        dom.marco.style.display = 'none';
    }

    /* Dibuja los cuatro paneles alrededor del hueco. Los paneles solo oscurecen:
       no tapan el clic (ver .tour-panel en el CSS). */
    function pintarHueco(r, interactivo) {
        var m = 6;   // aire alrededor del control
        var t = Math.max(0, r.top - m), l = Math.max(0, r.left - m);
        var w = r.width + m * 2, h = r.height + m * 2;
        var b = t + h, der = l + w;

        var p = dom.paneles;
        p.forEach(function (x) { x.style.display = 'block'; });
        // arriba / abajo / izquierda / derecha
        Object.assign(p[0].style, { top: '0px', left: '0px', width: '100%', height: t + 'px' });
        Object.assign(p[1].style, { top: b + 'px', left: '0px', width: '100%', height: Math.max(0, innerHeight - b) + 'px' });
        Object.assign(p[2].style, { top: t + 'px', left: '0px', width: l + 'px', height: h + 'px' });
        Object.assign(p[3].style, { top: t + 'px', left: der + 'px', width: Math.max(0, innerWidth - der) + 'px', height: h + 'px' });

        Object.assign(dom.marco.style, { display: 'block', top: t + 'px', left: l + 'px', width: w + 'px', height: h + 'px' });
        // Amarillo y latiendo cuando le toca a la persona; cian cuando solo se explica.
        dom.marco.classList.toggle('is-interactivo', !!interactivo);
    }

    /* Oscurece toda la pantalla, sin hueco (pasos centrados). */
    function pintarTodo() {
        var p = dom.paneles;
        p.forEach(function (x) { x.style.display = 'none'; });
        p[0].style.display = 'block';
        Object.assign(p[0].style, { top: '0px', left: '0px', width: '100%', height: '100%' });
        dom.marco.style.display = 'none';
    }

    /* Hasta dónde se ve realmente un control: la intersección de la ventana con
       todos sus ancestros que RECORTAN (overflow auto/scroll/hidden).

       Es lo que arregla el caso de la tabla de auditorías: #results-table-container
       tiene su propio scroll (max-height 600px), así que una fila scrolleada fuera de
       la tabla sigue teniendo tamaño y posición —pero muy lejos, arriba o abajo del
       contenedor— y `getBoundingClientRect()` la devuelve igual. Sin esto, el
       resaltado se dibujaba donde no hay nada y el globo, que se ubica respecto de
       ese rectángulo, se iba de la pantalla al mover ese scrollbar. */
    function ventanaVisible(el) {
        var caja = { top: 0, left: 0, bottom: innerHeight, right: innerWidth };
        var p = el.parentElement;
        while (p && p !== document.body && p !== document.documentElement) {
            var cs = getComputedStyle(p);
            if (/(auto|scroll|hidden)/.test(cs.overflowY + ' ' + cs.overflowX)) {
                var pr = p.getBoundingClientRect();
                caja.top = Math.max(caja.top, pr.top);
                caja.left = Math.max(caja.left, pr.left);
                caja.bottom = Math.min(caja.bottom, pr.bottom);
                caja.right = Math.min(caja.right, pr.right);
            }
            p = p.parentElement;
        }
        return caja;
    }

    /* El rectángulo que se va a resaltar, siempre dentro de la pantalla.

       Sin esto, un control más alto que la ventana (la tabla de resultados scrolleada
       por la mitad, la grilla de gráficos del dashboard) devuelve un rectángulo que
       empieza mucho más arriba y termina mucho más abajo del viewport. Con eso, el
       hueco tapa todo y —peor— el globo se ubicaba respecto de un borde que está
       fuera de la pantalla y terminaba invisible: el tutorial "desaparecía" al
       scrollear entre las auditorías.

       Se recorta a lo que se ve y, si aun así ocupa casi todo, se marca solo su
       parte de arriba: alcanza para señalar "esto de acá" y deja lugar al globo. El
       tope se calcula con el alto REAL del globo de este paso, así siempre queda
       abajo un hueco donde meterlo sin taparlo. */
    function recorte(r, el) {
        var m = 8;
        var caja = el ? ventanaVisible(el)
                      : { top: 0, left: 0, bottom: innerHeight, right: innerWidth };
        var top = Math.max(r.top, caja.top + m / 2, m);
        var left = Math.max(r.left, caja.left, m);
        var bottom = Math.min(r.bottom, caja.bottom - m / 2, innerHeight - m);
        var right = Math.min(r.right, caja.right, innerWidth - m);
        var alturaGlobo = (dom && dom.globo) ? dom.globo.offsetHeight : 0;
        // Lo que sobra de pantalla después del globo y sus márgenes; nunca menos de
        // un 30% de la ventana, para que el resaltado siga significando algo.
        var maxAlto = Math.max(innerHeight * 0.30, innerHeight - top - alturaGlobo - 34);
        if (bottom - top > maxAlto) { bottom = top + maxAlto; }
        return {
            top: top, left: left, bottom: bottom, right: right,
            width: Math.max(0, right - left), height: Math.max(0, bottom - top)
        };
    }

    // --------------------------------------------------------------------- //
    // Candado: los botones que lanzan algo no funcionan durante el tutorial    //
    // --------------------------------------------------------------------- //

    /* La pantalla queda usable a propósito (para eso está el tutorial: para que la
       persona vaya eligiendo y vea aparecer las cosas), pero "ENVIAR A LA COLA"
       lanza una auditoría real, que gasta tokens y descuenta cupo. Esos botones se
       anulan mientras dura el recorrido.

       Se anula el CLIC (en captura, antes de que llegue el handler de la pantalla) y,
       en los formularios que se declaren en `bloquearSubmit`, también el SUBMIT: un
       Enter en cualquier campo manda el formulario sin pasar por ningún botón. Los
       dos son listas aparte a propósito — en "Auditorías Realizadas" hay botones que
       conviene bloquear (borrar una plantilla de columnas) dentro del mismo <form>
       que el botón Buscar, que el tutorial necesita que ande. */
    function ponerCandado(selectores, formularios) {
        st.bloqueados = (selectores || []).filter(function (sel) {
            try { return document.querySelector(sel) !== null; }
            catch (e) { return false; }
        });
        st.formsBloqueados = (formularios || []).filter(function (sel) {
            try { return document.querySelector(sel) !== null; }
            catch (e) { return false; }
        });
        if (!st.bloqueados.length) { return; }
        var lista = st.bloqueados.join(',');

        st.candadoClic = function (ev) {
            var t = ev.target;
            if (!t || !t.closest) { return; }
            if (!t.closest(lista)) { return; }
            ev.preventDefault();
            ev.stopPropagation();
            if (ev.stopImmediatePropagation) { ev.stopImmediatePropagation(); }
            avisar();
        };
        st.candadoSubmit = function (ev) {
            var form = ev.target;
            if (!form || !form.matches || !st.formsBloqueados.length) { return; }
            if (!form.matches(st.formsBloqueados.join(','))) { return; }
            ev.preventDefault();
            ev.stopPropagation();
            if (ev.stopImmediatePropagation) { ev.stopImmediatePropagation(); }
            avisar();
        };
        document.addEventListener('click', st.candadoClic, true);
        if (st.formsBloqueados.length) {
            document.addEventListener('submit', st.candadoSubmit, true);
        }

        // Marca visual: el botón se ve apagado y con el candado. No se le pone
        // `disabled` porque la pantalla maneja ese atributo por su cuenta.
        st.bloqueados.forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (b) {
                b.classList.add('tour-bloqueado');
                b.setAttribute('aria-disabled', 'true');
            });
        });
    }

    function sacarCandado() {
        if (!st) { return; }
        if (st.candadoClic) { document.removeEventListener('click', st.candadoClic, true); }
        if (st.candadoSubmit) { document.removeEventListener('submit', st.candadoSubmit, true); }
        (st.bloqueados || []).forEach(function (sel) {
            document.querySelectorAll(sel).forEach(function (b) {
                b.classList.remove('tour-bloqueado');
                b.removeAttribute('aria-disabled');
            });
        });
    }

    function avisar() {
        if (!dom || !dom.aviso) { return; }
        dom.aviso.hidden = false;
        clearTimeout(st && st.avisoTimer);
        if (st) {
            st.avisoTimer = setTimeout(function () {
                if (dom && dom.aviso) { dom.aviso.hidden = true; }
            }, 4000);
        }
    }

    // --------------------------------------------------------------------- //
    // Ubicación del globo                                                     //
    // --------------------------------------------------------------------- //

    var LADOS = ['abajo', 'arriba', 'derecha', 'izquierda'];

    /* Dónde poner el globo. La regla de oro: NUNCA encima del control resaltado.
       Si lo tapa, el paso interactivo se vuelve imposible — es lo que pasaba con el
       botón "Buscar Auditorías", pegado al borde izquierdo y abajo de todo: los
       cuatro lados "no entraban" y el último se aceptaba a la fuerza, justo arriba
       del botón.

       Para arriba/abajo solo hace falta que entre a lo ALTO (el globo se corre a los
       costados sin acercarse al control); para izquierda/derecha, que entre a lo
       ANCHO. Antes se exigían las dos cosas y por eso descartaba lados que servían. */
    function ubicar(r, ladoPref) {
        var g = dom.globo;
        g.classList.remove('is-centrado');
        g.style.transform = '';
        var gw = g.offsetWidth, gh = g.offsetHeight;
        var sep = 16, borde = 10;
        var maxTop = Math.max(borde, innerHeight - gh - borde);
        var maxLeft = Math.max(borde, innerWidth - gw - borde);

        function acotar(v, max) { return Math.min(Math.max(v, borde), max); }

        /* Cada candidato se ACOTA a la pantalla, y `entra` dice si además no hizo
           falta acotarlo por el lado que importa. Acotar siempre es lo que garantiza
           que el globo nunca termine fuera de la vista, por raro que sea el control. */
        function candidato(lado) {
            var top, left, entra;
            if (lado === 'abajo') {
                top = r.bottom + sep;
                entra = top + gh <= innerHeight - borde;
                top = acotar(top, maxTop);
                left = acotar(r.left + r.width / 2 - gw / 2, maxLeft);
            } else if (lado === 'arriba') {
                top = r.top - sep - gh;
                entra = top >= borde;
                top = acotar(top, maxTop);
                left = acotar(r.left + r.width / 2 - gw / 2, maxLeft);
            } else if (lado === 'derecha') {
                left = r.right + sep;
                entra = left + gw <= innerWidth - borde;
                left = acotar(left, maxLeft);
                top = acotar(r.top + r.height / 2 - gh / 2, maxTop);
            } else {
                left = r.left - sep - gw;
                entra = left >= borde;
                left = acotar(left, maxLeft);
                top = acotar(r.top + r.height / 2 - gh / 2, maxTop);
            }
            return { lado: lado, top: top, left: left, entra: entra };
        }

        /* Cuánto se pisan dos rectángulos. 0 = no se tocan. */
        function pisa(c) {
            var ancho = Math.min(c.left + gw, r.right) - Math.max(c.left, r.left);
            var alto = Math.min(c.top + gh, r.bottom) - Math.max(c.top, r.top);
            if (ancho <= 0 || alto <= 0) { return 0; }
            return ancho * alto;
        }

        var orden = [ladoPref].concat(LADOS).filter(function (x, i, a) {
            return x && a.indexOf(x) === i;
        });

        var elegido = null, respaldo = null;
        for (var i = 0; i < orden.length; i++) {
            var c = candidato(orden[i]);
            if (c.entra && !pisa(c)) { elegido = c; break; }
            // El menos malo, por si no entra de ningún lado (pantalla chica, globo
            // largo): el que menos tape.
            if (!respaldo || pisa(c) < pisa(respaldo)) { respaldo = c; }
        }

        if (!elegido) {
            elegido = respaldo;
            // Último recurso: el control ocupa casi toda la pantalla. Se lo manda a
            // la franja libre más grande (arriba o abajo del control) aunque quede
            // apretado; tapar el control es peor que quedar corto.
            if (pisa(elegido)) {
                var arriba = r.top - sep, abajo = innerHeight - r.bottom - sep;
                elegido = arriba >= abajo
                    ? { lado: 'arriba', top: Math.max(borde, r.top - sep - gh),
                        left: acotar(r.left + r.width / 2 - gw / 2, maxLeft) }
                    : { lado: 'abajo', top: Math.min(maxTop, r.bottom + sep),
                        left: acotar(r.left + r.width / 2 - gw / 2, maxLeft) };
            }
        }

        g.style.top = elegido.top + 'px';
        g.style.left = elegido.left + 'px';
        colocarFlecha(elegido.lado, r, elegido.top, elegido.left, gw, gh);
    }

    function colocarFlecha(lado, r, top, left, gw, gh) {
        var f = dom.flecha;
        f.style.display = 'block';
        // La flecha se pinta como el encabezado solo si sale por arriba (que es donde
        // está el encabezado); en el resto de los lados sale del cuerpo blanco.
        f.classList.toggle('is-cuerpo', lado !== 'abajo');
        var centroX = r.left + r.width / 2 - left;
        var centroY = r.top + r.height / 2 - top;
        var tope = function (v, max) { return Math.min(Math.max(v, 18), max - 18); };

        if (lado === 'abajo') { f.style.top = '-6px'; f.style.left = tope(centroX, gw) - 7 + 'px'; }
        else if (lado === 'arriba') { f.style.top = (gh - 7) + 'px'; f.style.left = tope(centroX, gw) - 7 + 'px'; }
        else if (lado === 'derecha') { f.style.left = '-6px'; f.style.top = tope(centroY, gh) - 7 + 'px'; }
        else { f.style.left = (gw - 7) + 'px'; f.style.top = tope(centroY, gh) - 7 + 'px'; }
    }

    function centrar() {
        dom.globo.classList.add('is-centrado');
    }

    // --------------------------------------------------------------------- //
    // Recorrido de los pasos                                                  //
    // --------------------------------------------------------------------- //

    function pintarPaso(paso, indice, total) {
        dom.titulo.textContent = paso.titulo || '';
        dom.num.textContent = (indice + 1) + ' / ' + total;
        var extra = paso.interactivo
            ? '<div class="tour-hacelo"><i class="bi bi-hand-index-thumb"></i>' +
              '<span>' + (paso.hacelo || 'Probalo vos: el tutorial sigue cuando lo hagas.') + '</span></div>'
            : '';
        dom.cuerpo.innerHTML = (paso.texto || '') + extra;
        dom.cuerpo.scrollTop = 0;
        dom.relleno.style.width = Math.round(((indice + 1) / total) * 100) + '%';
        dom.anterior.style.display = indice === 0 ? 'none' : '';

        var ultimo = indice === total - 1;
        var siguientePantalla = ultimo && recorridoActivo() ? siguienteDelRecorrido(st.id) : null;
        if (siguientePantalla) {
            dom.siguiente.innerHTML = 'Seguir en ' + escapar(siguientePantalla.label) +
                                      ' <i class="bi bi-arrow-right"></i>';
        } else {
            dom.siguiente.textContent = ultimo ? 'Terminar' : 'Siguiente';
        }
        st.siguientePantalla = siguientePantalla;
    }

    function escapar(t) {
        var d = document.createElement('div');
        d.textContent = t == null ? '' : String(t);
        return d.innerHTML;
    }

    function limpiarEspera() {
        if (st && st.espera) { clearInterval(st.espera); st.espera = null; }
        if (st && st.avanceEl && st.avanceFn) {
            st.avanceEl.removeEventListener('click', st.avanceFn);
            st.avanceEl.removeEventListener('change', st.avanceFn);
            st.avanceEl = null;
            st.avanceFn = null;
        }
    }

    function mostrar(indice, direccion) {
        if (!st) { return; }
        limpiarEspera();
        var total = st.pasos.length;
        if (indice < 0) { indice = 0; }
        if (indice >= total) { return terminar(true); }

        var paso = st.pasos[indice];
        st.i = indice;

        // "antes" prepara la pantalla: abre un panel colapsado, cambia de pestaña.
        // Nunca dispara acciones que cuesten plata ni que naveguen.
        if (typeof paso.antes === 'function') {
            try { paso.antes(); } catch (e) { /* un paso roto no frena el tutorial */ }
        }

        esperarPor(paso.el, paso.esperar || 0).then(function (el) {
            if (!st || st.i !== indice) { return; }   // se movió mientras esperábamos

            if (paso.el && !visible(el)) {
                // El control no está: o el usuario no tiene ese permiso, o todavía
                // no cargó los datos. Se explica igual (centrado) o se saltea.
                if (paso.siFalta === 'centrar') { return dibujarCentrado(paso, indice, total); }
                return mostrar(indice + (direccion < 0 ? -1 : 1), direccion);
            }

            if (!paso.el) { return dibujarCentrado(paso, indice, total); }

            // El control tal cual lo declara el paso. Se guarda aparte del que se
            // resalta porque son distintos: select2 dibuja una caja al lado del
            // <select>, y el 'change' lo sigue tirando el <select> original.
            var original = el;

            // `trepar` resalta el recuadro que CONTIENE al control en vez del control
            // pelado: un checkbox suelto resaltado no se entiende, el bloque sí.
            if (paso.trepar) {
                var cont = el.closest(paso.trepar);
                if (cont && visible(cont)) { el = cont; }
            }

            // `bajar` es al revés: el paso apunta a un contenedor que SIEMPRE está en
            // el DOM (así sobrevive al filtro del arranque) pero resalta algo de
            // adentro que recién existe más tarde — una fila de la tabla, por ejemplo.
            if (paso.bajar) {
                var hijo = el.querySelector(paso.bajar);
                if (hijo && visible(hijo)) { el = hijo; }
            }

            acercar(el).then(function () {
                if (!st || st.i !== indice) { return; }
                st.elActual = el;
                st.pasoActual = paso;
                dibujarResaltado(el, paso, indice, total);
                engancharAvance(el, original, paso, indice);
            });
        });
    }

    function dibujarCentrado(paso, indice, total) {
        st.elActual = null;
        st.pasoActual = paso;
        pintarTodo();
        pintarPaso(paso, indice, total);
        centrar();
        dom.siguiente.focus({ preventScroll: true });
    }

    function dibujarResaltado(el, paso, indice, total) {
        pintarPaso(paso, indice, total);
        reubicar();
        dom.siguiente.focus({ preventScroll: true });
    }

    /* Repinta hueco y globo con la posición actual del control: se llama al scrollear,
       al cambiar el tamaño de la ventana y cuando el control cambia de tamaño. */
    function reubicar() {
        if (!st || !st.pasoActual) { return; }
        // Con un modal de Bootstrap abierto (el detalle de una auditoría, el
        // historial de una plantilla) el tutorial se corre de la escena: apaga la
        // oscuridad y deja el globo centrado, arriba del modal. Si no, el modal
        // quedaría atrás de los paneles y parecería roto.
        if (document.body.classList.contains('modal-open')) {
            ocultarCapa();
            centrar();
            return;
        }
        if (!st.elActual) { pintarTodo(); centrar(); return; }
        if (!visible(st.elActual)) { return; }
        // Se saca "centrado" ANTES de medir: centrado el globo es más ancho y por lo
        // tanto más bajo, y recorte() necesita su alto real para dejarle lugar.
        dom.globo.classList.remove('is-centrado');
        var r = recorte(st.elActual.getBoundingClientRect(), st.elActual);
        // El control quedó fuera de la pantalla (la persona scrolleó lejos): en vez
        // de dibujar un resaltado en la nada, se deja el texto centrado.
        if (r.width < 8 || r.height < 8) { pintarTodo(); centrar(); return; }
        pintarHueco(r, st.pasoActual.interactivo);
        ubicar(r, st.pasoActual.lado);
    }

    /* Pasos interactivos: el tutorial sigue solo cuando la persona hizo lo que se le
       pidió. El botón Siguiente queda igual disponible para el que prefiere mirar.

       Tres formas de detectarlo, de la más precisa a la más floja:
         avanzarCuando  función que devuelve true cuando ya está (un select con
                        valor, los KPI cargados). Es la que se usa casi siempre:
                        no confunde "abrió el desplegable" con "eligió algo".
         avanzarAlVer   un selector que aparece en pantalla (un panel que se abre).
         (por defecto)  un clic o un change sobre el propio control.
       Las dos primeras se miran con un intervalo: los cambios de estos formularios
       vienen de jQuery/select2 y de respuestas del backend, no de un evento propio. */
    function engancharAvance(el, original, paso, indice) {
        if (!paso.interactivo) { return; }

        var seguir = function () {
            if (!st || st.i !== indice) { return; }
            limpiarEspera();
            // Un respiro para que se vea lo que acaba de pasar (la lista que se
            // llenó, el panel que se abrió) antes de saltar al paso siguiente.
            setTimeout(function () { if (st && st.i === indice) { avanzar(1); } }, 700);
        };

        if (paso.avanzarCuando || paso.avanzarAlVer) {
            var listo = paso.avanzarCuando || function () {
                return visible(buscar(paso.avanzarAlVer));
            };
            // Si la condición ya se cumple al llegar (el usuario volvió atrás, o el
            // campo venía completo), no se avanza solo: sería un paso que pasa de
            // largo sin que se llegue a leer.
            var yaEstaba = false;
            try { yaEstaba = !!listo(); } catch (e) { yaEstaba = false; }
            st.espera = setInterval(function () {
                if (!st || st.i !== indice) { return limpiarEspera(); }
                var ok = false;
                try { ok = !!listo(); } catch (e) { ok = false; }
                if (!ok) { yaEstaba = false; return; }
                if (yaEstaba) { return; }
                seguir();
            }, 250);
            return;
        }

        st.avanceEl = original;
        st.avanceFn = seguir;
        original.addEventListener('click', seguir);
        original.addEventListener('change', seguir);
    }

    function avanzar(paso) {
        if (!st) { return; }
        if (paso > 0 && st.i === st.pasos.length - 1) {
            if (st.siguientePantalla) { return irA(st.siguientePantalla); }
            return terminar(true);
        }
        mostrar(st.i + paso, paso);
    }

    function irA(destino) {
        marcarVisto(st.id);
        setRecorrido(true);
        var url = destino.url + (destino.url.indexOf('?') >= 0 ? '&' : '?') + 'tutorial=1';
        window.location.href = url;
    }

    function terminar(completo) {
        if (!st) { return; }
        limpiarEspera();
        sacarCandado();
        clearTimeout(st.avisoTimer);
        document.body.classList.remove('tour-activo');
        marcarVisto(st.id);
        // Se llega acá tanto al terminar la última pantalla como al salir a mitad de
        // camino: en los dos casos el recorrido encadenado se da por cerrado (cuando
        // hay pantalla siguiente no se pasa por acá, se navega con irA()).
        setRecorrido(false);
        ocultarCapa();
        dom.capa.style.display = 'none';
        dom.globo.style.display = 'none';
        dom.aviso.hidden = true;
        document.removeEventListener('keydown', alTeclado, true);
        window.removeEventListener('resize', reubicar);
        window.removeEventListener('scroll', reubicar, true);
        if (st.observador) { st.observador.disconnect(); }
        var fab = document.getElementById('tour-fab');
        if (fab) { fab.hidden = false; }
        st = null;
    }

    function alTeclado(ev) {
        if (!st) { return; }
        if (ev.key === 'Escape') { ev.preventDefault(); terminar(false); }
        else if (ev.key === 'ArrowRight') { ev.preventDefault(); avanzar(1); }
        else if (ev.key === 'ArrowLeft') { ev.preventDefault(); avanzar(-1); }
    }

    // --------------------------------------------------------------------- //
    // API pública                                                             //
    // --------------------------------------------------------------------- //

    function registrar(id, def) { registro[id] = def; }

    function hay(id) { return !!registro[id]; }

    function iniciar(id, opciones) {
        var def = registro[id];
        if (!def || !def.pasos || !def.pasos.length) { return false; }
        if (st) { terminar(false); }
        opciones = opciones || {};
        if (opciones.recorrido) { setRecorrido(true); }

        // Qué pasos le tocan a ESTA persona se decide ACÁ, antes de empezar, para
        // que el contador no diga "3 / 14" y salte a "5 / 14" salteando en el camino.
        // Dos criterios:
        //   - `si`: una condición que solo sabe el servidor (utils/tutorial_config.py).
        //     Se usa para lo que no se puede deducir del DOM, como el cupo mensual:
        //     el aviso recién aparece al elegir campaña, o sea nunca durante el
        //     tutorial, así que mirar si está en pantalla daría siempre que no.
        //   - el control no existe en el DOM: es lo que resuelve los permisos que sí
        //     se ven (sin audit:sync no está el botón "Auditar ahora"; con permiso de
        //     edición no está el cartel de "solo lectura").
        var flags = CFG.flags || {};
        var pasos = def.pasos.filter(function (p) {
            if (p.si) { return !!flags[p.si]; }
            if (!p.el) { return true; }
            try { return document.querySelector(p.el) !== null; }
            catch (e) { return false; }
        });
        if (!pasos.length) { return false; }

        crearDom();
        dom.capa.style.display = '';
        dom.globo.style.display = '';
        dom.aviso.hidden = true;
        // Marca en el <body>: la usa el CSS para levantar por arriba de la oscuridad
        // los desplegables de select2 y los calendarios, que si no se ven apagados.
        document.body.classList.add('tour-activo');
        var fab = document.getElementById('tour-fab');
        if (fab) { fab.hidden = true; }

        st = { id: id, pasos: pasos, i: 0, elActual: null, pasoActual: null };
        ponerCandado(def.bloquear, def.bloquearSubmit);

        document.addEventListener('keydown', alTeclado, true);
        window.addEventListener('resize', reubicar);
        window.addEventListener('scroll', reubicar, true);
        // El contenido de estas pantallas se dibuja solo (tablas que crecen, gráficos
        // que cargan): si el control resaltado se mueve, el resaltado lo sigue.
        if (window.ResizeObserver) {
            st.observador = new ResizeObserver(function () { reubicar(); });
            st.observador.observe(document.body);
        }

        mostrar(0, 1);
        return true;
    }

    window.AcmeTutorial = { registrar: registrar, iniciar: iniciar, hay: hay, config: CFG };

    // --------------------------------------------------------------------- //
    // Arranque                                                                //
    // --------------------------------------------------------------------- //

    function arrancar() {
        var id = CFG.actual;
        if (!id || !hay(id)) { return; }

        var params = new URLSearchParams(location.search);
        var pedido = params.get('tutorial');

        // Se saca el parámetro de la URL: si no, recargar o compartir el link vuelve
        // a lanzar el tutorial.
        if (pedido !== null) {
            params.delete('tutorial');
            var limpio = location.pathname + (params.toString() ? '?' + params.toString() : '') + location.hash;
            try { history.replaceState(null, '', limpio); } catch (e) { /* da igual */ }
        }

        if (pedido === '1' || pedido === 'recorrido') {
            return iniciar(id, { recorrido: pedido === 'recorrido' });
        }
        // Arranque automático: solo la primera vez que la persona entra a la pantalla.
        if (!yaLoVio(id)) {
            // Un respiro para que la pantalla termine de cargar sus datos.
            setTimeout(function () { iniciar(id); }, 900);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var fab = document.getElementById('tour-fab');
        if (fab) {
            fab.addEventListener('click', function () { iniciar(CFG.actual); });
            fab.hidden = !CFG.actual || !hay(CFG.actual);
        }
        document.querySelectorAll('[data-tutorial]').forEach(function (b) {
            b.addEventListener('click', function (ev) {
                var destino = b.getAttribute('data-tutorial');
                if (destino === 'recorrido') {
                    var primera = (CFG.recorrido || [])[0];
                    if (!primera) { return; }
                    ev.preventDefault();
                    if (primera.id === CFG.actual) { return iniciar(primera.id, { recorrido: true }); }
                    setRecorrido(true);
                    location.href = primera.url + (primera.url.indexOf('?') >= 0 ? '&' : '?') + 'tutorial=1';
                    return;
                }
                if (hay(destino || CFG.actual)) {
                    ev.preventDefault();
                    iniciar(destino || CFG.actual);
                }
            });
        });
        arrancar();
    });
})();
