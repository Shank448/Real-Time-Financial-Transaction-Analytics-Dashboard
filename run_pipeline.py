#!/usr/bin/env python3
"""End-to-end pipeline:  generate+load -> clean -> SQL marts -> CSV export (-> preview).

    python run_pipeline.py                       # everything
    python run_pipeline.py --rows 200000         # small, fast dev run
    python run_pipeline.py --only marts,export   # refresh analytics only (what cron would run)
"""
import argparse
import logging
import time

from txn_analytics import config as C

STAGES = ["load", "clean", "marts", "export", "preview"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=C.DEFAULT_ROWS, help="raw rows to generate")
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--only", default=",".join(STAGES), help=f"comma list from {STAGES}")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("pipeline")
    todo = [s.strip() for s in args.only.split(",")]

    t_all = time.time()
    if "load" in todo:
        from txn_analytics.load_raw import load_raw
        log.info("[1/5] generate + load raw data"); load_raw(args.rows, args.seed)
    if "clean" in todo:
        from txn_analytics.clean import run_clean
        log.info("[2/5] extract + clean with Pandas"); res = run_clean()
        log.info("      %s -> %s rows", f"{res['rows_in']:,}", f"{res['rows_out']:,}")
    if "marts" in todo:
        from txn_analytics.marts import run_marts
        log.info("[3/5] build SQL marts"); run_marts()
    if "export" in todo:
        from txn_analytics.export import export_marts
        log.info("[4/5] export CSVs for Tableau"); export_marts()
    if "preview" in todo:
        from txn_analytics.preview import make_preview
        log.info("[5/5] sanity-check preview -> %s", make_preview())
    log.info("done in %.0fs", time.time() - t_all)


if __name__ == "__main__":
    main()
