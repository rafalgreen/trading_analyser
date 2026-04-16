#!/usr/bin/env python3
"""
Jednorazowa naprawa tradingview_results_*.csv: dopisuje kolumny Scrape_Status i Scrape_Error
po Interval, tak aby wiersze zapisane ze statusem OK miały poprawne mapowanie kolumn.
"""
import argparse
import csv
import sys


def repair_path(path: str) -> None:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        print("Pusty plik:", path, file=sys.stderr)
        return

    header = rows[0]
    if "Scrape_Status" in header:
        print("OK:", path, "— nagłówek już zawiera Scrape_Status")
        return

    try:
        idx_iv = header.index("Interval")
    except ValueError:
        print("Brak kolumny Interval w:", path, file=sys.stderr)
        return

    insert_at = idx_iv + 1
    new_header = header[:insert_at] + ["Scrape_Status", "Scrape_Error"] + header[insert_at:]
    n_old = len(header)
    n_new = len(new_header)

    out_rows = [new_header]
    for row in rows[1:]:
        if len(row) == n_new:
            out_rows.append(row)
        elif len(row) == n_old:
            out_rows.append(row[:insert_at] + ["", ""] + row[insert_at:])
        elif len(row) < n_new:
            padded = row + [""] * (n_new - len(row))
            out_rows.append(padded)
        else:
            out_rows.append(row[:n_new])

    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(out_rows)

    print(f"Naprawiono: {path} ({len(out_rows) - 1} wierszy danych, {n_old}→{n_new} kolumn)")


def main() -> None:
    p = argparse.ArgumentParser(description="Naprawa nagłówka CSV wyników scrapera.")
    p.add_argument("csv", nargs="+", help="Ścieżka do pliku CSV")
    args = p.parse_args()
    for path in args.csv:
        repair_path(path)


if __name__ == "__main__":
    main()
