import argparse
import json
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from .config import Settings
from .storage import SystemSettingRow, UserRow, make_engine, sessions
from .services.cases import CaseService, principal_from_claims
from .services.gemma_client import GemmaClient, ModelError
from .services.jobs import purge_expired


ROOT = Path(__file__).resolve().parents[1]


def migrate():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


def main():
    parser = argparse.ArgumentParser(description="Evidence Arena administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("smoke")
    commands.add_parser("purge")
    commands.add_parser("health")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--output", default="docs/evaluation-results.json")
    admin = commands.add_parser("bootstrap-admin")
    admin.add_argument("--issuer", required=True)
    admin.add_argument("--subject", required=True)
    admin.add_argument("--name", default="Administrator")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "migrate":
        migrate()
        print("Database migrations applied")
    elif args.command == "smoke":
        try:
            print(json.dumps(GemmaClient(settings).smoke(), indent=2))
        except ModelError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1)
    elif args.command == "evaluate":
        from .evaluation import evaluate
        result = evaluate()
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        if result["failed"]:
            raise SystemExit(1)
    else:
        engine = make_engine(settings.database_url)
        if args.command == "health":
            from datetime import datetime
            from .core.contracts import utcnow
            with sessions(engine)() as db:
                db.execute(select(UserRow.id).limit(1))
                heartbeat = db.get(SystemSettingRow, "worker_heartbeat")
                age = (utcnow() - datetime.fromisoformat(heartbeat.value["timestamp"])).total_seconds() if heartbeat else None
            import httpx
            try:
                base = settings.gemma_base_url.removesuffix("/v1")
                with httpx.Client(timeout=5, trust_env=False) as client:
                    response = client.get(base + "/api/tags")
                    installed = any(m.get("name") == settings.gemma_model_id for m in response.json().get("models", []))
            except Exception:
                installed = False
            result = {"database": "ready", "worker_alive": age is not None and age < 35,
                      "worker_last_seen_seconds": round(age, 1) if age is not None else None,
                      "model_installed": installed, "model": settings.gemma_model_id,
                      "notice": "Installation/liveness check; use smoke to verify actual inference"}
            print(json.dumps(result, indent=2))
            if not result["worker_alive"] or not installed:
                raise SystemExit(1)
        elif args.command == "purge":
            print(json.dumps(purge_expired(engine, settings)))
        else:
            principal = principal_from_claims({"iss": args.issuer, "sub": args.subject, "name": args.name})
            CaseService(engine, settings).register(principal)
            with sessions(engine).begin() as db:
                db.get(UserRow, principal.user_id).admin = True
                CaseService._audit(db, principal.user_id, "administrator_bootstrapped")
            print(f"Administrator: {principal.user_id}; OIDC issuer and subject must match exactly")


if __name__ == "__main__":
    main()

