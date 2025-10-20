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
        #self.extract_league_stat_categories()
        self.extract_league_weeks_and_dates()
        self.extract_yahoo_league_standings()
        self.ref_list = pd.read_csv(f'{self.current_directory}/manual_data/Hockey_Team_Codes.csv')

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
        if self.year == 2012:
            print('[extract_yahoo_league_teams] - Not processing teams for 2012 as Yahoo API bugs out')
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
            # Some GMs have different names in different seasons, etc.
            gm_name =  manager.get('nickname', '')
            team_name = team.get('name', '').decode('utf-8')
            if team_name == "Vintage'tingle'Boar":
                gm_name = 'Tingle'
            elif team_name == "The Nerve":
                gm_name = 'Ira'
            elif (team_name == "#G") | (team_name == "Grampa Jarzabek"):
                gm_name = 'A'
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
        if self.year == 2012:
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
        df_nhl_codes = pd.read_csv(f'{self.current_directory}/manual_data/Hockey_Team_Codes.csv')
        # Fetch and parse the webpage
        page = requests.get(f"https://www.hockey-reference.com/leagues/NHL_{str(int(self.year) + 1)}_games.html")
        soup = BeautifulSoup(page.content, 'html.parser')
        items = soup.find(id="all_games").find_all(class_="left")

        # Initialize DataFrame and variables
        self.df_sched = pd.DataFrame(columns=['date', 'away', 'home'])
        counter = 0

        # Process items to extract game data
        for i, item in enumerate(items[5:], start=5):  # Skip first 5 items
            if i % 5 == 0:  # Date
                date = item.find("a").getText() if item.find("a") else item.getText()
                date = datetime.strptime(date, "%Y-%m-%d")
            elif (i - 2) % 5 == 0:  # Away team
                team_A = item.find("a").getText()
            elif (i - 3) % 5 == 0:  # Home team
                team_B = item.find("a").getText()
            elif (i - 4) % 5 == 0:  # Save game data
                self.df_sched.loc[counter] = [date.strftime("%Y-%m-%d"), team_A, team_B]
                counter += 1

        # Add game counts for each team
        for team in self.df_sched['home'].unique():
            game_counter = 1
            for j, row in self.df_sched.iterrows():
                if team in row['home']:
                    self.df_sched.at[j, 'home_count'] = game_counter
                    game_counter += 1
                elif team in row['away']:
                    self.df_sched.at[j, 'away_count'] = game_counter
                    game_counter += 1

        list_of_games = [
            a['href'][11:-5] for a in soup.find_all('a', href=True)
            if a['href'].startswith('/boxscores/') and len(a['href'][11:-5]) > 11
        ]
        print(f'>>>> [Rundate: {time.ctime()}] Got list of boxscore codes')

        # Map the URLCODE to the schedule DataFrame based on the game date
        self.df_sched['urlcode'] = self.df_sched.apply(
            lambda x: str(x['date'].replace('-',''))+'0'+df_nhl_codes[df_nhl_codes['Team_Name']==x['home']]['Code'].values[0], axis=1)

        # Save the schedule to a CSV file
        output_dir = f'{self.current_directory}/season_schedules'
        os.makedirs(output_dir, exist_ok=True)
        self.df_sched.to_csv(f'{output_dir}/{self.year}_NHL_Schedule.csv', index=False)

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
        week_arr = []
        for week_data in self.game_weeks:
            week_data = week_data.clean_data_dict()
            week = week_data.get('week', '?')
            week_start = week_data.get('start', '?')
            week_end = week_data.get('end', '?')
            date_range = pd.date_range(start=week_start, end=week_end)
            for date in date_range:
                week_arr.append({
                    'season': self.year,
                    'week': week,
                    'date': date,
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
                is_playoff = matchup_data.get('is_playoff', 0)
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
                                         'keeper'
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
                destination_type = trans.players[0].transaction_data.destination_type
                source = 'Free Agency'
                source_key = '99.l.99.t.99'
                source_type = trans.players[0].transaction_data.source_type
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', faab_bid, source, source_key,
                                               destination, destination_key, waiver_check, '')


            elif trans_type == 'drop':
                player_id = trans.players[0].player_key
                player_name = trans.players[0].name.full
                source = trans.players[0].transaction_data.source_team_name
                source_key = trans.players[0].transaction_data.source_team_key
                source_type = trans.players[0].transaction_data.source_type
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                destination = 'Free Agency'
                destination_key = '99.l.99.t.99'
                destination_type = trans.players[0].transaction_data.destination_type
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', '', source, source_key,
                                               destination, destination_key, waiver_check, '')

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
                destination_type = add.transaction_data.destination_type
                source = 'Free Agency'
                source_key = '99.l.99.t.99'
                source_type = add.transaction_data.source_type
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                trans_type = 'add'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', faab_bid, source, source_key,
                                               destination, destination_key, waiver_check, '')

                # drop portion
                drop = trans.players[1]
                player_id = drop.player_key
                player_name = drop.name.full

                source = drop.transaction_data.source_team_name
                source_key = drop.transaction_data.source_team_key
                source_type = drop.transaction_data.source_type
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                destination = 'Free Agency'
                destination_key = '99.l.99.t.99'
                destination_type = drop.transaction_data.destination_type
                trans_type = 'drop'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', '', source, source_key,
                                               destination, destination_key, waiver_check, '')

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
                        waiver_check = 'NO'
                        df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                                       trans_id, trans_status, player_id,
                                                       player_name, draft_round,'', '', source, source_key,
                                                       destination, destination_key, waiver_check, '')

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
                    waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                    df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                                   trans_id, trans_status, player_id,
                                                   player_name, '','', '', source, source_key,
                                                   destination, destination_key, waiver_check, '')
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
                        waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                        df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                                       trans_id, trans_status, player_id,
                                                       player_name, '','', '', source, source_key,
                                                       destination, destination_key, waiver_check, '')
            else:  # this is commish -> i don't know what to do with these
                trans_status = trans.status
                trans_time = datetime.fromtimestamp(trans.timestamp)
                trans_id = str(self.year) + "_" + str(trans_week) + "_" + str(trans.transaction_id)

        df_trans.columns = df_trans.columns.str.upper()
        df_trans.to_csv(f'{self.current_directory}/league_transactions/{self.year}_transactions.csv', index=False)

    def extract_yahoo_draft_results(self):
        print(f'[{time.ctime()}] extracting draft information for year {self.year}')
        draft_arr = []
        df_players = pd.read_csv(f'{self.current_directory}/player_metadata/player_metadata_master_list.csv')
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

            player_name = df_players[df_players['player_key'] == int(player_id)]['player_name'].values[0]
            gm_name = df_teams[df_teams['team_key'] == team_key]['gm_name'].values[0]
            team_name = df_teams[df_teams['team_key'] == team_key]['name'].values[0]
            source_key = '99.l.99.t.99'
            source = 'Free Agency'
            try: # TO-DO: Change it from player name to the player key
                keeper_check = 'KEEPER' if player_name in self.control_file['keepers'][str(self.year)][team_key] else 'NO'
            except:
                # for years 2017 and back
                keeper_check = 'NO'

            # Grab some additional draft metadata analytics
            draft_analytics = self.query.get_player_draft_analysis(player_key).clean_data_dict()
            time.sleep(0.5)
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
        df_league_weeks_and_dates = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')
        df_teams = pd.read_csv(f'{self.current_directory}/league_teams/{self.year}_league_teams.csv')
        df_stat_codes = pd.read_csv(f'{self.current_directory}/league_stat_categories/{self.year}_league_stat_categories.csv')
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

    def parse_HR_data(self):
        import os
        import time
        import requests
        import pandas as pd
        from bs4 import BeautifulSoup
        from datetime import datetime

        def get_team_code(team_name):
            code = self.ref_list.loc[self.ref_list['Team_Name'] == team_name, 'Code']
            if not code.empty:
                code = code.values[0].strip()
            else:
                code = team_name[:3].upper()
            # if code == 'VEG':
            #     code = 'VGK'
            return code


        def data_extractor(team_code, team, soup):
            print('>>>> Extracting HR data for team:', team, '(', team_code, ')')
            tables_dict = {
                f'{team_code}_goalies': {
                    'row_key': 1
                },
                f'{team_code}_skaters': {
                    'row_key': 1
                },
                f'{team_code}_adv_ALLAll': {
                    'row_key': 0
                }
            }

            totals = []
            for table_id in tables_dict.keys():
                row_key = tables_dict[table_id]['row_key']
                table = soup.find('table', id=table_id)
                rows = table.find_all('tr')

                # Extract headers
                header_row = rows[row_key]
                headers = [h.get_text(strip=True) for h in header_row.find_all(['th', 'td']) if
                           h.get_text(strip=True)]
                data = []
                player_names = []
                player_codes = []
                for row in rows[0:]:
                    cells = row.find_all('td')
                    if not cells:
                        continue

                    # Grab text values
                    row_data = [c.get_text(strip=True) for c in cells]
                    if (row_data[0] != 'TOTAL') and (row_data[0] != ''):  # this is the total in skaters
                        data.append(row_data)
                    else:
                        pass

                    # Player name + csv-code
                    player_cell = row.find('td', {'data-stat': 'player'})
                    if player_cell:
                        player_names.append(player_cell.get_text(strip=True))
                        player_codes.append(player_cell.get("data-append-csv"))
                    else:
                        player_names.append(None)
                        player_codes.append(None)

                if not data:
                    return

                df = pd.DataFrame(data, columns=headers[1:])
                if 'adv' in table_id:
                    # extend the adv data table by blank rows to account for the goalie stats
                    for i in range(0, len(totals[0])):
                        df.loc[len(df)] = (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
                if 'goalie' in table_id or 'skaters' in table_id:
                    df.insert(1, "Player_Code", player_codes[:len(df)])
                totals.append(df)
            #####################################
            # Now we do stuff outside to stitch it together
            df_goalie = totals[0]
            df_skater = totals[1]
            df_adv = totals[2]
            df_skaters_and_advanced = pd.concat([df_skater, df_adv], axis=1)
            df_all = df_skaters_and_advanced.merge(df_goalie[['Player', 'DEC', 'GA', 'SA', 'SV', 'SV%', 'SO']],
                                                   how='outer', on='Player')
            df_all['WIN'] = df_all['DEC'].apply(lambda x: 1 if x == 'W' else 0)
            df_all['LOSS'] = df_all['DEC'].apply(lambda x: 1 if x == 'L' else 0)

            # # Add metadata
            df_all["Date"] = date
            df_all["Team"] = team
            df_all["Team_Code"] = team_code
            return df_all


        print(f"[{time.ctime()}] Parsing hockey-reference data for year {self.year}")
        games_in_day_df = pd.DataFrame()
        for date in self.dates_to_check:
            games_in_day_df = pd.DataFrame()
            df_sched = pd.read_csv(f"{self.current_directory}/season_schedules/{self.year}_NHL_Schedule.csv")
            url_list = df_sched[df_sched["date"] == date]["urlcode"].unique()

            for url in url_list:
                urlGame = f"https://www.hockey-reference.com/boxscores/{url}.html"
                print(urlGame)
                page = requests.get(urlGame)
                if page.status_code == 429:
                    print(f"[Rundate: {time.ctime()}] Rate limit hit, exiting parser.")
                    return
                soup = BeautifulSoup(page.content, "html.parser")
                test = soup.find("div", id="inner_nav")
                list_metadata = [x for x in list(test)[1].text.split("\n") if x.strip()]
                print(list_metadata)
                home_team = list_metadata[-1].split("Schedule/Results")[0].strip()
                away_team = list_metadata[-2].split("Schedule/Results")[0].strip()
                home_code = get_team_code(home_team)
                away_code = get_team_code(away_team)
                df_home = data_extractor(home_code,home_team,soup)
                df_away = data_extractor(away_code,away_team,soup)
                game_df = pd.concat([df_home,df_away])
                games_in_day_df = pd.concat([games_in_day_df,game_df])

            # Cleanup
            if not games_in_day_df.empty:
                games_in_day_df.columns = games_in_day_df.columns.str.upper()
                games_in_day_df = self.identical_names_handler(games_in_day_df, "hr")
                os.makedirs(f"{self.current_directory}/hr_data/{self.year}", exist_ok=True)
                games_in_day_df.to_csv(f'{self.current_directory}/hr_data/{self.year}/hr_data_{date}.csv',index=False)
                # For data quality -> there are identical columns here that need to be merged, so load the dataframe again and sum the duplicates that are now tagged accordingly
                games_in_day_df = pd.read_csv(f'{self.current_directory}/hr_data/{self.year}/hr_data_{date}.csv')
                games_in_day_df['PPP'] = games_in_day_df['PP'] + games_in_day_df['PP.1']
                games_in_day_df['SHP'] = games_in_day_df['SH'] + games_in_day_df['SH.1']
                games_in_day_df['EVP'] = games_in_day_df['EV'] + games_in_day_df['EV.1']
                games_in_day_df.drop(['PP','SH','EV','PP.1','SH.1','EV.1'],axis=1,inplace=True)
                games_in_day_df.to_csv(f'{self.current_directory}/hr_data/{self.year}/hr_data_{date}.csv',index=False)

                time.sleep(40)
######################################################################################################
######################################################################################################
######################################################################################################

    def fuzzy_outer_merge_hr(self):
        """
        Outer merge Yahoo and HR on fuzzy name matching.
        Keeps all rows from both dataframes, with match metadata.
        """
        def normalize_name(fullname: str) -> str:
            """Lowercase, strip accents, remove punctuation/extra spaces.
                Also run it through a name normalizer to convert common variations"""
            if fullname is None:
                return ""
            fullname_str = str(fullname)
            first_name = fullname_str.split(" ")[0]
            last_name = " ".join(fullname_str.split(" ")[1:]) if len(fullname_str.split(" ")) > 1 else ""

            name_dict = {
                'Alexander': 'Alex',
                'Anthony': 'Tony',
                'Benjamin': 'Ben',
                'Cameron': 'Cam',
                'Christopher': 'Chris',
                'Daniel': 'Dan',
                'David': 'Dave',
                'Edward': 'Ed',
                'Gregory': 'Greg',
                'James': 'Jim',
                'Jacob':'Jake',
                'Jonathan': 'John',
                'Johnathan': 'John',
                'Joseph': 'Joe',
                'Matthew': 'Matt',
                'Michael': 'Mike',
                'Mitchell':'Mitch',
                'Nicholas': 'Nick',
                'Patrick': 'Pat',
                'Richard': 'Rich',
                'Robert': 'Bob',
                'Steven': 'Steve',
                'Thomas': 'Tom',
                'Timothy': 'Tim',
                'William': 'Will',
                'Zachary': 'Zach'}
            first_name = name_dict[first_name] if first_name in name_dict.keys() else first_name
            clean_name = first_name + " " + last_name
            d = unicodedata.normalize("NFKD", clean_name).encode("ascii", "ignore").decode("utf-8")
            final = " ".join(re.sub(r"[^a-z ]", " ", d.lower()).split())
            return final

        def levenshtein(s1: str, s2: str) -> int:
            """Compute Levenshtein distance between two strings."""
            if len(s1) < len(s2):
                return levenshtein(s2, s1)
            if len(s2) == 0:
                return len(s1)

            prev_row = range(len(s2) + 1)
            for i, c1 in enumerate(s1):
                curr_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = prev_row[j + 1] + 1
                    deletions = curr_row[j] + 1
                    substitutions = prev_row[j] + (c1 != c2)
                    curr_row.append(min(insertions, deletions, substitutions))
                prev_row = curr_row
            return prev_row[-1]

        def levenshtein_ratio(s1: str, s2: str) -> float:
            """Convert distance to similarity ratio (0–100)."""
            if not s1 and not s2:
                return 100.0
            dist = levenshtein(s1, s2)
            return 100.0 * (1 - dist / max(len(s1), len(s2)))

        ############
        # MAIN BODY
        ############
        for date in self.dates_to_check:
            ########################
            df_yahoo = pd.read_csv(f'{self.current_directory}/team_rosters_by_date/{self.year}/{self.year}_rosters_{date}.csv')
            try:
                df_hr = pd.read_csv(f'{self.current_directory}/hr_data/{self.year}/hr_data_{date}.csv')
            except:
                print(f'No HR data found for date {date} in year {self.year}. Skipping.')
                continue
            if len(df_hr) == 0:
                print(f'No data to fuzzy merge for date {date} in year {self.year} -> likely a day off')
                continue
            threshold = 85
            hr_col = 'PLAYER'
            yahoo_col = 'NAME'
            #########################
            # Normalize names
            hr_names = df_hr[hr_col].dropna().astype(str).tolist()
            yahoo_names = df_yahoo[yahoo_col].dropna().astype(str).tolist()
            hr_norm = {name: normalize_name(name) for name in hr_names}
            yahoo_norm = {name: normalize_name(name) for name in yahoo_names}

            # Track matched Yahoo players
            matched_yahoo = set()
            merged_rows = []

            # Pass 1: go through HR Data and try to find Yahoo matches
            for _, n_row in df_hr.iterrows():
                n_name = str(n_row[hr_col])
                n_norm = normalize_name(n_name)

                best_score, best_orig = -1, None
                for orig, norm in yahoo_norm.items():
                    score = levenshtein_ratio(n_norm, norm)
                    if score > best_score:
                        best_score, best_orig = score, orig

                match_ok = best_score >= threshold
                y_row = df_yahoo[df_yahoo[yahoo_col] == best_orig].iloc[0].to_dict() if match_ok else {}

                if match_ok:
                    matched_yahoo.add(best_orig)

                row = {**n_row, **y_row}
                row.update({
                    "Yahoo_BestGuess": best_orig,
                    "Score": round(best_score, 1) if best_score >= 0 else 0,
                    "MATCH": match_ok
                })
                merged_rows.append(row)

            # Pass 2: add unmatched Yahoo players
            for _, y_row in df_yahoo.iterrows():
                y_name = str(y_row[yahoo_col])
                if y_name not in matched_yahoo:
                    row = {**y_row}
                    row.update({
                        hr_col: None,
                        "Yahoo_BestGuess": y_name,
                        "Score": 0,
                        "MATCH": False
                    })
                    merged_rows.append(row)

            df_out =  pd.DataFrame(merged_rows)
            df_out['DATE'] = date
            df_out['SEASON'] = self.year
            df_out['WEEK']= pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv').set_index('date').loc[date]['week'] if date in pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')['date'].values else 0
            df_out.columns = df_out.columns.str.upper()
            df_out = df_out[['SEASON',
            'WEEK',
            'DATE',
            'PLAYER',
            'NAME',
            'PLAYER_ID',
            'PLAYER_KEY',
            'TEAM',
            'TEAM_CODE',
            'SCORE',
            'MATCH',
            'DISPLAY_POSITION',
            'INJURY_NOTE',
            'OWNER_TEAM_KEY',
            'OWNER_TEAM_NAME',
            'OWNER_TEAM_GM',
            'SELECTED_POSITION',
            'ELIGIBLE_POSITIONS',
            'PERCENT_OWNED',
            'PERCENT_OWNED_DELTA',
            'G',
            'A',
            'PTS' ,
            'PPP',
            'SHP',
            '+/-'    ,
            'PIM',
            'GW',
            'S',
            'S%',
            'HIT',
            'BLK',
            'SHFT',
            'TOI',
            'ICF',
            'SAT‑F',
            'SAT‑A',
            'CF%',
            'CREL%' ,
            'ZSO',
            'ZSD',
            'OZS%',
            'DEC',
            'GA',
            'SA',
            'SV',
            'SV%' ,
            'SO',
            'WIN',
            'LOSS']]

            os.makedirs(f'{self.current_directory}/merged_daily_data/{self.year}', exist_ok=True)
            df_out.to_csv(f'{self.current_directory}/merged_daily_data/{self.year}/{self.year}_merged_yahoo_hr_{date}.csv',
                          index=False)

    def post_processor_matchup_matchups(self):
        # I want to use this to create a matchup dataframe containing each week's data
        # and also do a sanity check on the results relative to Yahoo's reported results

        print(f'[{time.ctime()}] Post-processing matchup metadata for year {self.year}')
        try:
            df_scoreboard = pd.read_csv(f'{self.current_directory}/league_scoreboards_by_week/{self.year}_league_scoreboards.csv')
        except FileNotFoundError:
            print(f'>>>> [Rundate: {time.ctime()}] No matchup data found for {self.year}. Skipping post-processing - make sure the data is available.')
            return
        try:
            df_weeks = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')
        except FileNotFoundError:
            print(f'>>>> [Rundate: {time.ctime()}] No schedule found for {self.year}. Skipping post-processing - make sure the data is available.')
            return
        for week in df_weeks['week'].unique():
            print(f'Post-processing week {week}...')
            df_week_matchups = pd.DataFrame()

            # Stitch together all the dates in this week
            for date in df_weeks[df_weeks['week'] == week]['date'].unique():
                # grab the associated fuzzy-matched data for this date, stitch them together for the entire week
                try:
                    df_fuzzy = pd.read_csv(f'{self.current_directory}/merged_daily_data/{self.year}/{self.year}_merged_yahoo_hr_{date}.csv')
                except FileNotFoundError:
                    #print(f'No fuzzy-merged data found for date {date}. Skipping.')
                    continue
                df_week_matchups = pd.concat([df_week_matchups,df_fuzzy])
            if len(df_week_matchups)==0:
                #print(f'No fuzzy-merged data found for week {week}. Skipping.')
                continue
            # Now we have the full week's fuzzy-merged data in df_week_matchups
            df_week_matchups = df_week_matchups[(df_week_matchups['PLAYER']!=0)&(df_week_matchups['PLAYER'].notna())]
            df_week_matchups['PLAY_OR_BENCH'] = df_week_matchups.apply(lambda row: 'PLAY' if row['SELECTED_POSITION'] in ['C','LW','RW','D','G','Util'] else 'BENCH', axis=1)
            df_week_matchups['GTOI'] = 0
            mask = df_week_matchups["DISPLAY_POSITION"].isin(['G'])
            df_week_matchups.loc[mask, "GTOI"] = df_week_matchups.loc[mask, "TOI"]
            # convert TOI and GTOI to float minutes
            df_week_matchups['TOI'] = df_week_matchups['TOI'].apply(lambda x: float(x.split(':')[0]) + float(x.split(':')[1]) / 60 if isinstance(x, str) and ':' in x else 0)
            df_week_matchups['GTOI'] = df_week_matchups['GTOI'].apply(lambda x: float(x.split(':')[0]) + float(x.split(':')[1]) / 60 if isinstance(x, str) and ':' in x else 0)
            df_week_matchups['MISSED_START'] = 0 # initialize as 0, then check;if you find a missed start, flag as 1
            # Missed start check
            # A bit greasy, maybe this can be refactored once it works # todo refactor
            for team in df_week_matchups['OWNER_TEAM_NAME'].unique():
                df_team = df_week_matchups[df_week_matchups['OWNER_TEAM_NAME'] == team]
                for date in df_team['DATE'].unique():
                    df_team_date = df_team[df_team['DATE'] == date]
                    # Derive the number of available spots
                    centres_avail = 2 - len(df_team_date[df_team_date['SELECTED_POSITION'] == 'C'])
                    left_wings_avail = 2-  len(df_team_date[df_team_date['SELECTED_POSITION'] == 'LW'])
                    right_wings_avail =2- len(df_team_date[df_team_date['SELECTED_POSITION'] == 'RW'])
                    defencemen_avail = 4- len(df_team_date[df_team_date['SELECTED_POSITION'] == 'D'])
                    util_avail = 1- len(df_team_date[df_team_date['SELECTED_POSITION'] == 'Util'])
                    for player in df_team_date['PLAYER'].unique():
                        if df_team_date[(df_team_date['PLAYER'] == player)]['SELECTED_POSITION'].values[0] == 'BN':
                            possible_positions = df_team_date[(df_team_date['PLAYER'] == player)]['DISPLAY_POSITION'].values[0].split(',')
                            for pos in possible_positions:
                                pos = pos.strip()
                                if pos == 'C' and (centres_avail > 0 or util_avail > 0):
                                    df_week_matchups.loc[(df_week_matchups['PLAYER'] == player) & (df_week_matchups['DATE'] == date), 'MISSED_START'] = 1
                                elif pos == 'LW' and (left_wings_avail > 0 or util_avail > 0):
                                    df_week_matchups.loc[(df_week_matchups['PLAYER'] == player) & (df_week_matchups['DATE'] == date), 'MISSED_START'] = 1
                                elif pos == 'RW' and (right_wings_avail > 0  or util_avail > 0):
                                    df_week_matchups.loc[(df_week_matchups['PLAYER'] == player) & (df_week_matchups['DATE'] == date), 'MISSED_START'] = 1
                                elif pos == 'D' and (defencemen_avail > 0  or util_avail > 0):
                                    df_week_matchups.loc[(df_week_matchups['PLAYER'] == player) & (df_week_matchups['DATE'] == date), 'MISSED_START'] = 1
                                else:
                                    # No change - player couldn't be played, so it's all good. Already initialized to 0
                                    pass


            # Now we can cycle through each GM and PLAY_OR_BENCH to sum up the appropriate stats
            df_week_matchups_play = df_week_matchups[df_week_matchups['PLAY_OR_BENCH'] == 'PLAY'].fillna(0)
            df_week_matchups_play = df_week_matchups_play[df_week_matchups_play['PLAYER']!=0]
            df_week_matchups_play['SKATER_COUNT'] = df_week_matchups_play['SELECTED_POSITION'].apply(lambda row: 1 if row in ['C','LW','RW','D','Util'] else 0)
            df_week_matchups_play['GOALIE_COUNT'] = df_week_matchups_play['SELECTED_POSITION'].apply(lambda row: 1 if row in ['G'] else 0)

            df_week_matchups_bench = df_week_matchups[df_week_matchups['PLAY_OR_BENCH'] == 'BENCH'].fillna(0)
            df_week_matchups_bench = df_week_matchups_bench[df_week_matchups_bench['PLAYER']!=0]
            df_week_matchups_bench['SKATER_COUNT'] = df_week_matchups_bench['ELIGIBLE_POSITIONS'].apply(lambda row: 1 if row != "['G']" else 0)
            df_week_matchups_bench['GOALIE_COUNT'] = df_week_matchups_bench['ELIGIBLE_POSITIONS'].apply(lambda row: 1 if row == "['G']" else 0)

            # Grab all the "useful" columns
            df_week_play_summary = df_week_matchups_play.groupby(['OWNER_TEAM_KEY','OWNER_TEAM_NAME','OWNER_TEAM_GM','PLAY_OR_BENCH']).agg({
                "G":"sum",
                "A":"sum",
                "+/-": "sum",
                "PIM": "sum",
                "PPP":"sum",
                "SHP":"sum",
                "S": "sum",
                "GW":"sum",
                "HIT":"sum",
                "BLK":"sum",
                "WIN":"sum",
                "LOSS": "sum",
                "SV": "sum",
                "GA": "sum",
                "SA": "sum",
                "SO": "sum",
                "TOI": "sum",
                "GTOI": "sum",
                "SKATER_COUNT": "sum",
                "GOALIE_COUNT": "sum",
                "MISSED_START": "sum"
            }).reset_index()

            df_week_play_summary['S%'] = round(df_week_play_summary['G'] / df_week_play_summary['S'].replace(0, np.nan),3)
            df_week_play_summary['SV%'] = round(df_week_play_summary['SV'] / df_week_play_summary['SA'].replace(0, np.nan),3)
            df_week_play_summary['GAA'] = round((df_week_play_summary['GA'] * 60) / df_week_play_summary['GTOI'].replace(0, np.nan),2)
            df_week_play_summary['GOALIE_STARTS_MET'] = df_week_play_summary['GOALIE_COUNT'].apply(lambda x: True if x >= 3 else False)

            # Apply minimum goalie count penalty
            df_week_play_summary['WIN'] = df_week_play_summary.apply(lambda row: row['WIN'] if row['GOALIE_STARTS_MET'] else -1, axis=1)
            df_week_play_summary['LOSS'] = df_week_play_summary.apply(lambda row: row['LOSS'] if row['GOALIE_STARTS_MET'] else 10, axis=1)
            df_week_play_summary['SV'] = df_week_play_summary.apply(lambda row: row['SV'] if row['GOALIE_STARTS_MET'] else 0, axis=1)
            df_week_play_summary['GA'] = df_week_play_summary.apply(lambda row: row['GA'] if row['GOALIE_STARTS_MET'] else 100, axis=1)
            df_week_play_summary['SA'] = df_week_play_summary.apply(lambda row: row['SA'] if row['GOALIE_STARTS_MET'] else -1, axis=1)
            df_week_play_summary['SO'] = df_week_play_summary.apply(lambda row: row['SO'] if row['GOALIE_STARTS_MET'] else -1, axis=1)
            df_week_play_summary['SV%'] = df_week_play_summary.apply(lambda row: row['SV%'] if row['GOALIE_STARTS_MET'] else 0.5, axis=1)
            df_week_play_summary['GAA'] = df_week_play_summary.apply(lambda row: row['GAA'] if row['GOALIE_STARTS_MET'] else 10, axis=1)



            # Agg the bench players now
            df_week_bench_summary = df_week_matchups_bench.groupby(['OWNER_TEAM_KEY','OWNER_TEAM_NAME','OWNER_TEAM_GM','PLAY_OR_BENCH']).agg({
                "G":"sum",
                "A":"sum",
                "+/-": "sum",
                "PIM": "sum",
                "PPP":"sum",
                "SHP":"sum",
                "S": "sum",
                "GW":"sum",
                "HIT":"sum",
                "BLK":"sum",
                "WIN":"sum",
                "LOSS": "sum",
                "SV": "sum",
                "GA": "sum",
                "SA": "sum",
                "SO":"sum",
                "TOI": "sum",
                "GTOI": "sum",
                "SKATER_COUNT": "sum",
                "GOALIE_COUNT": "sum",
                "MISSED_START": "sum"
            }).reset_index()

            df_week_bench_summary['S%'] = round(df_week_bench_summary['G'] / df_week_bench_summary['S'].replace(0, np.nan),3)
            df_week_bench_summary['SV%'] = round(df_week_bench_summary['SV'] / df_week_bench_summary['SA'].replace(0, np.nan),3)
            df_week_bench_summary['GAA'] = round((df_week_bench_summary['GA'] * 60) / df_week_bench_summary['GTOI'].replace(0, np.nan),2)

            df_week_summary = pd.concat([df_week_play_summary, df_week_bench_summary])
            # initialize the dummy columns that we will populate
            df_week_summary['MATCHUP_ID'] = 0
            df_week_summary['YAHOO_SCORE'] = 0
            df_week_summary['YAHOO_RESULT'] = 0

            # Now let's link up some data from the league_scoreboards and see where the differences are
            df_scoreboard_week = df_scoreboard[df_scoreboard['week']==week]
            for matchup_count in df_scoreboard_week['matchup']:
                df_scoreboard_matchup = df_scoreboard_week[df_scoreboard_week['matchup']==matchup_count]
                team_a_key =  df_scoreboard_matchup['team_a_key'].iloc[0]
                team_b_key =  df_scoreboard_matchup['team_b_key'].iloc[0]
                is_playoffs = df_scoreboard_matchup['is_playoff'].iloc[0]

                mask = df_week_summary["OWNER_TEAM_KEY"].isin([team_a_key, team_b_key])
                df_week_summary.loc[mask, "MATCHUP_ID"] = matchup_count
                df_week_summary.loc[mask, "IS_PLAYOFF"] = is_playoffs

                team_a_score = df_scoreboard_matchup['team_a_total_points'].iloc[0]
                team_a_result = df_scoreboard_matchup['team_a_result'].iloc[0]
                mask = df_week_summary["OWNER_TEAM_KEY"].isin([team_a_key])
                df_week_summary.loc[mask, "YAHOO_SCORE"] = team_a_score
                df_week_summary.loc[mask, "YAHOO_RESULT"] = team_a_result

                team_b_score = df_scoreboard_matchup['team_b_total_points'].iloc[0]
                team_b_result = df_scoreboard_matchup['team_b_result'].iloc[0]
                mask = df_week_summary["OWNER_TEAM_KEY"].isin([team_b_key])
                df_week_summary.loc[mask, "YAHOO_SCORE"] = team_b_score
                df_week_summary.loc[mask, "YAHOO_RESULT"] = team_b_result

            # now let's compare to our derived stats and see if they align
            cols_of_interest = self.stats_for_year
            reverse_cols = ['GAA', 'LOSS']

            # Initialize score column
            df_week_summary['CALC_SCORE'] = 0
            df_week_summary_play = df_week_summary[df_week_summary['PLAY_OR_BENCH']=='PLAY']
            df_week_summary_bench = df_week_summary[df_week_summary['PLAY_OR_BENCH']=='BENCH']


            # Process each matchup separately
            for matchup_val, group in df_week_summary_play.groupby("MATCHUP_ID", sort=False):
                # Use reset_index to avoid issues with duplicate indices
                group_reset = group.reset_index()
                arr = group_reset[cols_of_interest].to_numpy(dtype=float)
                n = arr.shape[0]
                wins = np.zeros(n, dtype=int)

                # Compare each column
                for j, col in enumerate(cols_of_interest):
                    col_vals = arr[:, j]
                    if col in reverse_cols:
                        comp = col_vals[:, None] < col_vals[None, :]
                    else:
                        comp = col_vals[:, None] > col_vals[None, :]
                    wins += comp.sum(axis=1)

                # Assign scores row by row using original indices
                for i, idx in enumerate(group_reset['index']):
                    df_week_summary_play.at[idx, 'CALC_SCORE'] = wins[i]

            # derive the roto-score for each GM
            for col in cols_of_interest:
                if col in reverse_cols:
                    df_week_summary_play[col + '_RANK'] = df_week_summary_play[col].rank(ascending=False,pct=True).round(2)
                else:
                    df_week_summary_play[col + '_RANK'] = df_week_summary_play[col].rank(ascending=True,pct=True).round(2)
            df_week_summary_play['ROTO_SCORE'] = df_week_summary_play[[col + '_RANK' for col in cols_of_interest]].sum(axis=1)

            # Check if there are any goalie failures (i.e. no goalie played in the week)

            df_week_summary = pd.concat([df_week_summary_bench,df_week_summary_play])


            # Finally, let's do a sanity check on the CALC_SCORE and SCORE columns to see where the deltas are
            for i in range(0,len(df_week_summary_play)):
                if df_week_summary_play['YAHOO_SCORE'].iloc[i] != df_week_summary_play['CALC_SCORE'].iloc[i]:
                    print(f"Found a delta in Week {week} Matchup {df_week_summary_play['MATCHUP_ID'].iloc[i]} {df_week_summary_play['OWNER_TEAM_GM'].iloc[i]}, as Yahoo score: {df_week_summary_play['YAHOO_SCORE'].iloc[i]} and calc score: {df_week_summary_play['CALC_SCORE'].iloc[i]}")

            df_week_summary = df_week_summary[df_week_summary['OWNER_TEAM_KEY']!=0]
            # And output the data
            df_week_summary = df_week_summary[[
            'OWNER_TEAM_KEY',
            'OWNER_TEAM_NAME',
            'OWNER_TEAM_GM',
            'PLAY_OR_BENCH',
            'G',
            'A',
            '+/-',
            'PIM',
            'PPP',
            'SHP',
            'S',
            'S%',
            'HIT',
            'BLK',
            'GW',
            'WIN',
            'LOSS',
            'GA',
            'GAA',
            'SV',
            'SA',
            'SV%',
            'SO',
            'TOI',
            'GTOI',
            'SKATER_COUNT',
            'GOALIE_COUNT',
            'MISSED_START',
            'GOALIE_STARTS_MET',
            'MATCHUP_ID',
            'YAHOO_SCORE',
            'YAHOO_RESULT',
            'IS_PLAYOFF',
            'CALC_SCORE',
            'ROTO_SCORE',
            ]]
            os.makedirs(f'{self.current_directory}/matchup_summaries_by_week/{self.year}', exist_ok=True)
            df_week_summary.to_csv(f'{self.current_directory}/matchup_summaries_by_week/{self.year}/{self.year}_matchup_summary_week_{week}.csv', index=False)

    #
    #
    # def duckdb_test(self):
    #     con = duckdb.connect("mydata.duckdb")
    #
    #     # Create table from CSV and persist
    #     con.execute("""
    #         CREATE TABLE roster_data AS
    #         SELECT *
    #         FROM read_csv_auto('team_rosters_by_date/*.csv')
    #     """)
    #
    #     # Now it's stored inside mydata.duckdb
    #     df = con.execute("SELECT COUNT(*) FROM roster_data").df()
    #
    #     print(df.head())

    # Let's start porting over some of the old functions from the previous pipeline here for easier access
    def super_stitcher(self):
        print(f'[{time.ctime()}] Stitching together all fuzzy-merged data for year {self.year}')
        df_total = pd.DataFrame()
        for file in list(glob.glob('merged_daily_data/**/*.csv',recursive=True)):
            df_out = pd.read_csv(file)
            df_total = pd.concat([df_total, df_out])

        df_total.to_csv(f'{self.current_directory}/yearly_summaries/ALL_DATES_DATA.csv',index=False)
        con = duckdb.connect("sql_tables/fantasy_database.duckdb")

    def sql_table_creator(self):
        print(f'[{time.ctime()}] Creating DuckDB table from all fuzzy-merged data for year {self.year}')
        # Create table from CSV and persist
        con = duckdb.connect("sql_tables/fantasy_database.duckdb")

        # Create the roster tables
        con.execute("""
            CREATE OR REPLACE TABLE all_roster_data AS
            SELECT *
            FROM read_csv_auto('merged_daily_data/**/*.csv')
        """)

        # create the transaction tables
        con.execute("""
            CREATE OR REPLACE TABLE all_transaction_data AS
            SELECT *
            FROM read_csv_auto('league_transactions/**/*.csv')
        """)

        # create the draft tables
        con.execute("""
            CREATE OR REPLACE TABLE all_draft_data AS
            SELECT *
            FROM read_csv_auto('league_drafts/**/*.csv')
        """)

        con.close()





    def loyalty_analytics(self):

        con = duckdb.connect("sql_tables/fantasy_database.duckdb")
        trans_df = con.execute(f"SELECT * FROM all_transaction_data where SEASON == {self.year}").df()
        draft_df = con.execute(f"SELECT * FROM all_draft_data where SEASON == {self.year}").df()

        for gm_key in draft_df['DESTINATION_KEY'].unique():
            # Let's look for keepers for this GM
            gm_draft = draft_df[(draft_df['DESTINATION_KEY']==gm_key)]
            gm_name = gm_draft['GM_NAME_DESTINATION'].values[0]
            count_keepers = len(gm_draft[gm_draft['KEEPER']=='KEEPER'])
            keeper_loyalty = count_keepers
            count_draftees = len(gm_draft[gm_draft['KEEPER']=='NO'])
            draft_loyalty = count_draftees

            loyalty_score = (draft_loyalty+keeper_loyalty)/(count_keepers+count_draftees)
            for keeper in gm_draft[(gm_draft['KEEPER']=='KEEPER')]['NAME']:
                # Look to see if this player was ever dropped in the transactions list
                # If so, subtract one from the keeper loyalty score
                if len(trans_df[(trans_df['NAME']==keeper)&(trans_df['SOURCE_KEY']==gm_key)&(trans_df['TRANSACTION_TYPE']=='drop')])>0:
                    keeper_loyalty = keeper_loyalty-1
                    loyalty_score = (draft_loyalty + keeper_loyalty) / (count_keepers + count_draftees)
                    print(f'{gm_name} dropped keeper {keeper}, thus lowering loyalty_score to {loyalty_score} ')


            for draftee in gm_draft[(gm_draft['KEEPER']=='NO')]['NAME']:
                # Look to see if this player was ever dropped in the transactions list
                if len(trans_df[(trans_df['NAME']==draftee)&(trans_df['SOURCE_KEY']==gm_key)&(trans_df['TRANSACTION_TYPE']=='drop')])>0:
                    draft_loyalty = draft_loyalty-1
                    loyalty_score = (draft_loyalty + keeper_loyalty) / (count_keepers + count_draftees)
                    print(f'{gm_name} dropped draftee {draftee}, thus lowering loyalty_score to {loyalty_score} ')

            # todo: Make an outputer for this
        con.close()

    def hospital_analytics(self):
        con = duckdb.connect("sql_tables/fantasy_database.duckdb")

        hurt_df = con.execute(f"SELECT * FROM all_roster_data where SEASON == {self.year} and INJURY_NOTE is not NULL").df()
        print(f'Number of hurt players: {len(hurt_df)}')
        for patient in hurt_df['NAME'].unique():
            player_df = hurt_df[hurt_df['NAME']==patient]
            # we want to derive the "trips" to the hospital per player (ie, consecutive days are one trip)
            # for each trip, what was the reason
            # how many trips per player
            # how many players per gm, and thus how many trips

            total_days = len(player_df)
            player_df['DATE'] = pd.to_datetime(player_df['DATE'])
            player_df.sort_values('DATE',inplace=True)
            player_df['DATE_GROUP'] = (player_df['DATE'].diff().dt.days.ne(1)).cumsum()
            for group_count in player_df['DATE_GROUP'].unique():
                player_group_df = player_df[player_df['DATE_GROUP']==group_count]
                group_start = player_group_df['DATE'].min()
                group_end = player_group_df['DATE'].max()
                injury = player_group_df['INJURY_NOTE'].iloc[0]
                time_in_group = len(player_group_df)
                print(f'{patient} was in the hospital from {group_start} to {group_end} ({time_in_group} days) with a(n) {injury} issue')
        # todo: this kind of works; there is a problem with the datagaps causing an issue with the grouping.. but at least everyting parses correctly


    def test_function(self):

        # Let's try some z-score derivations


        con = duckdb.connect("sql_tables/fantasy_database.duckdb")
        player_df = con.execute(f"SELECT * FROM all_roster_data where SEASON == {self.year}").df()
