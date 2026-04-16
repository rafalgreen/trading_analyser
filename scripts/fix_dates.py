import os
import glob
import pandas as pd

RESULTS_DIR = 'results'
files = glob.glob(os.path.join(RESULTS_DIR, 'tradingview_results_*_*.csv'))

daily_data = {}

for f in files:
    filename = os.path.basename(f)
    # Extract date YYYY-MM-DD
    date_str = filename.replace('tradingview_results_', '')[:10]
    
    df = pd.read_csv(f, on_bad_lines='skip')
    if date_str not in daily_data:
        daily_data[date_str] = []
    daily_data[date_str].append(df)
    
for date_str, dfs in daily_data.items():
    combined = pd.concat(dfs, ignore_index=True)
    # Deduplicate keeping the last entry 
    combined = combined.drop_duplicates(subset=['Ticker', 'Interval'], keep='last')
    
    target_path = os.path.join(RESULTS_DIR, f"tradingview_results_{date_str}.csv")
    
    if os.path.exists(target_path):
        existing = pd.read_csv(target_path)
        combined = pd.concat([existing, combined], ignore_index=True).drop_duplicates(subset=['Ticker', 'Interval'], keep='last')

    combined.to_csv(target_path, index=False)
    print(f"Utworzono: {target_path}")

for f in files:
    os.remove(f)
    print(f"Usunięto stare: {f}")
