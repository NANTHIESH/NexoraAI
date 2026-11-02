"""Distil a real-world bank dataset into a tiny statistical profile (no rows are copied).

Run once: python -m scripts.profile_bank --src "<path>/trans.asc"
Reads the public PKDD'99 (Berka) `trans.asc` and writes data/reference/bank_profile.json:
the shape of small outgoing payments (amount quantiles, as ratios to the median) and the
day-of-month pattern. scripts/generate_data.py uses this profile to make its background
transactions look realistic. The profile is committed, so generating data needs no download.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from nexora.config import PROJECT_ROOT

PROFILE_PATH = PROJECT_ROOT / "data" / "reference" / "bank_profile.json"
OUTGOING_TYPES = ("VYDAJ", "VYBER")  # Czech: withdrawal / cash withdrawal
LOAN_SYMBOL = "UVER"  # loan repayments are not "small payments"


def build_profile(trans_path: Path) -> dict:
    """Compute the profile from a Berka ``trans.asc`` file."""
    df = pd.read_csv(trans_path, sep=";", usecols=["date", "type", "amount", "k_symbol"],
                     keep_default_na=False)
    out = df[df["type"].isin(OUTGOING_TYPES) & (df["k_symbol"] != LOAN_SYMBOL) & (df["amount"] > 0)]
    median = float(out["amount"].median())
    quantiles = out["amount"].quantile([i / 100 for i in range(1, 100)]).to_numpy()
    days = (out["date"] % 100).clip(1, 27)
    weights = days.value_counts(normalize=True).reindex(range(1, 28), fill_value=0.0)
    return {
        "source": "PKDD'99 Berka bank dataset (public); only aggregate statistics are stored",
        "rows_used": int(len(out)),
        "amount_ratio_quantiles": [round(float(q) / median, 4) for q in quantiles],
        "day_of_month_weights": [round(float(w), 5) for w in weights],
    }


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="path to Berka trans.asc")
    args = parser.parse_args()
    profile = build_profile(args.src)
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {PROFILE_PATH} from {profile['rows_used']} outgoing transactions")


if __name__ == "__main__":
    main()
