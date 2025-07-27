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

# I can move these into the generalized system
today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')

operation_mode = input('Enter the operation mode (1 for Year-based, 2 for all years): ')
if operation_mode == '1':
    year = input('Enter the year you want to check (YYYY): ')
    years = [year]
elif operation_mode == '2':
    years = [2024,2023,2022,2021,2020,2019,2018,2017,2016,2015,2014,2013,2012,2011]
elif operation_mode == '3':
    print('Secret Debug mode!')
    years = [2024]
else:
    print('Incorrect operation mode. Please enter 1 or 2 next time, you jerk!')
# =============================================================================================
for year in years:
    print(f'Processing Year: {year}')
    yahoo_api_instance = YEAR_INSTANCE(control_file, current_directory, year)
    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
    # yahoo_api_instance.extract_yahoo_league_metadata()
    # yahoo_api_instance.extract_yahoo_league_teams()
    # yahoo_api_instance.extract_yahoo_league_standings()
    # yahoo_api_instance.NHL_schedule_parser()
    yahoo_api_instance.extract_player_metadata()
    # yahoo_api_instance.extract_league_weeks_and_dates()
    # yahoo_api_instance.extract_league_stat_categories()
    # yahoo_api_instance.extract_league_scoreboard_by_week()
    # yahoo_api_instance.extract_yahoo_draft_results()
    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<



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