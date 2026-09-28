/*
 * Coral: la burbuja de ayuda que está en todas las pantallas.
 *
 * Manda la pregunta a /documentacion/asistente (Flask), que le agrega el manual del
 * usuario y se lo pasa al backend. La respuesta llega en streaming y se va pintando
 * como markdown (marked + DOMPurify, ya cargados en layout.html).
 *
 * El historial vive solo acá, en memoria: es ayuda de paso, no una conversación que
 * haya que persistir. Cambiar de pantalla arranca de cero, que es justamente lo que
 * se quiere cuando la duda es sobre otra cosa.
 */
document.addEventListener('DOMContentLoaded', function () {
    const fab = document.getElementById('asistente-fab');
    const panel = document.getElementById('asistente-panel');
    if (!fab || !panel) return; // la burbuja no se renderiza sin sesión

    const mensajes = document.getElementById('asistente-mensajes');
    const vacio = document.getElementById('asistente-vacio');
    const form = document.getElementById('asistente-form');
    const input = document.getElementById('asistente-input');
    const enviar = document.getElementById('asistente-enviar');
    const cerrar = document.getElementById('asistente-cerrar');
    const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';

    const MAX_TURNOS = 6;
    let historial = [];      // [{rol: 'user'|'bot', texto}], los últimos MAX_TURNOS
    let enCurso = false;

    // --- utilidades -------------------------------------------------------

    function pantallaActual() {
        // El título de la pestaña ya trae el nombre de la pantalla ("Plantillas -
        // Acme Solutions"); la ruta desambigua cuando dos pantallas se llaman igual.
        const titulo = (document.title || '').split(' - ')[0].trim();
        return `${titulo || 'Inicio'} (${window.location.pathname})`;
    }

    function scrollAlFinal() {
        mensajes.scrollTop = mensajes.scrollHeight;
    }

    function burbuja(rol) {
        if (vacio) vacio.style.display = 'none';
        const div = document.createElement('div');
        div.className = 'asistente-msg ' + (rol === 'user' ? 'asistente-msg-user' : 'asistente-msg-bot');
        mensajes.appendChild(div);
        scrollAlFinal();
        return div;
    }

    function pintarMarkdown(elemento, texto) {
        // Los links del asistente apuntan al manual (/documentacion#seccion), así que
        // se dejan pasar tal cual; DOMPurify se encarga de todo lo demás.
        elemento.innerHTML = DOMPurify.sanitize(marked.parse(texto));
    }

    function mostrarError(elemento, texto) {
        elemento.classList.add('asistente-error');
        elemento.textContent = texto;
    }

    // --- abrir / cerrar ---------------------------------------------------

    // El botón muestra a Coral cerrado y una cruz abierto; el intercambio lo hace el
    // CSS con la clase is-open (los dos íconos ya están en el DOM).
    function abrir() {
        panel.hidden = false;
        fab.classList.add('is-open');
        fab.setAttribute('aria-expanded', 'true');
        fab.title = 'Cerrar el asistente';
        input.focus();
        scrollAlFinal();
    }

    function cerrarPanel() {
        panel.hidden = true;
        fab.classList.remove('is-open');
        fab.setAttribute('aria-expanded', 'false');
        fab.title = 'Preguntarle a Coral cómo se usa el sistema';
    }

    fab.addEventListener('click', function () {
        panel.hidden ? abrir() : cerrarPanel();
    });
    cerrar.addEventListener('click', cerrarPanel);

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !panel.hidden) {
            cerrarPanel();
            fab.focus();
        }
    });

    mensajes.addEventListener('click', function (e) {
        const chip = e.target.closest('.asistente-chip');
        if (chip) preguntar(chip.dataset.pregunta || chip.textContent.trim());
    });

    // Enter envía, Shift+Enter hace salto de línea (lo esperable en un chat).
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
        }
    });

    // El textarea crece con el texto hasta el tope que fija el CSS.
    input.addEventListener('input', function () {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 112) + 'px';
    });

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        preguntar(input.value);
    });

    // --- consulta ---------------------------------------------------------

    async function preguntar(pregunta) {
        pregunta = (pregunta || '').trim();
        if (!pregunta || enCurso) return;

        enCurso = true;
        enviar.disabled = true;
        input.value = '';
        input.style.height = 'auto';

        burbuja('user').textContent = pregunta;

        const respuestaEl = burbuja('bot');
        respuestaEl.innerHTML = '<span class="asistente-pensando"><span></span><span></span><span></span></span>';

        let texto = '';
        try {
            const respuesta = await fetch('/documentacion/asistente', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({
                    pregunta: pregunta,
                    pantalla: pantallaActual(),
                    historial: historial.slice(-MAX_TURNOS)
                })
            });

            if (!respuesta.ok) {
                let detalle = 'No pudimos responder tu consulta. Probá de nuevo en un momento.';
                try {
                    const error = await respuesta.json();
                    if (error && error.detail) detalle = error.detail;
                } catch (_) { /* la respuesta no era JSON: queda el mensaje genérico */ }
                if (respuesta.status === 401) {
                    detalle = 'Tu sesión venció. Volvé a iniciar sesión para seguir preguntando.';
                }
                mostrarError(respuestaEl, detalle);
                return;
            }

            const lector = respuesta.body.getReader();
            const decoder = new TextDecoder('utf-8');
            while (true) {
                const { done, value } = await lector.read();
                if (done) break;
                texto += decoder.decode(value, { stream: true });
                pintarMarkdown(respuestaEl, texto);
                scrollAlFinal();
            }
            texto += decoder.decode();
            pintarMarkdown(respuestaEl, texto);

            if (!texto.trim()) {
                mostrarError(respuestaEl, 'El asistente no devolvió respuesta. Probá reformulando la pregunta.');
                return;
            }

            historial.push({ rol: 'user', texto: pregunta });
            historial.push({ rol: 'bot', texto: texto });
            historial = historial.slice(-MAX_TURNOS);
        } catch (error) {
            mostrarError(respuestaEl, 'Se cortó la conexión con el asistente. Probá de nuevo.');
        } finally {
            enCurso = false;
            enviar.disabled = false;
            scrollAlFinal();
        }
    }
});
