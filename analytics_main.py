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
pd.options.display.float_format = '{:,}'.format
np.seterr(divide='ignore')
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings('error')
warnings.simplefilter(action='ignore', category=FutureWarning)
logging.getLogger("yfpy.query").setLevel(level=logging.INFO)

current_directory = os.getcwd()


# ------------------------------
# General startup
# ------------------------------
print("GOOD DAY! FANTASY HOCKEY 2025 VERSION -- [[ANALYTICS ENGINE]]")

with open(f'{current_directory}/manual_data/control_file.json', 'r') as f:
    control_file = json.loads(f.read())
years_enabled = list(control_file['Years'].keys())

today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')

# ==============================
# OPERATION MODE SELECTION
# ==============================
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
    # Daily operation
    year = int(max(years_enabled))
    print(f'Processing the latest live data for year(s): {year}')
    years_to_check = years_enabled

else:
    print('Incorrect operation mode. Please enter 1, 2, or 3 next time.')
    exit()

print(f'Processing Year(s): {years_to_check}')

# ask which processes you want to rerun
available_methods_baseline = {
    '12': 'link_hr_days_to_yh_days',
    '13': 'yearly_stat_roster_combiner',
    '14': 'matchup_analytics',
    '15': 'hospital_analytics',
    '16': 'loyalty_analytics',
    '17': 'keeper_analytics',
    '18': 'draft_analytics',
    '19': 'FAAB_analytics',
    '20': 'streamer_analytics',
    '21': 'engagement_analytics',
    '22': 'power_ranking_analytics'

}
print("\nAvailable methods to run:")
for k, v in available_methods_baseline.items():
    print(f"  {k}. {v}")

selected = input("\nEnter method numbers to run (comma separated, e.g. 3,4,5, all): ").split(',')

# ask if you want to run the database updater
database_prompt = input("\n Do you want to regenerate all the databases? (Y/N) ")

# fire away

for year in years_to_check:
    # ==============================
    # MAIN PROCESSING OBJECT
    # ==============================
    engine_instance = ANALYTICS_ENGINE(control_file, current_directory, year)

    # I don't anticipate needing to automate this - generally the analytics can lag behind until they're absolutely needed
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

if database_prompt == ('Y'):
    engine_instance.create_csv_databases()
