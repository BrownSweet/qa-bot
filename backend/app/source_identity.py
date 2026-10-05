"""Connection identity for records that must not silently cross data sources."""
import json


def source_identity(config) -> dict:
    return {
        "type": config.type,
        "host": config.host,
        "port": config.port,
        "database": config.database_name,
        "username": config.username,
        "file_path": config.file_path,
    }


def source_identity_json(config) -> str:
    return json.dumps(source_identity(config), ensure_ascii=False, sort_keys=True)


def source_identity_matches(saved_json: str | None, config) -> bool:
    if not saved_json:
        return False
    try:
        return json.loads(saved_json) == source_identity(config)
    except (TypeError, ValueError):
        return False
