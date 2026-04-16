#!/usr/bin/env python3
"""
Jednorazowa naprawa tradingview_results_*.csv: dopisuje kolumny Scrape_Status i Scrape_Error
po Interval, tak aby wiersze zapisane ze statusem OK miały poprawne mapowanie kolumn.
"""
import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from results_store import CSV_META_COLUMNS  # noqa: E402


def repair_path(path: str) -> None:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        print("Pusty plik:", path, file=sys.stderr)
        return

    header = rows[0]
    meta_after_interval = [c for c in CSV_META_COLUMNS if c not in ("Ticker", "Company_Name", "Current_Price", "Interval")]
    if all(c in header for c in meta_after_interval):
        print("OK:", path, "— nagłówek już zawiera kolumny meta (Scrape_Status/Scrape_Error)")
        return

    try:
        idx_iv = header.index("Interval")
    except ValueError:
        print("Brak kolumny Interval w:", path, file=sys.stderr)
        return

    missing_meta = [c for c in meta_after_interval if c not in header]
    insert_at = idx_iv + 1
    new_header = header[:insert_at] + missing_meta + header[insert_at:]
    n_old = len(header)
    n_new = len(new_header)
    n_added = len(missing_meta)

    out_rows = [new_header]
    for row in rows[1:]:
        if len(row) == n_new:
            out_rows.append(row)
        elif len(row) == n_old:
            out_rows.append(row[:insert_at] + [""] * n_added + row[insert_at:])
        elif len(row) < n_new:
            padded = row + [""] * (n_new - len(row))
            out_rows.append(padded)
        else:
            out_rows.append(row[:n_new])

    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(out_rows)

    print(
        f"Naprawiono: {path} ({len(out_rows) - 1} wierszy danych, {n_old}→{n_new} kolumn)"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Naprawa nagłówka CSV wyników scrapera.")
    p.add_argument("csv", nargs="+", help="Ścieżka do pliku CSV")
    args = p.parse_args()
    for path in args.csv:
        repair_path(path)


if __name__ == "__main__":
    main()
