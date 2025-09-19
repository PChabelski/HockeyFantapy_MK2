import json
import time
import os
import logging
import warnings
from datetime import datetime
from datetime import timedelta
import pandas as pd
import numpy as np
from generic import YEAR_INSTANCE

pd.options.display.float_format = '{:,}'.format
np.seterr(divide='ignore')
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings('error')
warnings.simplefilter(action='ignore', category=FutureWarning)
logging.getLogger("yfpy.query").setLevel(level=logging.INFO)

current_directory = os.getcwd()

print("GOOD DAY! FANTASY HOCKEY 2025 VERSION")
with open(f'{current_directory}/control_file.json', 'r') as f:
    control_file = json.loads(f.read())
years_enabled = list(control_file['Years'].keys())
# I can move these into the generalized system
today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')

# =============================================================================================
operation_mode = input('Enter the operation mode (1 for Year-based, 2 for latest-live): ')
if operation_mode == '1':
    year = input('Enter the year you want to reprocess (YYYY): ')
    processing_type = 'REPROCESS'
elif operation_mode == '2':
    year = [max(years_enabled)]
    print(f'Processing the latest live data for year(s): {year}')
    processing_type = 'LIVE'
else:
    print('Incorrect operation mode. Please enter 1 or 2 next time')
    exit()

# =============================================================================================
dates_to_check = input(f"Enter the dates you want to run for year {year} (comma separated, e.g. {year}-01-01,{year}-01-02) OR ALL if you want everything in the season, OR ONWARD{year}-01-01 if you want everything  from a specific date onward: ").strip().split(',')
if dates_to_check == ['ALL']:
    print('Grabbing all dates in the season...')
    dates_to_check = pd.read_csv(f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv')['date'].unique()
elif 'ONWARD' in dates_to_check[0]:
    print('Grabbing dates from specified date onward...')
    onward_date = [x.replace('ONWARD','').strip() for x in dates_to_check if 'ONWARD' in x][0]
    all_dates = pd.read_csv(f'{current_directory}/league_weeks_and_dates/{year}_league_weeks_and_dates.csv')['date'].unique()
    dates_to_check = [x for x in all_dates if x >= onward_date]
else:
    print('Grabbing specific dates...')
    dates_to_check = [x.strip() for x in dates_to_check if x.strip()]
print(f'>> Dates to check: {dates_to_check}')

# =============================================================================================
print(f'Processing Year: {year}')
yahoo_api_instance = YEAR_INSTANCE(control_file, current_directory, year, processing_type, dates_to_check)

# Functions that either extract external data or should only be run once
# yahoo_api_instance.extract_player_metadata()
# yahoo_api_instance.NHL_schedule_parser()
yahoo_api_instance.extract_yahoo_draft_results()
yahoo_api_instance.extract_yahoo_transactions()
# yahoo_api_instance.get_team_roster_player_info_by_date()
# yahoo_api_instance.fuzzy_outer_merge()

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
print('Running the experimental methods')
# Not working yet:


yahoo_api_instance
# todo: expand the player metadata to a per year and add in the fuzzy matching logic from MK1
# todo: create the data stitcher
# todo: add in the transactions logic

# todo: how can i sql these????

'''
Available methods from yahoo api:
[
'get_game_roster_positions_by_game_id', 'get_game_stat_categories_by_game_id',
'get_game_weeks_by_game_id', 'get_league_draft_results', 'get_league_info', 
'get_league_key', 'get_league_matchups_by_week', 'get_league_metadata', 
'get_league_players', 'get_league_scoreboard_by_week', 'get_league_settings',
'get_league_standings', 'get_league_teams', 'get_league_transactions',
'get_player_draft_analysis', 'get_player_ownership', 'get_player_percent_owned_by_week', 
'get_player_stats_by_date', 'get_player_stats_by_week', 'get_player_stats_for_season',
'get_response', 'get_team_draft_results', 'get_team_info', 'get_team_matchups', 
'get_team_metadata', 'get_team_roster_by_week', 'get_team_roster_player_info_by_date',
'get_team_roster_player_info_by_week', 'get_team_roster_player_stats', 
'get_team_roster_player_stats_by_week', 'get_team_standings', 'get_team_stats',
'get_team_stats_by_week', 'get_user_games', 'get_user_leagues_by_game_key', ]
'''