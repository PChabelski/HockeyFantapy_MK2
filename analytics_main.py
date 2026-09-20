"""
analytics_main.py

Interactive entry point for the post-extraction analytics pipeline.

Instantiates ANALYTICS_ENGINE from analytics_engine.py for one or more
configured seasons, then lets the user choose which analytics methods to run.
Unlike main.py (which pulls live data), this script operates entirely on CSVs
that have already been written by main.py / generic.py.

Typical flow:
    1. Run main.py to extract and save raw Yahoo + HR data as CSVs.
    2. Run analytics_main.py to compute derived analytics on top of those CSVs.
    3. Optionally regenerate the merged csv_databases/ files for Power BI.
"""

import json
import os
import logging
import warnings
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import argparse
from analytics_engine import ANALYTICS_ENGINE
import traceback

# ==============================
# CONFIGURATION & INITIALIZATION
# ==============================

# Suppress float formatting noise in console output
pd.options.display.float_format = '{:,}'.format

# Silence divide-by-zero warnings (expected in stat ratio calculations)
np.seterr(divide='ignore')

# Suppress pandas FutureWarnings — pipeline is stable
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings('error')
warnings.simplefilter(action='ignore', category=FutureWarning)

# Reduce yfpy's verbose API logging to INFO level
logging.getLogger("yfpy.query").setLevel(level=logging.INFO)

current_directory = os.getcwd()


# ------------------------------
# General startup
# ------------------------------
print("GOOD DAY! FANTASY HOCKEY 2025 VERSION -- [[ANALYTICS ENGINE]]")

# Load the runtime control file for league config, scoring categories, etc.
with open(f'{current_directory}/manual_data/control_file.json', 'r') as f:
    control_file = json.loads(f.read())

# The list of seasons that have been configured in control_file.json
years_enabled = list(control_file['Years'].keys())

today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')

# ==============================
# OPERATION MODE SELECTION
# ==============================
# Three modes control which set of years to process:
#   1 — Single year (interactively specified)
#   2 — Custom multi-year list (comma-separated input)
#   3 — All configured years in control_file.json
operation_mode =  input('Enter the operation mode (1 for single year, 2 for multi-year, 3 for all):')

if operation_mode == '1':
    years_to_check =  [input('Enter the year you want to reprocess (YYYY): ')]

elif operation_mode == '2':
    # Multi year
    year = int(max(years_enabled))
    print(f'Processing the latest live data for year(s): {year}')
    years_to_check = input(
        f"Enter the years you want to analyze"
        f"(comma separated, e.g. 2025,2024,2023"
    ).strip().split(',')

elif operation_mode == ('3'):
    # All configured years
    year = int(max(years_enabled))
    print(f'Processing the latest live data for year(s): {year}')
    years_to_check = years_enabled

else:
    print('Incorrect operation mode. Please enter 1, 2, or 3 next time.')
    exit()

print(f'Processing Year(s): {years_to_check}')

# ==============================
# METHOD SELECTION
# ==============================
# Map numeric menu keys to analytics method names.
# Numbers start at 12 to distinguish from main.py's extraction methods (1-11),
# making it easy to cross-reference log output between the two scripts.
available_methods_baseline = {
    '12': 'link_hr_days_to_yh_days',       # Merge Yahoo roster CSVs with HR box score CSVs
    '13': 'yearly_stat_roster_combiner',    # Concatenate all merged daily CSVs into one yearly file
    '14': 'matchup_analytics',              # Compute per-week head-to-head stats and quality scores
    '15': 'hospital_analytics',             # Track injury streaks per player
    '16': 'loyalty_analytics',              # Measure how many drafted/kept players each GM held all season
    '17': 'keeper_analytics',               # Evaluate keeper ROI (FP generated while on roster)
    '18': 'draft_analytics',               # Evaluate draft pick ROI (FP generated while on roster)
    '19': 'FAAB_analytics',                 # Evaluate FAAB spend efficiency
    '20': 'streamer_analytics',             # Evaluate short-term pickups by week
    '21': 'engagement_analytics',           # Track add activity, missed starts, and failed goalie reqs per week
    '22': 'power_ranking_analytics'         # Aggregate monthly power rankings from matchup data
}
print("\nAvailable methods to run:")
for k, v in available_methods_baseline.items():
    print(f"  {k}. {v}")

# User can enter comma-separated method numbers, or 'all' to run everything
selected = input("\nEnter method numbers to run (comma separated, e.g. 3,4,5, all): ").split(',')

# Optionally regenerate the aggregated csv_databases/ files after analytics run
database_prompt = input("\n Do you want to regenerate all the databases? (Y/N) ")

# ==============================
# MAIN PROCESSING LOOP
# ==============================
# Instantiate a fresh ANALYTICS_ENGINE for each year and run the selected methods.
# Iterating per year (rather than once) keeps memory usage bounded and allows
# partial reruns of individual seasons.
for year in years_to_check:
    # ==============================
    # MAIN PROCESSING OBJECT
    # ==============================
    # ANALYTICS_ENGINE does not call the Yahoo API — it operates solely on CSVs.
    engine_instance = ANALYTICS_ENGINE(control_file, current_directory, year)

    # I don't anticipate needing to automate this - generally the analytics can lag behind until they're absolutely needed
    # Bind method names to their callables for this year's engine instance
    available_methods = {
        '12': ('link_hr_days_to_yh_days', engine_instance.link_hr_days_to_yh_days),
        '13': ('yearly_stat_roster_combiner', engine_instance.yearly_stat_roster_combiner),
        '14': ('matchup_analytics', engine_instance.matchup_analytics),
        '15': ('hospital_analytics', engine_instance.hospital_analytics),
        '16': ('loyalty_analytics', engine_instance.loyalty_analytics),
        '17': ('keeper_analytics', engine_instance.keeper_analytics),
        '18': ('draft_analytics', engine_instance.draft_analytics),
        '19': ('FAAB_analytics', engine_instance.FAAB_analytics),
        '20': ('streamer_analytics', engine_instance.streamer_analytics),
        '21': ('engagement_analytics',engine_instance.engagement_analytics),
        '22': ('power_ranking_analytics',engine_instance.power_ranking_analytics)
    }

    for s in selected:
        s = s.strip()
        if s in available_methods:
            name, func = available_methods[s]
            print(f"\n🔹 Running {name}()...")
            try:
                func()
                print(f"✅ {name} completed successfully.")
            except Exception as e:
                print(f"⚠️ {name} failed: {e}")
                tb_str = traceback.format_exc()
                print(tb_str)
        elif s =='all':
            # Run every available method in order for this year
            for method in available_methods:
                name, func = available_methods[method]
                print(f"\n🔹 Running {name}()...")
                try:
                    func()
                    print(f"✅ {name} completed successfully.")
                except Exception as e:
                    print(f"⚠️ {name} failed: {e}")
                    tb_str = traceback.format_exc()
                    print(tb_str)
        else:
            print(f"Skipping invalid selection: {s}")

    print(f"\n🎯 Processed analytics for {year} successfully\n🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒🏒")

# Regenerate the aggregated csv_databases/ files that Power BI connects to.
# Done once after all years are processed rather than per-year to avoid
# writing partial databases mid-run.
if database_prompt == ('Y'):
    engine_instance.create_csv_databases()
