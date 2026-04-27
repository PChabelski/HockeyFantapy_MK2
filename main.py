"""
main.py

Interactive entry point for Yahoo Fantasy Hockey data extraction.

Supports two operation modes:
    Mode 1 (manual): Reprocess a specific year and set of dates, with a
                     menu to choose which extraction methods to run.
    Mode 2 (live):   Automatically pull yesterday's data across all
                     standard extraction methods. Called daily by
                     daily_runner.py via subprocess.

All extracted data is written to per-year CSV folders and consumed
downstream by analytics_engine.py / analytics_main.py.
"""

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

# Suppress float formatting noise in console output
pd.options.display.float_format = '{:,}'.format

# Silence divide-by-zero warnings from numpy (expected in stat calculations)
np.seterr(divide='ignore')

# Suppress pandas FutureWarnings — intentional, pipeline is stable
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings('error')
warnings.simplefilter(action='ignore', category=FutureWarning)

# Reduce yfpy's verbose API logging to INFO level only
logging.getLogger("yfpy.query").setLevel(level=logging.INFO)

current_directory = os.getcwd()

# ------------------------------
# Command-line arguments
# ------------------------------
# Allows daily_runner.py (and manual invocations) to pass mode/year/dates
# without interactive prompts, enabling fully automated runs.
parser = argparse.ArgumentParser(description="Fantasy Hockey Automation Script")
parser.add_argument("--mode", type=str, default=None, help="1 for Year-based, 2 for latest-live, 3 for custom")
parser.add_argument("--year", type=str, default=None, help="Year to process (YYYY, used if mode=1)")
parser.add_argument("--dates", type=str, default=None, help="Comma-separated dates, or ALL/ONWARDYYYY-MM-DD")
args = parser.parse_args()

# ------------------------------
# General startup
# ------------------------------
print("GOOD DAY! FANTASY HOCKEY 2025 VERSION")

# Load the runtime control file — contains league IDs, game IDs, scoring
# categories, keeper lists, and matchup metadata for every enabled season.
with open(f'{current_directory}/manual_data/control_file.json', 'r') as f:
    control_file = json.loads(f.read())

# Build the list of years that are configured and active in the control file
years_enabled = list(control_file['Years'].keys())

today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')

# ==============================
# OPERATION MODE SELECTION
# ==============================
# Falls back to interactive prompt if --mode was not passed on the CLI
operation_mode = args.mode or input('Enter the operation mode (1 for Year-based/manual, 2 for latest-live): ')

if operation_mode == '1':
    # Manual mode: user specifies year interactively or via --year flag
    year = args.year or input('Enter the year you want to reprocess (YYYY): ')

elif operation_mode == '2':
    # Live/daily mode: always target the most recently configured season
    year = int(max(years_enabled))
    print(f'Processing the latest live data for year(s): {year}')
    # Live mode always processes yesterday — today's games aren't finished yet
    dates_to_check = [str(yesterday)]

else:
    print('Incorrect operation mode. Please enter 1, 2, or 3 next time.')
    exit()

# ==============================
# DATE SELECTION LOGIC
# ==============================
# Only needed for manual mode (mode 2 already set dates_to_check above)
if operation_mode not in ['2']:
    if args.dates:
        # Dates passed directly as a CLI argument — split on comma
        dates_to_check = args.dates.strip().split(',')
    else:
        # Interactive date prompt with several convenience shortcuts
        dates_to_check = input(
            f"Enter the dates you want to run for year {year} "
            f"(comma separated, e.g. {year}-01-01,{year}-01-02) OR ALL if you want everything, "
            f"OR ONWARD{year}-01-01 if you want everything from a specific date onward: "
        ).strip().split(',')

    if dates_to_check == ['ALL']:
        # Load every date in the season from the pre-generated weeks CSV
        print('Grabbing all dates in the season...')
        dates_to_check = pd.read_csv(
            f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv'
        )['date'].unique()

    elif 'ONWARD' in dates_to_check[0]:
        # Extract the anchor date and filter to everything on or after it
        print('Grabbing dates from specified date onward...')
        onward_date = [x.replace('ONWARD', '').strip() for x in dates_to_check if 'ONWARD' in x][0]
        all_dates = pd.read_csv(
            f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv'
        )['date'].unique()
        dates_to_check = [x for x in all_dates if x >= onward_date]

    elif dates_to_check == ['YESTERDAY']:
        # Filter the full season date list to everything up through yesterday
        print('Grabbing dates from start to yesterday...')
        all_dates = pd.read_csv(
            f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv'
        )['date'].unique()
        dates_to_check = [x for x in all_dates if x <= yesterday]

    else:
        # Specific date list — strip whitespace from each entry
        print('Grabbing specific dates...')
        dates_to_check = [x.strip() for x in dates_to_check if x.strip()]

print(f'>> Dates to check: {dates_to_check}')
print(f'Processing Year: {year}')

# ==============================
# MAIN PROCESSING OBJECT
# ==============================
# YEAR_INSTANCE.__init__ immediately runs several auto-extractions:
# league metadata, teams, scoreboard, weeks/dates, standings,
# NHL schedule, player metadata, and the Yahoo<->HR name mapper.
yahoo_api_instance = YEAR_INSTANCE(control_file, current_directory, year, dates_to_check)

# ==============================
# AUTOMATION / INTERACTIVE MODES
# ==============================
if operation_mode == '2':
    # === LIVE MODE: automatic standard pipeline ===
    # Runs the full daily extract sequence without user interaction.
    # Order matters: schedule must exist before rosters, HR data parsed last.
    print("Running LIVE mode — executing yahoo time-senstive parsing.")
    yahoo_api_instance.NHL_schedule_parser()
    yahoo_api_instance.extract_yahoo_transactions()
    yahoo_api_instance.extract_yahoo_rosters()
    yahoo_api_instance.parse_HR_data()
    yahoo_api_instance.mapping_hr_to_yh_names()
    print("✅ Live online data processing complete.")


elif operation_mode == '1':
    # === MANUAL MODE: pick which methods to run ===
    # Maps menu keys to (display_name, bound_method) tuples.
    # Numbered to allow future insertion of new methods without renumbering.
    available_methods = {
        '1': ('NHL_schedule_parser', yahoo_api_instance.NHL_schedule_parser),
        '2': ('extract_yahoo_draft_results', yahoo_api_instance.extract_yahoo_draft_results),
        '3': ('extract_yahoo_transactions', yahoo_api_instance.extract_yahoo_transactions),
        '4': ('extract_yahoo_rosters', yahoo_api_instance.extract_yahoo_rosters),
        '5': ('parse_HR_data', yahoo_api_instance.parse_HR_data),
        '11': ('mapping_hr_to_yh_names', yahoo_api_instance.mapping_hr_to_yh_names)
    }


    print("\nAvailable methods to run:")
    for k, v in available_methods.items():
        print(f"  {k}. {v[0]}")

    selected = input("\nEnter method numbers to run (comma separated, e.g. 3,4,5): ").split(',')

    # Execute each selected method in order, catching and printing any
    # per-method failures so one bad extraction doesn't abort the rest.
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
