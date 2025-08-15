# backend/sitecfg/checks.py
from pathlib import Path
from django.conf import settings
from django.core.checks import register, Error, Warning
from django.apps import apps as django_apps

REQUIRED_APPS = {"sitecfg", "ocr", "ops"}
REQUIRED_VAR_SUBDIRS = ["incoming", "quarantine", "receipts_raw", "logs", "exports"]
OPTIONAL_VAR_SUBDIRS = ["credentials/gmail", "locks"]


@register()
def project_conventions_check(app_configs, **kwargs):
    errors = []
    warnings = []

    # 1) DB = PostgreSQL
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    if "postgresql" not in engine:
        errors.append(Error(
            "La base de données doit être PostgreSQL.",
            id="CFG.E001",
            hint="Régle DATABASES['default']['ENGINE'] = 'django.db.backends.postgresql'",
        ))

    # 2) Apps requises (via registry, pas via INSTALLED_APPS brut)
    present_apps = {cfg.name for cfg in django_apps.get_app_configs()}
    missing = REQUIRED_APPS - present_apps
    if missing:
        errors.append(Error(
            f"Apps manquantes: {', '.join(sorted(missing))}",
            id="CFG.E002",
        ))

    # 3) DEFAULT_AUTO_FIELD recommandé
    default_auto = getattr(settings, "DEFAULT_AUTO_FIELD", "")
    if default_auto != "django.db.models.BigAutoField":
        warnings.append(Warning(
            "DEFAULT_AUTO_FIELD devrait être 'django.db.models.BigAutoField'.",
            id="CFG.W003",
        ))

    # 4) Timezone / USE_TZ
    if getattr(settings, "TIME_ZONE", "") != "Europe/Paris":
        warnings.append(Warning("TIME_ZONE conseillé: 'Europe/Paris'.", id="CFG.W004"))
    if not getattr(settings, "USE_TZ", False):
        errors.append(Error("USE_TZ doit être True.", id="CFG.E005"))

    # 5) Arborescence var/*
    base_dir = Path(getattr(settings, "BASE_DIR"))
    var_dir = base_dir / "var"
    if not var_dir.exists():
        warnings.append(Warning(f"Le répertoire {var_dir} n'existe pas.", id="CFG.W006"))
    else:
        missing_dirs = [d for d in REQUIRED_VAR_SUBDIRS if not (var_dir / d).exists()]
        if missing_dirs:
            warnings.append(Warning(
                f"Sous-dossiers manquants dans var/: {', '.join(missing_dirs)}",
                id="CFG.W007",
                hint="Crée-les ou ajoute une initialisation au démarrage.",
            ))
        opt_missing = [d for d in OPTIONAL_VAR_SUBDIRS if not (var_dir / d).exists()]
        if opt_missing:
            warnings.append(Warning(
                f"Dossiers recommandés absents dans var/: {', '.join(opt_missing)}",
                id="CFG.W009",
            ))

    # 6) TEMPLATES minimal
    templates = getattr(settings, "TEMPLATES", [])
    if not templates or not templates[0].get("DIRS"):
        warnings.append(Warning(
            "TEMPLATES.DIRS est vide ; vérifie les chemins des templates admin personnalisés.",
            id="CFG.W008",
        ))

    # 7) Vérifs spécifiques Gmail si activé
    gmail_cfg = getattr(settings, "OPS_GMAIL_COLLECT", {})
    if gmail_cfg.get("ENABLED"):
        cred_dir = gmail_cfg.get("CREDENTIALS_DIR")
        if not cred_dir or not Path(cred_dir).exists():
            errors.append(Error(
                "OPS_GMAIL_COLLECT.ENABLED=True mais le dossier credentials Gmail est introuvable.",
                id="CFG.E010",
                hint="Crée var/credentials/gmail/ et place-y client_secret.json",
            ))
        else:
            client_secret = Path(cred_dir) / "client_secret.json"
            if not client_secret.exists():
                warnings.append(Warning(
                    "client_secret.json manquant pour la collecte Gmail.",
                    id="CFG.W011",
                    hint="Télécharge le secret OAuth (Desktop app) et place-le ici.",
                ))
        if not gmail_cfg.get("ALLOWED_MIME_TYPES"):
            warnings.append(Warning("ALLOWED_MIME_TYPES est vide pour Gmail.", id="CFG.W012"))
        if gmail_cfg.get("MAX_SIZE_BYTES", 0) <= 0:
            warnings.append(Warning("MAX_SIZE_BYTES devrait être > 0 pour Gmail.", id="CFG.W013"))

    return errors + warnings
