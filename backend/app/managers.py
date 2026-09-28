import logging
from typing import Optional
from app.database import engine
from AuditorIA.Plantillas_prompts import plantillas_manager

plantillas_manager_instance: Optional[plantillas_manager] = None
try:
    if engine:
        plantillas_manager_instance = plantillas_manager(engine=engine)
        logging.getLogger('PlantillasManager').info("PlantillasManager initialized successfully via Api.py.")
    else:
        logging.getLogger('PlantillasManager').error("Database engine not initialized, cannot initialize PlantillasManager.")
        plantillas_manager_instance = None
except Exception as e:
    logging.getLogger('PlantillasManager').exception(f"Failed to initialize PlantillasManager: {e}. PlantillasManager service will be unavailable.")
