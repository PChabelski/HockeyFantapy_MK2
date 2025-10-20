import json
import os
import logging
import warnings
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import argparse
from generic import YEAR_INSTANCE
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
# Command-line arguments
# ------------------------------
parser = argparse.ArgumentParser(description="Fantasy Hockey Automation Script")
parser.add_argument("--mode", type=str, default=None, help="1 for Year-based, 2 for latest-live, 3 for custom")
parser.add_argument("--year", type=str, default=None, help="Year to process (YYYY, used if mode=1)")
parser.add_argument("--dates", type=str, default=None, help="Comma-separated dates, or ALL/ONWARDYYYY-MM-DD")
args = parser.parse_args()

# ------------------------------
# General startup
# ------------------------------
print("GOOD DAY! FANTASY HOCKEY 2025 VERSION")

with open(f'{current_directory}/manual_data/control_file.json', 'r') as f:
    control_file = json.loads(f.read())
years_enabled = list(control_file['Years'].keys())

today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')

# ==============================
# OPERATION MODE SELECTION
# ==============================
operation_mode = args.mode or input('Enter the operation mode (1 for Year-based/manual, 2 for latest-live, 3 for weekly): ')

if operation_mode == '1':
    year = args.year or input('Enter the year you want to reprocess (YYYY): ')

elif operation_mode == '2':
    # Daily operation
    year = int(max(years_enabled))
    print(f'Processing the latest live data for year(s): {year}')
    dates_to_check = [str(yesterday)]

elif operation_mode == '3':
    # weekly operation to grab HR data and run the
    year = int(max(years_enabled))
    week = input('Weekly operation - which week do you want to run?')
    df_weeks = pd.read_csv(f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv')
    if week not in df_weeks['week'].astype(str).unique():
        print(f'Invalid week: {week}. Available weeks: {df_weeks["week"].astype(str).unique()}')
        exit()
    dates_to_check = df_weeks[df_weeks['week'].astype(str) == week]['date'].tolist()
    print(f'Processing week {week} for year {year} on dates: {dates_to_check}')
    # For CUSTOM mode, we can hardcode specific dates if

else:
    print('Incorrect operation mode. Please enter 1, 2, or 3 next time.')
    exit()

# ==============================
# DATE SELECTION LOGIC
# ==============================
if operation_mode not in ['2', '3']:
    if args.dates:
        dates_to_check = args.dates.strip().split(',')
    else:
        dates_to_check = input(
            f"Enter the dates you want to run for year {year} "
            f"(comma separated, e.g. {year}-01-01,{year}-01-02) OR ALL if you want everything, "
            f"OR ONWARD{year}-01-01 if you want everything from a specific date onward: "
        ).strip().split(',')

    if dates_to_check == ['ALL']:
        print('Grabbing all dates in the season...')
        dates_to_check = pd.read_csv(
            f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv'
        )['date'].unique()

    elif 'ONWARD' in dates_to_check[0]:
        print('Grabbing dates from specified date onward...')
        onward_date = [x.replace('ONWARD', '').strip() for x in dates_to_check if 'ONWARD' in x][0]
        all_dates = pd.read_csv(
            f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv'
        )['date'].unique()
        dates_to_check = [x for x in all_dates if x >= onward_date]

    else:
        print('Grabbing specific dates...')
        dates_to_check = [x.strip() for x in dates_to_check if x.strip()]

print(f'>> Dates to check: {dates_to_check}')
print(f'Processing Year: {year}')

# ==============================
# MAIN PROCESSING OBJECT
# ==============================
yahoo_api_instance = YEAR_INSTANCE(control_file, current_directory, year, dates_to_check)

# ==============================
# AUTOMATION / INTERACTIVE MODES
# ==============================
if operation_mode == '2':
    # === LIVE MODE: automatic standard pipeline ===
    print("Running LIVE mode — executing yahoo time-senstive parsing.")
    yahoo_api_instance.extract_yahoo_transactions()
    yahoo_api_instance.extract_yahoo_rosters()
    print("✅ Live data processing complete.")

if operation_mode == '3':
    # === WEEKLY MODE: automatic standard pipeline ===
    print("Running WEEKLY mode — executing HR Parsing, stitching, and weekly analytics.")
    yahoo_api_instance.NHL_schedule_parser()
    yahoo_api_instance.parse_HR_data()
    yahoo_api_instance.fuzzy_outer_merge_hr()
    yahoo_api_instance.post_processor_matchup_matchups()
    print("✅ Weekly data processing complete.")

elif operation_mode == '1':
    # === MANUAL MODE: pick which methods to run ===
    available_methods = {
        '1': ('NHL_schedule_parser', yahoo_api_instance.NHL_schedule_parser),
        '2': ('extract_yahoo_draft_results', yahoo_api_instance.extract_yahoo_draft_results),
        '3': ('extract_yahoo_transactions', yahoo_api_instance.extract_yahoo_transactions),
        '4': ('extract_yahoo_rosters', yahoo_api_instance.extract_yahoo_rosters),
        '5': ('parse_HR_data', yahoo_api_instance.parse_HR_data),
        '6': ('fuzzy_outer_merge_hr', yahoo_api_instance.fuzzy_outer_merge_hr),
        '7': ('super_stitcher', yahoo_api_instance.super_stitcher),
        '8': ('post_processor_matchup_matchups', yahoo_api_instance.post_processor_matchup_matchups),
        '9': ('sql_table_creator', yahoo_api_instance.sql_table_creator),
        '10': ('test_function', yahoo_api_instance.test_function),

    }

    print("\nAvailable methods to run:")
    for k, v in available_methods.items():
        print(f"  {k}. {v[0]}")

    selected = input("\nEnter method numbers to run (comma separated, e.g. 3,4,5): ").split(',')

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
        else:
            print(f"Skipping invalid selection: {s}")

print("\n🎯 Script completed successfully.")
