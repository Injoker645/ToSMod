"""ToSMod API routes: config, dataset import, connectors, training."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from flask import Blueprint, jsonify, request

from tosmod.config.loader import get_config
from tosmod.connectors.registry import get_registry
from tosmod.import_.engine import ImportEngine
from tosmod.paths import PROJECT_ROOT

bp = Blueprint("tosmod", __name__)


def _get_conn():
    from dashboard.app import get_db
    conn = get_db()
    if conn is None:
        db_path = get_config().db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        from thesis_scraper.storage.database import init_schema
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        conn.commit()
    return conn


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _env_path() -> Path:
    return _project_root() / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    current = _read_env_file(path)
    current.update(values)
    lines = ["# Local ToSMod settings", ""]
    for key in sorted(current):
        lines.append(f"{key}={current[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 6:
        return "*" * len(secret)
    return f"{secret[:3]}***{secret[-3:]}"


def _settings_status() -> dict[str, object]:
    cfg = get_config()
    env_vals = _read_env_file(_env_path())
    db_path = cfg.db_path()
    env_exists = _env_path().exists()
    db_exists = db_path.exists()
    db_counts = {"posts": 0, "comments": 0, "annotations": 0}
    if db_exists:
        conn = _get_conn()
        try:
            for t in ("posts", "comments", "annotations"):
                try:
                    db_counts[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                except sqlite3.OperationalError:
                    db_counts[t] = 0
        finally:
            conn.close()

    connectors = get_registry().list_connectors(opt_in_enabled=True)
    required = {
        "ANONYMIZATION_SALT": bool(
            os.environ.get("ANONYMIZATION_SALT")
            or env_vals.get("ANONYMIZATION_SALT")
        ),
        "YOUTUBE_API_KEY": bool(os.environ.get("YOUTUBE_API_KEY") or env_vals.get("YOUTUBE_API_KEY")),
        "APIFY_API_KEY": bool(os.environ.get("APIFY_API_KEY") or env_vals.get("APIFY_API_KEY")),
    }
    warnings: list[str] = []
    if not env_exists:
        warnings.append(".env file missing (copy .env.example or save from Settings)")
    if not required["ANONYMIZATION_SALT"]:
        warnings.append("ANONYMIZATION_SALT missing")
    if not db_exists:
        warnings.append("Database file not found yet (seed demo or import data)")
    if db_exists and db_counts["comments"] == 0:
        warnings.append("Database exists but has no comments yet")

    return {
        "env_file_present": env_exists,
        "db_path": str(db_path),
        "db_exists": db_exists,
        "db_counts": db_counts,
        "required_ready": required,
        "connectors": connectors,
        "warnings": warnings,
        "saved_keys": {
            "YOUTUBE_API_KEY": _mask(env_vals.get("YOUTUBE_API_KEY", "")),
            "APIFY_API_KEY": _mask(env_vals.get("APIFY_API_KEY", "")),
            "TIKTOK_RESEARCH_CLIENT_KEY": _mask(env_vals.get("TIKTOK_RESEARCH_CLIENT_KEY", "")),
            "REDDIT_CLIENT_ID": _mask(env_vals.get("REDDIT_CLIENT_ID", "")),
            "ANONYMIZATION_SALT": _mask(env_vals.get("ANONYMIZATION_SALT", "")),
        },
    }


@bp.route("/api/config/taxonomy")
def api_config_taxonomy():
    cfg = get_config()
    return jsonify({
        "labels": cfg.taxonomy.get("labels", []),
        "label_names": cfg.label_names(),
        "modalities": cfg.modalities(),
        "label_to_id": cfg.label_to_id(),
    })


@bp.route("/api/config/platforms")
def api_config_platforms():
    cfg = get_config()
    return jsonify(cfg.platforms)


@bp.route("/api/config/tos/<platform>")
def api_config_tos(platform: str):
    guide = get_config().tos_guide(platform)
    if not guide:
        return jsonify({"error": "no guide for platform", "platform": platform}), 404
    return jsonify({"platform": platform.lower(), **guide})


@bp.route("/api/config/tos")
def api_config_tos_list():
    return jsonify({"platforms": get_config().list_tos_platforms()})


@bp.route("/api/settings/status")
def api_settings_status():
    return jsonify(_settings_status())


@bp.route("/api/settings/save", methods=["POST"])
def api_settings_save():
    data = request.get_json(force=True) or {}
    allowed = {
        "TOSMOD_DB_PATH",
        "ANONYMIZATION_SALT",
        "YOUTUBE_API_KEY",
        "APIFY_API_KEY",
        "TIKTOK_RESEARCH_CLIENT_KEY",
        "TIKTOK_RESEARCH_CLIENT_SECRET",
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USER_AGENT",
        "TOSMOD_ENABLE_OPT_IN",
        "OLLAMA_BASE_URL",
    }
    values: dict[str, str] = {}
    for k, v in data.items():
        if k not in allowed:
            continue
        if v is None:
            continue
        values[k] = str(v).strip()
    if not values:
        return jsonify({"error": "No valid settings provided"}), 400
    _write_env_file(_env_path(), values)
    for k, v in values.items():
        os.environ[k] = v
    return jsonify({"ok": True, "saved": sorted(values.keys()), "status": _settings_status()})


@bp.route("/api/settings/verify", methods=["POST"])
def api_settings_verify():
    status = _settings_status()
    checks: list[dict[str, object]] = []
    for c in status["connectors"]:
        missing = c.get("missing_env") or []
        checks.append(
            {
                "id": c.get("id"),
                "display_name": c.get("display_name"),
                "tier": c.get("tier"),
                "ok": bool(c.get("available")),
                "missing_env": missing,
            }
        )
    apify_importable = True
    apify_msg = ""
    try:
        import apify_client  # noqa: F401
    except Exception:
        apify_importable = False
        apify_msg = "apify-client not installed"
    return jsonify(
        {
            "ok": True,
            "checks": checks,
            "apify_client_installed": apify_importable,
            "apify_hint": apify_msg,
        }
    )


@bp.route("/api/settings/seed-demo", methods=["POST"])
def api_settings_seed_demo():
    from tosmod.seed import seed_demo_db

    db_override = request.get_json(silent=True) or {}
    raw_db = (db_override.get("db_path") or "").strip()
    seed_demo_db(Path(raw_db) if raw_db else None)
    return jsonify({"ok": True, "status": _settings_status()})


@bp.route("/api/connectors")
def api_connectors():
    tier = request.args.get("tier")
    opt_in = os.environ.get("TOSMOD_ENABLE_OPT_IN", "") == "1"
    ack = request.args.get("opt_in_ack") == "1"
    items = get_registry().list_connectors(
        tier=tier,
        opt_in_enabled=opt_in or ack,
    )
    return jsonify({"connectors": items, "opt_in_enabled": opt_in or ack})


@bp.route("/api/dataset/import-csv", methods=["POST"])
def api_dataset_import_csv():
    """Fixed-schema CSV import (canonical ToSMod columns)."""
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    f = request.files["file"]
    suffix = Path(f.filename or "upload.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        f.save(tmp.name)
        tmp_path = Path(tmp.name)
    profile = {
        "format": "csv",
        "platform_default": request.form.get("platform_default", "custom"),
        "field_map": {
            "platform": "platform",
            "post_id": "post_id",
            "comment_id": "comment_id",
            "text": "text",
            "label": "label",
            "severity": "severity",
            "modality": "modality",
            "collection_stratum": "collection_stratum",
            "search_query": "search_query",
            "post_title": "post_title",
            "channel_name": "channel_name",
            "url": "url",
            "uncertain": "uncertain",
            "gif_context": "gif_context",
            "has_gif": "has_gif",
        },
    }
    conn = _get_conn()
    try:
        result = ImportEngine().import_file(conn, tmp_path, profile)
        return jsonify({
            "ok": True,
            "rows_read": result.rows_read,
            "comments_upserted": result.comments_upserted,
            "posts_upserted": result.posts_upserted,
            "annotations_upserted": result.annotations_upserted,
            "errors": result.errors[:50],
        })
    finally:
        conn.close()
        tmp_path.unlink(missing_ok=True)


@bp.route("/api/import/profiles", methods=["GET", "POST"])
def api_import_profiles():
    cfg = get_config()
    profiles_dir = cfg.config_dir / "import_profiles"
    if request.method == "GET":
        names = sorted(p.stem for p in profiles_dir.glob("*.yaml")) if profiles_dir.exists() else []
        return jsonify({"profiles": names})
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    profile = data.get("profile")
    if not name or not isinstance(profile, dict):
        return jsonify({"error": "name and profile required"}), 400
    profiles_dir.mkdir(parents=True, exist_ok=True)
    path = profiles_dir / f"{name}.yaml"
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, allow_unicode=True)
    return jsonify({"ok": True, "path": str(path)})


@bp.route("/api/import/preview", methods=["POST"])
def api_import_preview():
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    profile_name = request.form.get("profile", "tosmod_canonical")
    cfg = get_config()
    profile = cfg.import_profile(profile_name)
    if not profile:
        return jsonify({"error": f"profile not found: {profile_name}"}), 404
    f = request.files["file"]
    suffix = Path(f.filename or "upload.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        f.save(tmp.name)
        tmp_path = Path(tmp.name)
    try:
        rows = ImportEngine().preview(tmp_path, profile, limit=5)
        return jsonify({"preview": rows, "profile": profile_name})
    finally:
        tmp_path.unlink(missing_ok=True)


@bp.route("/api/import/validate", methods=["POST"])
def api_import_validate():
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    profile_name = request.form.get("profile", "tosmod_canonical")
    profile = get_config().import_profile(profile_name)
    if not profile:
        return jsonify({"error": f"profile not found: {profile_name}"}), 404
    f = request.files["file"]
    suffix = Path(f.filename or "upload.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        f.save(tmp.name)
        tmp_path = Path(tmp.name)
    engine = ImportEngine()
    errors: list[str] = []
    count = 0
    try:
        for row in engine.iter_rows(tmp_path, profile):
            count += 1
            errors.extend(f"row {count}: {e}" for e in engine.validate_row(row))
            if len(errors) >= 100:
                break
        return jsonify({"rows": count, "errors": errors, "valid": len(errors) == 0})
    finally:
        tmp_path.unlink(missing_ok=True)


@bp.route("/api/import/run", methods=["POST"])
def api_import_run():
    if "file" not in request.files:
        return jsonify({"error": "file required"}), 400
    profile_name = request.form.get("profile", "tosmod_canonical")
    profile = get_config().import_profile(profile_name)
    if not profile:
        return jsonify({"error": f"profile not found: {profile_name}"}), 404
    save_name = request.form.get("save_profile_as")
    if save_name:
        profiles_dir = get_config().config_dir / "import_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        with (profiles_dir / f"{save_name}.yaml").open("w", encoding="utf-8") as f:
            yaml.safe_dump(profile, f, allow_unicode=True)
    f = request.files["file"]
    suffix = Path(f.filename or "upload.csv").suffix or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        f.save(tmp.name)
        tmp_path = Path(tmp.name)
    conn = _get_conn()
    try:
        limit = request.form.get("limit")
        result = ImportEngine().import_file(
            conn, tmp_path, profile, limit=int(limit) if limit else None
        )
        return jsonify({
            "ok": True,
            "rows_read": result.rows_read,
            "comments_upserted": result.comments_upserted,
            "errors": result.errors[:50],
        })
    finally:
        conn.close()
        tmp_path.unlink(missing_ok=True)


# ── Training routes ──────────────────────────────────────────────────────────

@bp.route("/api/train/local-models")
def api_train_local_models():
    """Return list of locally fine-tuned models found in models/."""
    models_dir = PROJECT_ROOT / "models"
    found = []
    if models_dir.exists():
        for p in sorted(models_dir.iterdir()):
            if p.is_dir() and (p / "config.json").exists():
                card = p / "README.md"
                found.append({
                    "name": p.name,
                    "path": str(p.relative_to(PROJECT_ROOT)),
                    "card": str(card.relative_to(PROJECT_ROOT)) if card.exists() else None,
                })
    return jsonify({"models": found})


@bp.route("/api/train/finetune", methods=["POST"])
def api_train_finetune():
    """Launch training/finetune.py as a subprocess; returns job_id for log polling."""
    body = request.get_json(force=True, silent=True) or {}
    base_model = body.get("base_model", "hatebert")
    data_src   = body.get("data_src", "db")
    csv_path   = body.get("csv_path", "")
    output_dir = body.get("output_dir", "models/my-model")
    variant    = body.get("variant", "T+ED")
    quick      = bool(body.get("quick", True))

    finetune_script = PROJECT_ROOT / "training" / "finetune.py"
    if not finetune_script.exists():
        return jsonify({"error": "training/finetune.py not found — install training extras: pip install -e '.[training]'"}), 400

    cmd = [sys.executable, str(finetune_script),
           "--base-model", base_model,
           "--variant", variant,
           "--output-dir", str(PROJECT_ROOT / output_dir)]

    if data_src == "csv" and csv_path:
        cmd += ["--csv", csv_path]
    else:
        db_path = get_config().db_path()
        cmd += ["--db", str(db_path)]

    if quick:
        cmd.append("--quick")

    import uuid
    job_id = str(uuid.uuid4())[:8]
    log_path = PROJECT_ROOT / "data" / f"train_{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w") as lf:
        subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(PROJECT_ROOT))

    return jsonify({"job_id": job_id, "log": str(log_path.relative_to(PROJECT_ROOT))})
