import argparse
import logging
import time
from threading import Event, Thread

from .config import Settings
from .storage import SystemSettingRow, make_engine, sessions
from .core.contracts import utcnow
from .services.jobs import JobService, purge_expired


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    jobs = JobService(engine, settings)
    stop_heartbeat = Event()
    def heartbeat():
        while not stop_heartbeat.is_set():
            try:
                with sessions(engine).begin() as db:
                    db.merge(SystemSettingRow(key="worker_heartbeat", value={"timestamp": utcnow().isoformat()}))
            except Exception:
                pass
            stop_heartbeat.wait(10)
    Thread(target=heartbeat, daemon=True).start()
    last_purge = 0
    while True:
        try:
            if time.monotonic() - last_purge > 3600:
                purge_expired(engine, settings)
                last_purge = time.monotonic()
            worked = jobs.run_one()
        except KeyboardInterrupt:
            break
        except Exception:
            logging.error("Worker operation failed; retrying without logging source content")
            worked = False
        if args.once:
            break
        if not worked:
            time.sleep(2)
    stop_heartbeat.set()


if __name__ == "__main__":
    main()

