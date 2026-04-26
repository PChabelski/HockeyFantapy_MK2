import unicodedata, re
try:
    from unidecode import unidecode
except ImportError:
    unidecode = None
from yfpy.query import YahooFantasySportsQuery
from bs4 import BeautifulSoup, Comment
import requests
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import os
import time
import glob
import duckdb
from difflib import get_close_matches
from openai import OpenAI
import json

pd.options.display.float_format = '{:,}'.format
pd.set_option('mode.chained_assignment', None)

class YEAR_INSTANCE:
    """
    Generic engine for running the code.
    """

    def __init__(self, control_file, current_directory, year, dates_to_check):
        self.control_file       = control_file
        self.year               = year
        self.stats_for_year     = self.control_file["Years"][str(self.year)]['scoring_categories']
        self.league_id          = str(self.control_file['Years'][str(self.year)]['league_id'])
        self.game_id            = int(self.control_file['Years'][str(self.year)]['game_id'])
        self.current_directory  = current_directory
        self.dates_to_check     = dates_to_check
        print(f'[{time.ctime()}] Initializing instance for Year {self.year} | League ID {self.league_id} | Game ID {self.game_id}')


        self.query = YahooFantasySportsQuery(league_id=self.league_id,
                                             game_id=self.game_id,
                                             game_code='nhl',
                                             yahoo_consumer_key="dj0yJmk9eUpNejZORGw4QlUzJmQ9WVdrOVYwVnVOMlp0ZEhjbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PWJk",
                                             yahoo_consumer_secret="29403fb4377da89b90020a8dfb5ba79082a31c59",
                                             env_var_fallback=True,
                                             save_token_data_to_env_file=True,
                                             env_file_location=Path(f"{current_directory}/private"),
                                             all_output_as_json_str = False
                                             )

        self.query.save_access_token_data_to_env_file(
            env_file_location=Path(f"{current_directory}/private"),
            save_json_to_var_only=True
        )

        # run some of the metadata extraction functions
        self.extract_yahoo_league_metadata()
        self.extract_yahoo_league_teams()
        self.extract_league_scoreboard_by_week()
        self.extract_league_weeks_and_dates()
        self.extract_yahoo_league_standings()
        self.ref_list = pd.read_csv(f'{self.current_directory}/manual_data/Hockey_Team_Codes.csv')
        self.NHL_schedule_parser()

        # for the mapping portion - have a list of the most common names and their shortforms
        self.first_name_dict  = {
                      "Alexander": "Alex",
                      "Alexandar": "Alex",
                      "Andrew": "Andy",
                      "Anthony": "Tony",
                      "Benjamin": "Ben",
                      "Bradley": "Brad",
                      "Cameron":"Cam",
                      "Calvin": "Cal",
                      "Charles": "Charlie",
                      "Christopher": "Chris",
                      "Daniel": "Dan",
                      "Dominic": "Dom",
                      "Douglas": "Doug",
                      "Edward": "Ed",
                      "Francis": "Frank",
                      "Freddy":"Fred",
                      "Frederik": "Fred",
                      "Frederick": "Fred",
                      "Gabriel": "Gabe",
                      "Geoffrey": "Geoff",
                      "Gregory": "Greg",
                      "Jacob": "Jake",
                      "Jeffrey": "Jeff",
                      "Jonathan": "John",
                      "Joshua": "Josh",
                      "Leonard": "Leo",
                      "Lucas": "Luke",
                      "Marcus": "Mark",
                      "Matthew": "Matt",
                      "Maxime":"Max",
                      "Michael": "Mike",
                      "Mikey":"Mike",
                      "Nathaniel": "Nate",
                      "Nicholas": "Nick",
                      "Patrick": "Pat",
                      "Philip": "Phil",
                      "Robert": "Rob",
                      "Samuel": "Sam",
                      "Theodore": "Theo",
                      "Thomas": "Tom",
                      "Timothy": "Tim",
                      "Victor": "Vic",
                      "Walter": "Walt",
                      "William": "Will",
                      "Zachary": "Zach"
}

        self.extract_player_metadata()




    def extract_player_metadata(self):
        print(f'[{time.ctime()}] Extracting yahoo player metadata for year {self.year}')
        player_metadata = self.query.get_league_players()
        output_dir = f'{self.current_directory}/player_metadata'
        os.makedirs(output_dir, exist_ok=True)

        try:
            self.df_all_player_metadata = pd.read_csv(f'{self.current_directory}/manual_data/PLAYER_MASTER_DATA.csv')
            print(f'[{time.ctime()}] Successfully loaded PLAYER_MASTER_DATA file ({len(self.df_all_player_metadata)} entries)')
        except:
            self.df_all_player_metadata = pd.DataFrame(columns = ['season','yahoo_name','player_id','HR_MATCH_NAME','HR_LINK_NAME','HR_MATCH_SCORE','HR_REVIEW_FLAG','HR_COLLISION_FLAG'])
            print(f'[{time.ctime()}] Could not find PLAYER_MASTER_DATA file! Creating one now...')

        # Filter down to the appropriate season
        self.df_player_metadata = self.df_all_player_metadata[self.df_all_player_metadata['season']==int(self.year)]
        self.df_otheryear_player_metadata = self.df_all_player_metadata[self.df_all_player_metadata['season']!=int(self.year)]
        print(f'[{time.ctime()}] Filtered master list down to all {self.year} entries: ({len(self.df_player_metadata)} entries)')


        for i  in range(0,len(player_metadata)):

            try:
                player_data = player_metadata[i].clean_data_dict()
            except:
                player_data = player_metadata[i]['player'].clean_data_dict()
                #print(f'Year {self.year} bugged out trying to extract: {player_data}')

            player_id = player_data['player_id']
            yahoo_name = player_data.get('name', {}).get('full', 'UNKNOWN NAME')
            if player_id in self.df_player_metadata['player_id'].values:
                continue
            else:
                print(f'Adding {player_id} {yahoo_name} to MASTER DATA LIST')
                self.df_player_metadata.loc[len(self.df_player_metadata)] = (self.year, yahoo_name,player_id, '', '',np.nan,True,True)

        # RUN THE MAPPER - this will check for unmatched names, run against the latest HR data, and try to update whatever it can.
        # The function also outputs the year-file to the player_metadata/ folder
        self.mapping_hr_to_yh_names()

        # Then recreate the megafile once more
        self.master_metadata_file = pd.concat([self.df_player_metadata, self.df_otheryear_player_metadata])


        self.master_metadata_file.to_csv(f'manual_data/PLAYER_MASTER_DATA.csv', index=False)

    def mapping_hr_to_yh_names(self):
        print(f'[{time.ctime()}] Running the HR<->yahoo player metadata mapper for year {self.year}')

        # grab all the names and associated metadata for this particular year
        df_hr_year = pd.DataFrame()
        for filename in glob.glob(f'{self.current_directory}/hr_data_extract/{self.year}/*.csv', recursive=True):
            hr_df = pd.read_csv(filename)
            df_hr_year = pd.concat([df_hr_year, hr_df], ignore_index=True)

        df_hr_year_just_names = df_hr_year[['PLAYER', 'HR_LINK_NAME']].drop_duplicates().reset_index(drop=True)
        print(f'[{time.ctime()}] Fuzzy-matching yahoo player metadata to HR names for year {self.year}')
        # THEN do a fuzzy match, but only on the names that we have yet to match -> HR_REVIEW_FLAG = True
        df_player_metadata_matched = self.df_player_metadata[self.df_player_metadata['HR_REVIEW_FLAG']==False]
        df_player_metadata_unmatched = self.df_player_metadata[self.df_player_metadata['HR_REVIEW_FLAG']==True]

        df_player_metadata_unmatched = self.fuzzy_match_players_to_hr(
            player_df=df_player_metadata_unmatched,
            hr_df=df_hr_year_just_names,
            player_name_col="yahoo_name",
            hr_name_col="PLAYER",
            hr_link_name_col="HR_LINK_NAME",
            score_cutoff=90,
            review_threshold=90

        )
        self.df_player_metadata = pd.concat([df_player_metadata_unmatched, df_player_metadata_matched])

        # now send a notification if there are duplicate HR_MATCH_NAME entries with different player_ids
        duplicate_hr_matches = self.df_player_metadata.groupby(['HR_LINK_NAME'])['player_id'].nunique()
        duplicate_hr_matches = duplicate_hr_matches[duplicate_hr_matches > 1]
        if not duplicate_hr_matches.empty:
            print(f'[{time.ctime()}] WARNING: Duplicate HR matches found for year {self.year}:')
            print(duplicate_hr_matches)

        time.sleep(10)


    def extract_yahoo_league_metadata(self):
        """
        Extracts and saves Yahoo Fantasy Sports league metadata as a CSV file.
        Optimized for performance by directly appending data to the DataFrame.
        """
        # Retrieve and clean league metadata
        print(f'[{time.ctime()}] Extracting yahoo league metadata for year {self.year}')
        league_metadata = self.query.get_league_metadata().clean_data_dict()
        # Create a DataFrame directly from the metadata dictionary
        self.df_league_metadata = pd.DataFrame([league_metadata])
        # Ensure the 'league_metadata' directory exists
        output_dir = f'{self.current_directory}/league_metadata'
        os.makedirs(output_dir, exist_ok=True)
        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_metadata.csv'
        self.df_league_metadata.to_csv(output_file, index=False)

    def extract_yahoo_league_teams(self):
        """
        Extracts and saves Yahoo Fantasy Sports league teams data as a CSV file.

        This method retrieves a list of team dictionaries using the YahooFantasySportsQuery object,
        processes each team's data, and stores it in a pandas DataFrame. The data is then saved
        to a CSV file in a directory named 'league_teams'. If the directory does not exist, it is created.

        Attributes:
            self.league_teams (list): A list of team dictionaries retrieved from the Yahoo API.
            self.df_league_teams (pd.DataFrame): A DataFrame to store the processed team data.

        CSV Columns:
            - name: Team name (decoded from UTF-8).
            - team_id: Unique identifier for the team.
            - team_key: Key associated with the team.
            - number_of_moves: Number of moves made by the team.
            - number_of_trades: Number of trades made by the team.
            - waiver_priority: Waiver priority of the team.
            - faab_balance: Free Agent Acquisition Budget balance.
            - clinched_playoffs: Indicates if the team clinched playoffs (default is 0 if not available).
            - team_logo_url: URL of the team's logo.
            - email: Email of the team manager.
            - felo_score: Felo score of the team manager.
            - felo_tier: Felo tier of the team manager.
            - gm_image_url: URL of the general manager's image.
            - gm_name: Nickname of the general manager.
        """
        # Retrieve and clean league teams data

        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # 2012 the API CANNOT PARSE THE TEAMS; MANUALLY GENERATE INSTEAD!
        print(f'[{time.ctime()}] Extracting yahoo league team info for year {self.year}')
        if int(self.year) == 2012:
            print('[extract_yahoo_league_teams] - Not processing teams for 2012 as Yahoo API bugs out. Load the premade one.')
            self.df_league_teams = pd.read_csv(f'{self.current_directory}/league_teams/{self.year}_league_teams.csv')
            return
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        league_teams = self.query.get_league_teams()
        # Prepare a list of team dictionaries for DataFrame creation
        team_data = []
        for team_obj in league_teams:
            team = team_obj.clean_data_dict()
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            # YAHOO API ERROR-HANDLING - CONSIDER MOVING THESE INTO YEAR-METHODS
            # Safely handle the 'managers' field if it's a list
            # This occurs in 2014 when Yusko co-managed with someone else.
            managers = team.get('managers', {})
            if isinstance(managers, list) and managers:
                manager = managers[0]['manager'].clean_data_dict()  # Access the first manager in the list
                print('[extract_yahoo_league_teams] - Co-manager found, using first manager')
            else:
                manager = managers['manager'].clean_data_dict()  # Assume it's a dictionary or empty
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            # Data quality update - force the names of each GM to be the same across seasons
            # Some GMs have different names in different seasons, etc. Some old accounts have been removed and replaced with --hidden--
            gm_name =  manager.get('nickname', '')
            team_name = team.get('name', '').decode('utf-8')
            if team_name == "Vintage'tingle'Boar":
                gm_name = 'Tingle'
            elif team_name == "The Nerve":
                gm_name = 'Ira'
            elif (team_name == "#G") | (team_name == "Grampa Jarzabek"):
                gm_name = 'A'
            # 2014
            elif team_name == "garrett's Team":
                gm_name = 'Garrett'
            elif team_name == "Josh's Cool Team":
                gm_name = 'Josh'
            elif team_name == "Unstopoulos":
                gm_name = 'George'
            elif team_name == "the dusters":
                gm_name = 'Yusko' # I'm not actually sure about this one
            else:
                pass
            # 2015
            if team_name == "The T-BAGS":
                gm_name = 'Taylor'
            elif team_name == "Kessel/Trudeau 2015":
                gm_name = 'Thomson'
            else:
                pass
            if gm_name == 'Doctor Kocktapus':
                gm_name = 'Peter'
            elif gm_name == 't':
                gm_name = 'Taylor'
            elif gm_name == 'Master':
                gm_name = 'Yusko'
            elif gm_name == 'Thomson McKnight':
                gm_name = 'Thomson'
            elif gm_name == 'garrett':
                gm_name = 'Garrett'
            elif gm_name == 'george':
                gm_name = 'George'
            else:
                pass
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

            team_data.append({
                'season':self.year,
                'name': team_name,
                'team_id': team.get('team_id', ''),
                'team_key': team.get('team_key', ''),
                'number_of_moves': team.get('number_of_moves', 0),
                'number_of_trades': team.get('number_of_trades', 0),
                'waiver_priority': team.get('waiver_priority', 0),
                'faab_balance': team.get('faab_balance', 0),
                'clinched_playoffs': team.get('clinched_playoffs', 0),
                'team_logo_url': team.get('team_logos', {}).get('team_logo', {}).url,
                'email': manager.get('email', '') if isinstance(manager, dict) else '',
                'felo_score': manager.get('felo_score', 0) if isinstance(manager, dict) else 0,
                'felo_tier': manager.get('felo_tier', '') if isinstance(manager, dict) else '',
                'gm_image_url': manager.get('image_url', '') if isinstance(manager, dict) else '',
                'gm_name': gm_name
            })
        # Create a DataFrame from the list of dictionaries
        self.df_league_teams = pd.DataFrame(team_data)
        # Ensure the 'league_teams' directory exists
        output_dir = f'{self.current_directory}/league_teams'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_teams.csv'
        self.df_league_teams.to_csv(output_file, index=False)

    def extract_yahoo_league_standings(self):
        print(f'[{time.ctime()}] extracting yahoo league standings for year {self.year}')

        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # 2012 the API CANNOT PARSE THE TEAMS; MANUALLY GENERATE INSTEAD!
        if int(self.year) == 2012:
            print(f'[{time.ctime()}] [extract_yahoo_league_standings] - Not processing standings for 2012 as Yahoo API bugs out')
            return
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        league_standings = self.query.get_league_standings().clean_data_dict()['teams']
        # League standings are presented as team objects, with additional standings metadata
        team_standings = []
        for team_results_obj in league_standings:
            team_results = team_results_obj['team'].clean_data_dict()
            team_standings.append({
                'name': team_results.get('name', '').decode("utf-8"),
                'team_id': team_results.get('team_id', ''),
                'team_key': team_results.get('team_key', ''),
                'clinched_playoffs': team_results.get('clinched_playoffs', 0),
                'total_points': team_results.get('team_points', {}).get('total', 0),
                'wins': team_results.get('team_standings', {}).get('outcome_totals', {}).get('wins', 0),
                'losses': team_results.get('team_standings', {}).get('outcome_totals', {}).get('losses', 0),
                'ties': team_results.get('team_standings', {}).get('outcome_totals', {}).get('ties', 0),
                'percentage': team_results.get('team_standings', {}).get('outcome_totals', {}).get('percentage', 0),
                'playoff_seed': team_results.get('team_standings', {}).get('playoff_seed', 0),
                'rank': team_results.get('team_standings', {}).get('rank', 0)
            })

        # Create a DataFrame from the list of dictionaries
        self.df_league_standings = pd.DataFrame(team_standings)
        # Ensure the 'league_standings' directory exists
        output_dir = f'{self.current_directory}/league_standings'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_standings.csv'
        self.df_league_standings.sort_values('total_points', ascending=False, inplace=True)
        self.df_league_standings.to_csv(output_file, index=False)

    def NHL_schedule_parser(self):
        print(f'[{time.ctime()}] grabbing nhl schedule data from year {self.year}')
        url = f"https://www.hockey-reference.com/leagues/NHL_{int(self.year)+1}_games.html"
        print(url)
        page = requests.get(url)
        soup = BeautifulSoup(page.content, "html.parser")
        tables = soup.find_all('table')
        self.schedule_df = pd.read_html(str(tables[0]))[0]
        rows = tables[0].find_all(['th', 'tr'])
        row_count = 0
        for row in rows:
            cells = row.find_all('td')
            for cell in cells:
                if 'csk' in cell.attrs and cell['csk'] not in ['0', '1'] and cell['csk'].split('.')[0] in \
                        cell['csk'].split('.')[1]:
                    url_code = cell['csk'].split('.')[1]
                    self.schedule_df.at[row_count, 'URL_KEY'] = url_code
                    row_count += 1
        # Save the schedule to a CSV file
        output_dir = f'{self.current_directory}/season_schedules'
        os.makedirs(output_dir, exist_ok=True)
        self.schedule_df.to_csv(f'{output_dir}/{self.year}_NHL_Schedule.csv', index=False)

    def identical_names_handler(self, df, dbtype):
        """
        Handles identical player names by appending middle names based on unique identifiers.
        """
        players_to_update = {
            'yahoo': {
                'Sebastian Aho': {6777: 'Sebastian Antero Aho', 7654: 'Sebastian Johannes Aho'},
                'Elias Pettersson': {7520: 'Elias Fredrik Pettersson', 32762: 'Elias Nils Pettersson'}
            },
            'hr': {
                'Sebastian Aho': {'ahose02': 'Sebastian Johannes Aho', 'ahose01': 'Sebastian Antero Aho'},
                'Elias Pettersson': {'petteel01': 'Elias Fredrik Pettersson', 'petteel02': 'Elias Nils Pettersson'}
            }
        }
        col = 'PLAYER' if dbtype == 'hr' else 'NAME'
        if dbtype in players_to_update:
            for player, updates in players_to_update[dbtype].items():
                if player in df[col].unique():
                    for key, new_name in updates.items():
                        print(f'Looking for {player} with key {key} to update to {new_name}')
                        try:
                            if dbtype == 'yahoo':
                                player_keys = df[df[col] == player]['PLAYER_ID'].astype(int)
                                if any(player_keys == key):
                                    print(f'Found {player} ({key}) - adding middle name')
                                    df.loc[(df[col] == player) & (
                                                df['PLAYER_ID'].astype(int) == key), col] = new_name
                            elif dbtype == 'hr':
                                pcode = df[df[col] == player]['PLAYER_CODE']
                                if any(pcode == key):
                                    print(f'Found {player} ({key}) - adding middle name')
                                    df.loc[(df[col] == player) & (df['PLAYER_CODE'] == key), col] = new_name
                        except Exception as e:
                            print(f'Error updating {player}: {e}')

        return df

    def extract_league_weeks_and_dates(self):

        print(f'[{time.ctime()}] extracting league weeks and dates for year {self.year}')

        self.game_weeks = self.query.get_game_weeks_by_game_id(self.game_id)

        week_max = self.df_league_metadata['end_week'].iloc[0]
        week_arr = []
        for week_data in self.game_weeks:
            week_data = week_data.clean_data_dict()
            week = week_data.get('week', '?')
            week_start = week_data.get('start', '?')
            week_end = week_data.get('end', '?')

            is_playoff_week = 'PLAYOFFS' if 1 in self.df_matchup_metadata[self.df_matchup_metadata['week'].astype(int)==week]['is_playoff'].unique() else 'POST-SEASON' if week>week_max else 'SEASON'


            date_range = pd.date_range(start=week_start, end=week_end)
            for date in date_range:
                week_arr.append({
                    'season': self.year,
                    'week': week,
                    'date': date,
                    'playoff_week':is_playoff_week
                })
        # Create a DataFrame from the list of dictionaries
        self.df_weeks = pd.DataFrame(week_arr)
        output_dir = f'{self.current_directory}/league_weeks_and_dates'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_weeks_and_dates.csv'
        self.df_weeks.to_csv(output_file, index=False)

    def extract_league_scoreboard_by_week(self):
        # Check the # of weeks for this year
        # If there are no weeks, return None
        print(f'[{time.ctime()}] extracting league scoreboard information for year {self.year}')

        self.df_weeks = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')
        matchup_arr = []
        for week in self.df_weeks['week'].unique():
            try:
                week_matchup_data = self.query.get_league_scoreboard_by_week(chosen_week=int(week)).clean_data_dict()['matchups']
            except:
                print(f'No matchups in week {week} for year {self.year} (likely extra playoff week)')
                continue
            matchup_count = 0
            for matchup in week_matchup_data:
                matchup_count +=1
                matchup_data = matchup['matchup'].clean_data_dict()
                # Extract the relevant data from the matchup_data dictionary
                is_consolation = matchup_data.get('is_consolation', 0)
                is_playoff = matchup_data.get('is_playoffs', 0)
                week = matchup_data.get('week', 0)
                winner_team_key = matchup_data.get('winner_team_key', 'TIED')
                team_a_data = matchup_data['teams'][0]['team'].clean_data_dict()
                team_b_data = matchup_data['teams'][1]['team'].clean_data_dict()
                team_a_total_points = int(team_a_data.get('team_points', {}).get('total', 0))
                team_a_key = team_a_data.get('team_key', 'Unknown')
                team_a_name = team_a_data.get('name', 'Unknown').decode('utf-8')
                team_b_total_points = int(team_b_data.get('team_points', {}).get('total', 0))
                team_b_key = team_b_data.get('team_key', 'Unknown')
                team_b_name = team_b_data.get('name', 'Unknown').decode('utf-8')
                team_a_result = 'WIN' if team_a_total_points > team_b_total_points else 'LOSS' if team_a_total_points < team_b_total_points else 'TIE'
                team_b_result = 'WIN' if team_b_total_points > team_a_total_points else 'LOSS' if team_b_total_points < team_a_total_points else 'TIE'

                matchup_arr.append({
                    'season': self.year,
                    'week': week,
                    'matchup': int(matchup_count),
                    'is_consolation': is_consolation,
                    'is_playoff': is_playoff,
                    'winner_team_key': winner_team_key,
                    'team_a_key': team_a_key,
                    'team_a_name': team_a_name,
                    'team_a_total_points': team_a_total_points,
                    'team_a_result': team_a_result,
                    'team_b_key': team_b_key,
                    'team_b_name': team_b_name,
                    'team_b_total_points': team_b_total_points,
                    'team_b_result': team_b_result
                })

        # Create a DataFrame from the list of dictionaries
        self.df_matchup_metadata = pd.DataFrame(matchup_arr)
        output_dir = f'{self.current_directory}/league_scoreboards_by_week'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_scoreboards.csv'
        self.df_matchup_metadata.to_csv(output_file, index=False)

    def extract_yahoo_transactions(self):
        print(f'[{time.ctime()}] extracting transaction information for year {self.year}')

        df_trans = pd.DataFrame(columns=['season',
                                         'week',
                                         'transaction_date',
                                         'transaction_type',
                                         'transaction_id',
                                         'status',
                                         'player_id',
                                         'name',
                                         'draft_round',
                                         'draft_pick',
                                         'faab_bid',
                                         'source',
                                         'source_key',
                                         'destination',
                                         'destination_key',
                                         'waiver',
                                         'keeper',
                                         'GM_Name_destination',
                                        'GM_Name_source'
                                             ])

        try:
            df_weeks = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')
        except FileNotFoundError:
            print(
                f'>>>> [Rundate: {time.ctime()}] No schedule found for {self.year}. Skipping transactions parsing - make sure the data is available.')
            return
        try:
            df_teams = pd.read_csv(f'{self.current_directory}/league_teams/{self.year}_league_teams.csv')
        except FileNotFoundError:
            print(
                f'>>>> [Rundate: {time.ctime()}] No teams metadata found for {self.year}. Skipping transactions parsing - make sure the data is available.')
            return

        transactions = self.query.get_league_transactions()
        # go through the transactions and parse them
        for i in range(0, len(transactions)):

            trans = transactions[i]
            trans_type = trans.type
            trans_time = datetime.fromtimestamp(trans.timestamp)
            trans_datetime = trans_time.strftime('%Y-%m-%d')
            try:
                trans_week = df_weeks[df_weeks['date'] == trans_datetime]['week'].values[0]
            except:
                trans_week = 0

            trans_id = str(self.year) + "_" + str(trans_week) + "_Trans_" + str(trans.transaction_id)
            trans_status = trans.status

            try:  # some years we have no faab_bid
                faab_bid = trans.faab_bid
            except:
                faab_bid = np.nan
            if trans_type == 'add':
                player_id = trans.players[0].player_key
                player_name = trans.players[0].name.full
                destination = trans.players[0].transaction_data.destination_team_name
                destination_key = trans.players[0].transaction_data.destination_team_key
                destination_gm = self.df_league_teams[self.df_league_teams['team_key']==destination_key]['gm_name'].values[0]
                source = 'Free Agency'
                source_key = '99.l.99.t.99'
                source_type = trans.players[0].transaction_data.source_type
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', faab_bid, source, source_key,
                                               destination, destination_key, waiver_check, '', destination_gm, source)


            elif trans_type == 'drop':
                player_id = trans.players[0].player_key
                player_name = trans.players[0].name.full
                source = trans.players[0].transaction_data.source_team_name
                source_key = trans.players[0].transaction_data.source_team_key
                source_type = trans.players[0].transaction_data.source_type
                source_gm = self.df_league_teams[self.df_league_teams['team_key']==source_key]['gm_name'].values[0]
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                destination = 'Free Agency'
                destination_key = '99.l.99.t.99'
                destination_type = trans.players[0].transaction_data.destination_type
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', '', source, source_key,
                                               destination, destination_key, waiver_check, '',destination,source_gm)

            elif trans_type == 'add/drop':
                try:
                    faab_bid = trans.faab_bid
                except:
                    faab_bid = np.nan

                add = trans.players[0]
                player_id = add.player_key
                player_name = add.name.full
                # add portion
                destination = add.transaction_data.destination_team_name
                destination_key = add.transaction_data.destination_team_key
                destination_gm = self.df_league_teams[self.df_league_teams['team_key']==destination_key]['gm_name'].values[0]
                source = 'Free Agency'
                source_key = '99.l.99.t.99'
                source_type = add.transaction_data.source_type
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                trans_type = 'add'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', faab_bid, source, source_key,
                                               destination, destination_key, waiver_check, '',destination_gm, source)

                # drop portion
                drop = trans.players[1]
                player_id = drop.player_key
                player_name = drop.name.full

                source = drop.transaction_data.source_team_name
                source_key = drop.transaction_data.source_team_key
                source_type = drop.transaction_data.source_type
                source_gm = self.df_league_teams[self.df_league_teams['team_key']==source_key]['gm_name'].values[0]

                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                destination = 'Free Agency'
                destination_key = '99.l.99.t.99'
                destination_type = drop.transaction_data.destination_type
                trans_type = 'drop'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', '', source, source_key,
                                               destination, destination_key, waiver_check, '',destination,source_gm)

            elif trans_type == 'trade':
                if len(trans.picks) > 0:
                    # There will always be an even amount of picks
                    for pck in range(0, len(trans.picks)):
                        pick = trans.picks[pck]
                        player_id = '999.p.9999'
                        og_team_name = pick.original_team_name
                        og_gm_name = df_teams[df_teams['name'] == og_team_name]['gm_name'].values[0]

                        # Special case for 2021 where some GM names were changed across the year - this is more for the trades that involved the previous year's draft pick and thus their name
                        if self.year == 2021:
                            if og_gm_name == 'Kristofer':
                                og_gm_name = 'Mack'
                            if og_gm_name == 'Cole':
                                og_gm_name = 'Taylor'
                            if og_gm_name == 'Tingle':
                                og_gm_name = 'Nigel'
                        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

                        player_name = str(int(self.year) + 1) + " " + og_gm_name + " " + "Round " + str(
                            pick.round) + " Draft Pick"
                        draft_round = pick.round
                        source = pick.source_team_name
                        source_key = pick.source_team_key
                        destination = pick.destination_team_name
                        destination_key = pick.destination_team_key
                        source_gm = self.df_league_teams[self.df_league_teams['team_key'] == source_key]['gm_name'].values[0]
                        destination_gm = self.df_league_teams[self.df_league_teams['team_key'] == destination_key]['gm_name'].values[0]

                        waiver_check = 'NO'
                        df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                                       trans_id, trans_status, player_id,
                                                       player_name, draft_round,'', '', source, source_key,
                                                       destination, destination_key, waiver_check, '',destination_gm,source_gm)

                if trans.players == None:
                    continue  # this is likely a stupid 17 for 17 trade
                elif len(trans.players) == 0:
                    continue  # this is likely a stupid 17 for 17 trade
                elif len(trans.players) == 1:
                    player = trans.players[0]  # ['player']
                    player_id = player.player_key
                    player_name = player.name.full
                    destination = player.transaction_data.destination_team_name
                    destination_key = player.transaction_data.destination_team_key
                    destination_type = player.transaction_data.destination_type
                    source = player.transaction_data.source_team_name
                    source_key = player.transaction_data.source_team_key
                    source_type = player.transaction_data.source_type
                    source_gm = self.df_league_teams[self.df_league_teams['team_key'] == source_key]['gm_name'].values[0]
                    destination_gm = self.df_league_teams[self.df_league_teams['team_key'] == destination_key]['gm_name'].values[0]
                    waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                    df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                                   trans_id, trans_status, player_id,
                                                   player_name, '','', '', source, source_key,
                                                   destination, destination_key, waiver_check, '',destination_gm,source_gm)
                else:
                    for plr in range(0, len(trans.players)):
                        player = trans.players[plr]  # ['player']
                        player_id = player.player_key
                        player_name = player.name.full

                        destination = player.transaction_data.destination_team_name
                        destination_key = player.transaction_data.destination_team_key
                        destination_type = player.transaction_data.destination_type
                        source = player.transaction_data.source_team_name
                        source_key = player.transaction_data.source_team_key
                        source_type = player.transaction_data.source_type
                        source_gm = self.df_league_teams[self.df_league_teams['team_key'] == source_key]['gm_name'].values[0]
                        destination_gm = self.df_league_teams[self.df_league_teams['team_key'] == destination_key]['gm_name'].values[0]
                        waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                        df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                                       trans_id, trans_status, player_id,
                                                       player_name, '','', '', source, source_key,
                                                       destination, destination_key, waiver_check, '',destination_gm,source_gm)
            else:  # this is commish -> i don't know what to do with these
                trans_status = trans.status
                trans_time = datetime.fromtimestamp(trans.timestamp)
                trans_id = str(self.year) + "_" + str(trans_week) + "_" + str(trans.transaction_id)

        df_trans.columns = df_trans.columns.str.upper()
        df_trans.to_csv(f'{self.current_directory}/league_transactions/{self.year}_transactions.csv', index=False)

    def extract_yahoo_draft_results(self):
        print(f'[{time.ctime()}] extracting draft information for year {self.year}')
        draft_arr = []
        df_players = self.df_player_metadata
        df_teams = pd.read_csv(f'{self.current_directory}/league_teams/{str(self.year)}_league_teams.csv')
        draft = self.query.get_league_draft_results()
        for drft in range(0, len(draft)):
            draft_pick = draft[drft].clean_data_dict()
            draft_time = self.control_file['keepers'][str(self.year)]['draft_date']
            draft_type = 'draft'
            pick_number = draft_pick.get('pick', '')
            pick_round = draft_pick.get('round', '')
            player_key = draft_pick.get('player_key', '')
            player_id = draft_pick.get('player_key', '').split('.')[-1] # grab only the ID part, not the game / year code
            team_key = draft_pick.get('team_key', '')
            draft_id = str(self.year) + "_0_Draft_" + str(pick_number)

            player_name = df_players[df_players['player_id'] == int(player_id)]['yahoo_name'].values[0]
            gm_name = df_teams[df_teams['team_key'] == team_key]['gm_name'].values[0]
            team_name = df_teams[df_teams['team_key'] == team_key]['name'].values[0]
            source_key = '99.l.99.t.99'
            source = 'Free Agency'
            try: # TO-DO: Change it from player name to the player key
                keeper_check = 'KEEPER' if player_name in self.control_file['keepers'][str(self.year)][team_key] else 'NO'
            except:
                # for years 2017 and back
                keeper_check = 'NO'

            # # Grab some additional draft metadata analytics # Todo: Is there anything useful here?
            # draft_analytics = self.query.get_player_draft_analysis(player_key).clean_data_dict()
            # time.sleep(0.5)
            draft_arr.append({
                'season': self.year,
                'week': 0,
                'transaction_date': draft_time,
                'transaction_type': draft_type,
                'transaction_id': draft_id,
                'status':'successful',
                'player_id': player_id,
                'name': player_name,
                'draft_round': pick_round,
                'draft_pick': pick_number,
                'source': source,
                'source_key': source_key,
                'destination': team_name,
                'destination_key': team_key,
                'waiver': "NO",
                'keeper': keeper_check,
                'GM_Name_destination': gm_name,
                'GM_Name_source': 'Free Agency'

            })
        #print(draft_pick, draft_time, draft_type, pick_number, pick_round, player_id, team_key, player_name, gm_name, team_name, source_key, source, source, keeper_check)

        # Create a DataFrame from the list of dictionaries
        self.df_draft = pd.DataFrame(draft_arr)
        output_dir = f'{self.current_directory}/league_drafts'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_draft.csv'
        self.df_draft.columns = self.df_draft.columns.str.upper()
        self.df_draft.to_csv(output_file, index=False)

    def extract_yahoo_rosters(self):
        print(f'[{time.ctime()}] Getting yahoo team roster player info for year {self.year}')
        df_teams = pd.read_csv(f'{self.current_directory}/league_teams/{self.year}_league_teams.csv')
        # Cycle through each date in the league weeks and dates DataFrame
        # and get the roster for each team on that date
        for date in self.dates_to_check:
            iter_date = date
            print(f'[{time.ctime()}] Processing date: {iter_date}')
            date_arr = []
            df_roster_stats_date = pd.DataFrame()
            # Get the teams for this date
            for team_code in df_teams['team_id'].unique():
                team_id = df_teams[df_teams['team_id'] == team_code]['team_key'].values[0]
                team_name = df_teams[df_teams['team_id'] == team_code]['name'].values[0]
                team_gm = df_teams[df_teams['team_id'] == team_code]['gm_name'].values[0]
                # Get the roster for this team on this date
                roster = self.query.get_team_roster_player_info_by_date(team_id=team_code, chosen_date=iter_date)
                if not roster:
                    print(f'No roster found for team {team_code} on date {iter_date}')
                    continue
                # Process the roster and save it to a file or database
                # lets create a DataFrame to store the roster data into a csv
                for player in roster:
                    player_daily_metadata = player.clean_data_dict()
                    player_metadata_dict = {}
                    # extract the relevant data from the player_data dictionary
                    # there is a lot of metadata in addition to the stats, so we might split these off eventually

                    player_metadata_dict={
                        'SEASON': self.year,
                        'NAME': player_daily_metadata.get('name', {}).get('full', 'Unknown Player'),
                        'PLAYER_ID': player_daily_metadata.get('player_id', 0),
                        'PLAYER_KEY': player_daily_metadata.get('player_key', '?'),
                        'DISPLAY_POSITION': player_daily_metadata.get('display_position', '?'),
                        'INJURY_NOTE': player_daily_metadata.get('injury_note', ''),
                        #'IS_KEEPER': player_daily_metadata.get('is_keeper', {}).get('status', False),
                        #'KEEPER_COST': player_daily_metadata.get('is_keeper', {}).get('cost', ''),
                        # 'KEPT': player_daily_metadata.get('is_keeper', {}).get('kept', ''),
                        'OWNER_TEAM_KEY': team_id,
                        'OWNER_TEAM_NAME': team_name,
                        'OWNER_TEAM_GM': team_gm,
                        'SELECTED_POSITION': player_daily_metadata.get('selected_position', {}).get('position', '?'),
                        'ELIGIBLE_POSITIONS': player_daily_metadata.get('eligible_positions', []),
                        'PERCENT_OWNED': player_daily_metadata.get('percent_owned', {}).get('value', 0),
                        'PERCENT_OWNED_DELTA': player_daily_metadata.get('percent_owned', {}).get('delta', 0.0),
                    }
                    # No need for stats, I will grab them from the fine folks at hockeyreference
                    # # Loop through and grab the stats for this player
                    # convert the player_metadata_and_stats_arr to a DataFrame
                    date_arr.append(player_metadata_dict)

            date_df = pd.DataFrame(date_arr)
            date_df = self.identical_names_handler(date_df, 'yahoo') if len(date_df) > 0 else date_df
            output_dir = f'{self.current_directory}/team_rosters_by_date/{self.year}'
            os.makedirs(output_dir, exist_ok=True)
            # Save the DataFrame to a CSV file
            output_file = f"{output_dir}/{self.year}_rosters_{iter_date}.csv"
            date_df['DATE'] = iter_date
            date_df.to_csv(output_file, index=False)
            time.sleep(10)  # Sleep for 10 seconds to avoid API rate limits
            iter_date = date_df['DATE'].iloc[0]
            date_df['UID'] = date_df['DATE'] + '_' + date_df['PLAYER_ID'].astype(str)
            date_df['RUNDATE'] = time.ctime()
            date_df.drop(columns=['PLAYER_KEY'], inplace=True)
            date_df = date_df.rename(columns={'OWNER_TEAM_KEY':'OWNER_TEAM_ID'})
            #self.insert_roster_metadata(date_df, iter_date, overwrite=False)

    def parse_HR_data(self):
        import os
        import time
        import requests
        import pandas as pd
        from bs4 import BeautifulSoup
        from io import StringIO

        print(f"[{time.ctime()}] Parsing hockey-reference data for year {self.year}")
        for date in self.dates_to_check:
            date_df = pd.DataFrame()
            for url_code in self.schedule_df[self.schedule_df['Date'] == date]['URL_KEY'].to_list():
                url = f"https://www.hockey-reference.com/boxscores/{url_code}.html"
                page = requests.get(url)
                soup = BeautifulSoup(page.content, "html.parser")
                tables = soup.find_all('table')

                # Grab home team skater normal, advanced stats & goalie stats

                home_df = pd.read_html(StringIO(str(tables[2])))[0]
                home_df = home_df.rename(columns=lambda x: x if x in ['Goals', 'Assists'] else '', level=0)
                home_df.columns = [f"{col[0]}{col[1]}" for col in home_df.columns]
                home_df['TEAM'] = tables[2]['id'].split('_')[0]
                # get some metadata from the table and add it in
                rows = tables[2].find_all(['th', 'tr'])
                row_count = 0
                for row in rows:
                    cells = row.find_all('td')
                    for cell in cells:
                        if 'data-append-csv' in cell.attrs:
                            link_name = cell['data-append-csv']
                            home_df.at[row_count, 'HR_LINK_NAME'] = link_name
                            row_count += 1

                adv_df = pd.read_html(StringIO(str(tables[6])))[0]
                home_df = home_df.merge(adv_df, how='left', on='Player')
                goalie_df = pd.read_html(StringIO(str(tables[3])))[0]
                goalie_df.columns = [f"{col[1]}" for col in goalie_df.columns]
                goalie_df.drop(columns=['Rk', 'PIM', 'TOI'], inplace=True)
                home_df = home_df.merge(goalie_df, how='left', on='Player')
                home_df = home_df[home_df['Player'] != 'TOTAL']

                # Grab away team skater normal, advanced stats & goalie stats

                away_df = pd.read_html(StringIO(str(tables[4])))[0]
                away_df = away_df.rename(columns=lambda x: x if x in ['Goals', 'Assists'] else '', level=0)
                away_df.columns = [f"{col[0]}{col[1]}" for col in away_df.columns]
                away_df['TEAM'] = tables[4]['id'].split('_')[0]
                # get some metadata from the table and add it in
                rows = tables[4].find_all(['th', 'tr'])
                row_count = 0
                for row in rows:
                    cells = row.find_all('td')
                    for cell in cells:
                        if 'data-append-csv' in cell.attrs:
                            link_name = cell['data-append-csv']
                            away_df.at[row_count, 'HR_LINK_NAME'] = link_name
                            row_count += 1

                adv_df = pd.read_html(StringIO(str(tables[13])))[0]
                away_df = away_df.merge(adv_df, how='left', on='Player')
                goalie_df = pd.read_html(StringIO(str(tables[5])))[0]
                goalie_df.columns = [f"{col[1]}" for col in goalie_df.columns]
                goalie_df.drop(columns=['Rk', 'PIM', 'TOI'], inplace=True)
                away_df = away_df.merge(goalie_df, how='left', on='Player')
                away_df = away_df[away_df['Player'] != 'TOTAL']

                merge_df = pd.concat([home_df, away_df]).reset_index(drop=True)
                merge_df.drop(columns=['Rk'], inplace=True)
                date_df = pd.concat([date_df, merge_df]).reset_index(drop=True)
                print(f'Completed extraction for game {url_code} on date {date}')
                time.sleep(5)  # Be polite and avoid overwhelming the server

            for col in ['PLAYER',	'G',	'A',	'PTS',	'+/-',	'PIM',	'GOALSEV'	,'GOALSPP'	,'GOALSSH',	'GOALSGW',	'ASSISTSEV'	,'ASSISTSPP',	'ASSISTSSH',	'S'	,'S%',	'SHFT',	'TOI',	'TEAM',	'HR_LINK_NAME',	'ICF',	'SAT‑F',	'SAT‑A',	'CF%',	'CREL%',	'ZSO',	'ZSD',	'OZS%',	'HIT',	'BLK',	'DEC',	'GA',	'SA',	'SV',	'SV%',	'SO']:
                if col not in date_df.columns.to_list():
                    date_df[col] = ''

            date_df['DATE'] = date
            season = int(self.year)
            date_df['SEASON'] = season
            date_df.columns = date_df.columns.str.strip().str.upper()
            # Add a dedeuplication line on the hr_link_column
            date_df.drop_duplicates('HR_LINK_NAME', keep='first', inplace=True)
            os.makedirs(f"hr_data_extract/{season}", exist_ok=True)
            date_df.to_csv(f'hr_data_extract/{season}/HR_{date}.csv', index=False)
            print('Sleeping for a day...')
            time.sleep(30)  # Be polite and avoid overwhelming the server
