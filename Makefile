.PHONY: dev backend frontend install db-demo db-demo-reset

BACKEND_DIR  = backend
FRONTEND_DIR = frontend
# Un único venv en la raíz con un único requirements.txt: backend y frontend
# comparten entorno. Antes cada target apuntaba a $(DIR)/requirements.txt, que no
# existe en el repo, así que `make install` fallaba y no había forma reproducible
# de rearmar el entorno (ni, por lo tanto, de volver atrás un upgrade).
VENV = .venv
PY   = $(VENV)/bin/python
PIP  = $(VENV)/bin/pip

# Levanta backend y frontend en paralelo
dev:
	@trap 'kill 0' SIGINT; \
	$(MAKE) backend & \
	$(MAKE) frontend & \
	wait

backend: install
	cd $(BACKEND_DIR) && ../$(PY) main.py

frontend: install
	cd $(FRONTEND_DIR) && ../$(PY) run.py

# Rearma el entorno exactamente como está pineado (también sirve de rollback:
# `git checkout <commit> -- requirements.txt && make install`).
install:
	@[ -d $(VENV) ] || python3 -m venv $(VENV)
	@$(PIP) install -q -r requirements.txt

# Base de demostración (SQL Server en Docker + datos inventados). Ver db/README.md.
db-demo: install
	docker compose -f db/docker-compose.yml up -d --wait
	$(PY) db/crear_base_demo.py

db-demo-reset: install
	docker compose -f db/docker-compose.yml up -d --wait
	$(PY) db/crear_base_demo.py --reset
