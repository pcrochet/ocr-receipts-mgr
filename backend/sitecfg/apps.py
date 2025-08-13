# backend/sitecfg/apps.py
from django.apps import AppConfig
import logging
logger = logging.getLogger(__name__)

class SitecfgConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sitecfg"
    verbose_name = "Site configuration"

    def ready(self):
        pass