######################################################################################################
######################################################################################################
######################################################################################################

    def fuzzy_match_players_to_hr(self,
            player_df,
            hr_df,
            player_name_col,
            hr_name_col,
            hr_link_name_col,
            output_match_col="HR_MATCH_NAME",
            output_link_col="HR_LINK_NAME",
            output_score_col="HR_MATCH_SCORE",
            collision_flag_col="HR_COLLISION_FLAG",
            review_flag_col="HR_REVIEW_FLAG",
            score_cutoff=80,
            review_threshold=90
    ):
        """
        Fuzzy-match player names to HR master names with:
        - aggressive normalization (ASCII only)
        - HR_NAME + HR_LINK_NAME lookup
        - collision detection
        - review flagging
        - optimized RapidFuzz matching
        """

        import re
        import unicodedata

        try:
            from rapidfuzz import fuzz
            from rapidfuzz import process as rf_process
        except ImportError:
            raise ImportError("RapidFuzz is required. Install via: pip install rapidfuzz")

        # -------------------------------
        # HARD normalization (ASCII only)
        # -------------------------------
        def normalize(name):
            if pd.isna(name):
                return ""

            name = str(name)
            first_name = name.split(' ', 1)[0]
            last_part = name.split(' ', 1)[1]
            first_name = self.first_name_dict[first_name] if first_name in self.first_name_dict.keys() else first_name
            name = first_name + ' ' + last_part

            # Remove accents / diacritics
            name = unicodedata.normalize("NFKD", name)
            name = name.encode("ascii", "ignore").decode("ascii")

            # Lowercase
            name = name.lower()

            # Keep only letters + spaces
            name = re.sub(r"[^a-z\s]", " ", name)

            # Collapse whitespace
            name = re.sub(r"\s+", " ", name).strip()

            return name

        # -------------------------------
        # Prepare HR lookup tables
        # -------------------------------
        hr_df = hr_df.copy()
        hr_df["_norm_hr_name"] = hr_df[hr_name_col].apply(normalize)

        hr_lookup = (
            hr_df
            .dropna(subset=["_norm_hr_name"])
            .drop_duplicates("_norm_hr_name")
            .set_index("_norm_hr_name")[[hr_name_col, hr_link_name_col]]
            .to_dict("index")
        )

        hr_norm_names = list(hr_lookup.keys())

        # -------------------------------
        # Fuzzy match function
        # -------------------------------
        def match_one(norm_name):
            if not norm_name:
                return None, None, None

            match = rf_process.extractOne(
                norm_name,
                hr_norm_names,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=score_cutoff
            )

            if match is None:
                return None, None, None

            norm_match, score, _ = match
            return (
                hr_lookup[norm_match][hr_name_col],
                hr_lookup[norm_match][hr_link_name_col],
                score
            )

        # -------------------------------
        # Normalize player names
        # -------------------------------
        df = player_df.copy()
        df["_norm_player_name"] = df[player_name_col].apply(normalize)

        # -------------------------------
        # Perform matching
        # -------------------------------
        results = df["_norm_player_name"].apply(match_one)

        df[output_match_col] = results.apply(lambda x: x[0])
        df[output_link_col] = results.apply(lambda x: x[1])
        df[output_score_col] = results.apply(lambda x: x[2])

        # -------------------------------
        # Review flag
        # -------------------------------
        df[review_flag_col] = (
                df[output_score_col].isna() |
                (df[output_score_col] < review_threshold)
        )

        # -------------------------------
        # Collision detection
        # -------------------------------
        collision_counts = (
            df
            .dropna(subset=[output_match_col])
            .groupby(output_match_col)
            .size()
        )

        collisions = collision_counts[collision_counts > 1].index

        df[collision_flag_col] = df[output_match_col].isin(collisions)

        # -------------------------------
        # Cleanup
        # -------------------------------
        df.drop(columns=["_norm_player_name"], inplace=True)

        return df





