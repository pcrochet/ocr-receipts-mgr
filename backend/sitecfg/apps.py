# backend/sitecfg/apps.py
from __future__ import annotations

import logging
from pathlib import Path
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

class SitecfgConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sitecfg"
    verbose_name = "Site configuration"

    def ready(self) -> None:
        # 1) Enregistre les system checks (si le module existe déjà)
        try:
            from . import checks  # noqa: F401
        except Exception as exc:  # pragma: no cover
            logger.warning("sitecfg.checks non chargé (facultatif) : %s", exc)

        # 2) Crée les dossiers var/* de manière idempotente
        var_dir = Path(getattr(settings, "VAR_DIR", Path(getattr(settings, "BASE_DIR")) / "var"))
        default_map = {
            "incoming": var_dir / "incoming",
            "quarantine": var_dir / "quarantine",
            "receipts_raw": var_dir / "receipts_raw",
            "logs": var_dir / "logs",
            "exports": var_dir / "exports",
            "receipts_json": var_dir / "receipts_json",
        }

        subdirs = getattr(settings, "VAR_SUBDIRS", default_map)
        # Accepte dict ou liste de chemins
        paths = subdirs.values() if isinstance(subdirs, dict) else subdirs
        for p in paths:
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # pragma: no cover
                logger.error("Impossible de créer le dossier %s : %s", p, exc)
