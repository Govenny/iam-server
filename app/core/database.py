import os
import time
import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.core.config import settings

logger = logging.getLogger("iam-service.database")

engine = create_engine(
    settings.MYSQL_DSN,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_sql_file(file_path: str, max_retries: int = 15, retry_interval: float = 2.0):
    if not os.path.exists(file_path):
        logger.warning(f"SQL file not found: {file_path}")
        return

    logger.info(f"Executing SQL file: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # Split SQL by semicolons, filtering comments and empty statements
    statements = []
    current_statement = []
    for line in sql_content.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("--") or trimmed.startswith("/*"):
            continue
        current_statement.append(line)
        if trimmed.endswith(";"):
            statement_str = "\n".join(current_statement).strip()
            if statement_str:
                statements.append(statement_str)
            current_statement = []

    if current_statement:
        tail = "\n".join(current_statement).strip()
        if tail:
            statements.append(tail)

    for attempt in range(1, max_retries + 1):
        try:
            with engine.connect() as conn:
                for stmt in statements:
                    stmt_clean = stmt.strip()
                    if not stmt_clean:
                        continue
                    # Skip standalone USE statement as DB is typically specified in DSN
                    if stmt_clean.upper().startswith("USE "):
                        continue
                    conn.execute(text(stmt_clean))
                conn.commit()
            logger.info(f"Successfully executed DDL from {file_path}")
            return
        except Exception as e:
            logger.warning(f"Database connection/DDL execution attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                logger.error(f"Failed to execute SQL file after {max_retries} attempts: {e}")
                raise
            time.sleep(retry_interval)


def init_db():
    """Idempotently execute phase 1 (and phase 2 if available) core DDL on startup"""
    sql_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sql")
    phase1_sql = os.path.join(sql_dir, "01_phase1_core.sql")
    phase2_sql = os.path.join(sql_dir, "02_phase2_ext.sql")

    run_sql_file(phase1_sql)
    if os.path.exists(phase2_sql):
        run_sql_file(phase2_sql)
