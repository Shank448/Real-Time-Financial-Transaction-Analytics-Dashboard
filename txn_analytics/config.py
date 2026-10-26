"""Central configuration. Every tunable lives here so SQL + Python stay in sync."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
EXPORT_DIR = DATA_DIR / "exports"
SQL_DIR = ROOT / "sql"
REPORT_DIR = ROOT / "reports"

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://fin:fin@localhost:5432/payments"
)
SEED = int(os.getenv("SEED", "42"))

# ---- synthetic data window -------------------------------------------------
DEFAULT_ROWS = 1_540_000          # raw rows incl. injected anomalies (>1.5M after cleaning)
START_DATE = "2026-01-01"
N_DAYS = 181                      # 2026-01-01 .. 2026-06-30

# ---- cleaning rules --------------------------------------------------------
VALID_START = "2025-12-31"        # timestamps outside [VALID_START, VALID_END) are anomalous
VALID_END = "2026-07-02"
MZ_THRESHOLD = 5.0                # modified z-score on log(amount) per payment method
LATENCY_MAX_MS = 30_000           # above this = instrumentation error, not a real latency

# ---- canonical dimension values -------------------------------------------
REGIONS = ["North America", "Europe", "India", "APAC", "LATAM", "MEA"]
METHODS = ["CARD", "UPI", "WALLET", "BANK_TRANSFER", "BNPL"]
CATEGORIES = ["grocery", "travel", "electronics", "food_delivery", "utilities",
              "fashion", "gaming", "subscriptions", "crypto_exchange"]
STATUSES = ["SUCCESS", "FAILED", "PENDING", "REVERSED"]

# ---- parameters injected into the SQL files ({{NAME}} placeholders) ---------
SQL_PARAMS = {
    "FRAUD_THRESHOLD": 0.70,      # fraud_probability >= this => flagged for review
    "SPIKE_Z": 5.0,               # volume z-score vs trailing 28-day baseline
    "LATENCY_BREACH_MULT": 1.75,  # route p95 > 1.75x peer-median p95 => bottleneck
    "MIN_HOURLY_N": 20,           # min measured txns for an hourly p95 to be trusted
}
