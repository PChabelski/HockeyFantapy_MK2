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


pd.options.display.float_format = '{:,}'.format
pd.set_option('mode.chained_assignment', None)

class YEAR_INSTANCE:
    """
    Generic engine for running the code.
    """

    def __init__(self, control_file, current_directory, year, processing_type, dates_to_check):
        self.control_file       = control_file
        self.year               = year
        self.league_id          = str(self.control_file['Years'][str(year)]['league_id'])
        self.game_id            = int(self.control_file['Years'][str(year)]['game_id'])
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
        self.extract_league_stat_categories()
        self.extract_league_weeks_and_dates()
        self.extract_yahoo_league_standings()


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

        # Save the schedule to a CSV file
        output_dir = f'{self.current_directory}/season_schedules'
        os.makedirs(output_dir, exist_ok=True)
        self.df_sched.to_csv(f'{output_dir}/{self.year}_NHL_Schedule.csv', index=False)

    def extract_player_metadata(self):
        # Updating this to append to one master file
        # instead of creating a new file each time, since sometimes yahoo can't parse particular players per year
        # you still extract the players by year, but you can append to the master file instead of creating the new file
        print(f'[{time.ctime()}] updating player masterlist database with info from year {self.year}')

        try:
            self.df_players_metadata = pd.read_csv(f'{self.current_directory}/player_metadata/player_metadata_master_list.csv')
        except:
            self.df_players_metadata = pd.DataFrame(columns=['player_name', 'display_position', 'player_key', 'eligible_positions', 'headshot_url', 'stripped_name'])
        players = []
        self.league_players = self.query.get_league_players()
        for player in self.league_players:
            try:
                player_data = player.clean_data_dict()
            except:
                player_data = player
            player_key = player_data.get('player_key', '?').split('.')[-1] # grab only the ID part, not the game / year code
            if player_key !='?' and int(player_key) not in self.df_players_metadata['player_key'].unique():
                player_name = player_data.get('name', {}).get('full', 'Unknown Player')
                player_name = self.player_name_cleaner(player_name, player_key)
                # strip whitespace and convert to lowercase for easier fuzzy-matching
                # Ie: Phil Kessel -> philkessel
                stripped_name = re.sub(r'\W+', '', player_name.strip().replace(" ", "").lower())
                players.append({
                    'player_name': player_name,
                    'display_position': player_data.get('display_position', '?'),
                    'player_key':int(player_key),
                    'eligible_positions': player_data.get('eligible_positions', '?'),
                    'headshot_url': player_data.get('headshot', {}).get('url', '?'),
                    'stripped_name': stripped_name
                })
                print(f'Adding new player: {player_name} | {player_key} | {stripped_name} to the master list')

        new_players_df = pd.DataFrame(players)
        # Append new players to the existing DataFrame
        self.df_players_metadata = pd.concat([self.df_players_metadata, new_players_df], ignore_index=True)
        # Save the DataFrame to a CSV file
        output_dir = f'{self.current_directory}/player_metadata'
        os.makedirs(output_dir, exist_ok=True)
        output_file = f'{output_dir}/player_metadata_master_list.csv'
        self.df_players_metadata.sort_values('player_key', ascending=True, inplace=True)
        self.df_players_metadata.to_csv(output_file, index=False)
        time.sleep(60)  # Sleep for 60 seconds to avoid API rate limits

    def player_name_cleaner(self, name, player_key):
        # This function is used to clean player names such that they align with other databases we extract data from
        # For example, the name "Sebastian Aho" is stored as "Sebastian Antero Aho" in the NHL database
        cleaned_name = name
        if 'Mike' in name:
            cleaned_name  = name.replace('Mike', 'Michael')
        if 'John-Jason' in name:
            cleaned_name  = name.replace('John-Jason', 'JJ')
        if 'Tommy' in name:
            cleaned_name  = name.replace('Tommy', 'Thomas')
        if 'Nicholas' in name:
            cleaned_name  = name.replace('Nicholas', 'Nick')
        if 'Alexander' in name:
            cleaned_name  = name.replace('Alexander', 'Alex')

        # Now do actual name replacements
        if name == 'Bo Groulx':
            cleaned_name = 'Benoit-Olivier Groulx'
        if name == 'Sebastian Aho' and '6777' in player_key:
            cleaned_name = 'Sebastian Antero Aho' # This is the Carolina Hurricanes center
        if name == 'Sebastian Aho' and  '7654' in player_key:
            cleaned_name = 'Sebastian Johannes Aho' # This is the New York Islanders defenseman
        if name == 'Elias Pettersson' and  '32762' in player_key:
            cleaned_name = 'Elias Nils Pettersson' # This is the Canucks defenseman
        if name == 'Elias Pettersson' and '7520' in player_key:
            cleaned_name = 'Elias Fredrik Pettersson' # This is the Canucks forward

        # Diagnostic statement
        if name != cleaned_name:
            print(f'Player name cleaned from {name} to {cleaned_name}')

        return cleaned_name

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


    def extract_league_stat_categories(self):

        print(f'[{time.ctime()}] extracting league stat categories for year {self.year}')
        self.stat_categories = self.query.get_game_stat_categories_by_game_id(self.game_id).clean_data_dict()['stats']
        cf_yh = self.control_file['yahoo_columns']

        stat_arr = []
        for stat_data in self.stat_categories:
            stat = stat_data['stat'].clean_data_dict()
            stat_name = stat.get('name', '?')
            stat_active = True if stat_name in cf_yh.keys() and self.year in cf_yh[stat_name]['years_applicable'] else False
            stat_value = cf_yh[stat_name]['value'] if stat_active else np.nan
            stat_type = cf_yh[stat_name]['type'] if stat_active else np.nan

            stat_arr.append({
                'season': self.year,
                'display_name': stat.get('display_name', '?'),
                'name': stat.get('name', '?'),
                'stat_id': stat.get('stat_id', '?'),
                'stat_active': stat_active,
                'stat_value': stat_value,
                'stat_type': stat_type
            })
        # Create a DataFrame from the list of dictionaries
        self.df_stats = pd.DataFrame(stat_arr)
        output_dir = f'{self.current_directory}/league_stat_categories'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_stat_categories.csv'
        self.df_stats.to_csv(output_file, index=False)

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
            for matchup_count in week_matchup_data:
                matchup_data = matchup_count['matchup'].clean_data_dict()
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
                                               player_name, '', faab_bid, source, source_key,
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
                                               player_name, '', '', source, source_key,
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
                                               player_name, '', faab_bid, source, source_key,
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
                                               player_name, '', '', source, source_key,
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
                                                       player_name, draft_round, '', source, source_key,
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
                                                   player_name, '', '', source, source_key,
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
                                                       player_name, '', '', source, source_key,
                                                       destination, destination_key, waiver_check, '')
            else:  # this is commish -> i don't know what to do with these
                trans_status = trans.status
                trans_time = datetime.fromtimestamp(trans.timestamp)
                trans_id = str(self.year) + "_" + str(trans_week) + "_" + str(trans.transaction_id)

        df_trans.columns = df_trans.columns.str.upper()
        df_trans.to_csv(f'{self.current_directory}/league_transactions/{self.year}_transactions.csv', index=False)



    def extract_yahoo_draft_results(self):
        print(f'[{time.ctime()}] extracting draft information for year {self.year}')

        df_draft = pd.DataFrame(columns=['season',
                                         'week',
                                         'transaction_date',
                                         'transaction_type',
                                         'transaction_id',
                                         'status',
                                         'player_id',
                                         'name',
                                         'draft_round',
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

            draft_arr.append({
                'season': self.year,
                'week': 0,
                'transaction_date': draft_time,
                'transaction_type': draft_type,
                'transaction_id': draft_id,
                'status': 'successful',
                'player_id': player_id,
                'name': player_name,
                'draft_round': pick_round,
                'faab_bid': '',
                'source': source,
                'source_key': source_key,
                'destination': team_name,
                'destination_key': team_key,
                'waiver': '',
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

    # Lock this away for now, as it is not working properly
    # It might have to be a live-system only query... let's figure out what historic data we can actually pull properly
    #
    def get_team_roster_player_info_by_date(self):
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
                        'season': self.year,
                        'name': player_daily_metadata.get('name', {}).get('full', 'Unknown Player'),
                        'player_id': player_daily_metadata.get('player_id', 0),
                        'player_key': player_daily_metadata.get('player_key', '?'),
                        'display_position': player_daily_metadata.get('display_position', '?'),
                        'injury_note': player_daily_metadata.get('injury_note', ''),
                        'is_keeper': player_daily_metadata.get('is_keeper', {}).get('status', False),
                        'keeper_cost': player_daily_metadata.get('is_keeper', {}).get('cost', ''),
                        'kept': player_daily_metadata.get('is_keeper', {}).get('kept', ''),
                        'owner_team_key': team_id,
                        'owner_team_name': team_name,
                        'owner_team_gm': team_gm,
                        'selected_position': player_daily_metadata.get('selected_position', {}).get('position', '?'),
                        'eligible_positions': player_daily_metadata.get('eligible_positions', []),
                        'percent_owned': player_daily_metadata.get('percent_owned', {}).get('value', 0),
                        'percent_owned_delta': player_daily_metadata.get('percent_owned', {}).get('delta', 0.0),
                        # 'draft_average_pick': player_daily_metadata.get('draft_analysis', {}).get('average_pick', 0.0),
                        # 'draft_average_round': player_daily_metadata.get('draft_analysis', {}).get('average_round', 0.0),
                        # 'draft_percent_drafted': player_daily_metadata.get('draft_analysis', {}).get('percent_drafted', 0.0),
                        # 'preseason_average_pick': player_daily_metadata.get('draft_analysis', {}).get('preseason_average_pick', 0.0),
                        # 'preseason_average_round': player_daily_metadata.get('draft_analysis', {}).get('preseason_average_round', 0),
                        # 'preseason_percent_drafted': player_daily_metadata.get('draft_analysis', {}).get('preseason_percent_drafted', 0.0)
                    }
                    # No need for stats, I will grab them from the fine folks at natural stat trick
                    # # Loop through and grab the stats for this player
                    # for i in range(0, len(player_daily_metadata['player_stats']['stats'])):
                    #     player_day_stat = player_daily_metadata['player_stats']['stats'][i]['stat'].clean_data_dict()
                    #     # Check if the stat_id is in the df_stat_codes DataFrame
                    #     if player_day_stat.get('stat_id', '') in df_stat_codes['stat_id'].values:
                    #         player_metadata_dict[df_stat_codes[df_stat_codes['stat_id']==player_day_stat.get('stat_id', '')]['name'].values[0]] = player_day_stat.get('value', '')

                    # convert the player_metadata_and_stats_arr to a DataFrame
                    date_arr.append(player_metadata_dict)


                    # using the player key, let's see what the other player info can give us
                    #player_key = player_daily_metadata.get('player_key', '?')
                    #player_date_results = self.query.get_player_stats_by_date(player_key=player_key, chosen_date=iter_date)
                    #print(player_date_results)

            date_df = pd.DataFrame(date_arr)
            output_dir = f'{self.current_directory}/team_rosters_by_date'
            os.makedirs(output_dir, exist_ok=True)
            # Save the DataFrame to a CSV file
            output_file = f"{output_dir}/{self.year}_rosters_{iter_date}.csv"
            date_df['date'] = iter_date
            date_df.to_csv(output_file, index=False)
            time.sleep(10)  # Sleep for 10 seconds to avoid API rate limits



    def fuzzy_outer_merge(self):
        """
        Outer merge Yahoo and NST on fuzzy name matching.
        Keeps all rows from both dataframes, with match metadata.
        """

        def normalize_name(s: str) -> str:
            """Lowercase, strip accents, remove punctuation/extra spaces."""
            if s is None:
                return ""
            s = str(s)
            s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
            return " ".join(re.sub(r"[^a-z ]", " ", s.lower()).split())

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
            df_yahoo = pd.read_csv(f'{self.current_directory}/team_rosters_by_date/{self.year}_rosters_{date}.csv')
            df_nst = pd.read_csv(f'{self.current_directory}/nst_data/{self.year}/NST_Stats_{date}.csv')
            if len(df_nst) == 0:
                print(f'No data to fuzzy merge for date {date} in year {self.year} -> likely a day off')
                continue
            threshold = 85
            nst_col = 'Player'
            yahoo_col = 'name'
            #########################
            # Normalize names
            nst_names = df_nst[nst_col].dropna().astype(str).tolist()
            yahoo_names = df_yahoo[yahoo_col].dropna().astype(str).tolist()
            nst_norm = {name: normalize_name(name) for name in nst_names}
            yahoo_norm = {name: normalize_name(name) for name in yahoo_names}

            # Track matched Yahoo players
            matched_yahoo = set()
            merged_rows = []

            # Pass 1: go through NST and try to find Yahoo matches
            for _, n_row in df_nst.iterrows():
                n_name = str(n_row[nst_col])
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
                        nst_col: None,
                        "Yahoo_BestGuess": y_name,
                        "Score": 0,
                        "MATCH": False
                    })
                    merged_rows.append(row)

            df_out =  pd.DataFrame(merged_rows)

            df_out = df_out[[
                'season',
                'Date',
                'Code',
                'Player' ,
                'name'    ,
                'MATCH',
                'Score',
                'player_id'    ,
                'player_key'   ,
                'display_position' ,
                'injury_note',
                'is_keeper'   ,
                'keeper_cost'  ,
                'kept'    ,
                'owner_team_key' ,
                'owner_team_name' ,
                'owner_team_gm',
                'selected_position',
                'eligible_positions',
                'percent_owned'   ,
                'percent_owned_delta' ,
                'date',
                'Score'  ,
                'MATCH' ,
                'Team'   ,
                'Position'  ,
                'GP'    ,
                'TOI'    ,
                'Goals' ,
                'Total Assists'  ,
                'First Assists'  ,
                'Second Assists',
                'Total Points',
                'PPP',
                'SHP',
                'IPP'   ,
                'Shots'  ,
                'SH%' ,
                'ixG'  ,
                'iCF'  ,
                'iFF'  ,
                'iSCF' ,
                'iHDCF'  ,
                'Rush Attempts' ,
                'Rebounds Created' ,
                'PIM'   ,
                'Total Penalties'   ,
                'Minor'  ,
                'Major'   ,
                'Misconduct'   ,
                'Penalties Drawn'  ,
                'Giveaways',
                'Takeaways' ,
                'Hits'    ,
                'Hits Taken' ,
                'Shots Blocked',
                'Faceoffs Won' ,
                'Faceoffs Lost' ,
                'Faceoffs %' ,
                'Shots Against' ,
                'Saves'    ,
                'Goals Against',
                'SV%' ,
                'GAA'  ,
                'GSAA'    ,
                'xG Against'  ,
                'HD Shots Against',
                'HD Saves' ,
                'HD Goals Against'  ,
                'HDSV%' ,
                'HDGAA'  ,
                'HDGSAA'  ,
                'MD Shots Against',
                'MD Saves'    ,
                'MD Goals Against' ,
                'MDSV%' ,
                'MDGAA'   ,
                'MDGSAA'  ,
                'LD Shots Against'  ,
                'LD Saves'  ,
                'LD Goals Against' ,
                'LDSV%' ,
                'LDGAA',
                'LDGSAA'    ,
                'Rush Attempts Against' ,
                'Rebound Attempts Against' ,
                'Avg. Shot Distance'  ,
                'Avg. Goal Distance'
            ]]
            df_out['Date'] = date
            df_out['season'] = self.year
            df_out.columns = df_out.columns.str.upper()
            df_out.to_csv(f'{self.current_directory}/fuzzy_merged_yahoo_nst/{self.year}_fuzzy_merged_yahoo_nst_{date}.csv',
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
            df_week_matchups = pd.DataFrame

            for date in df_weeks[df_weeks['week'] == week]['date'].unique():
                # grab the associated fuzzy-matched data for this date, stitch them together for the entire week
                try:
                    df_fuzzy = pd.read_csv(f'{self.current_directory}/fuzzy_merged_yahoo_nst/{self.year}_fuzzy_merged_yahoo_nst_{date}.csv')
                except FileNotFoundError:
                    print(f'No fuzzy-merged data found for date {date}. Skipping.')
                    continue
                df_week_matchups = pd.concat([df_week_matchups,df_fuzzy])
            # Now we have the full week's fuzzy-merged data in df_week_matchups
            df_week_matchups['PLAY_OR_BENCH'] = df_week_matchups.apply(lambda row: 'PLAY' if row['SELECTED_POSITION'] in ['C','LW','RW','D','G','UTIL'] else 'BENCH', axis=1)
            # Now we can cycle through each GM and PLAY_OR_BENCH to sum up the appropriate stats
            df_week_summary = df_week_matchups.groupby(['OWNER_TEAM_KEY','OWNER_TEAM_NAME','OWNER_TEAM_GM','PLAY_OR_BENCH']).agg({
                "GOALS":"sum",
                "TOTAL ASSISTS":"sum",
                "PPP":"sum",
                "SHP":"sum",
                "SHOTS":"sum",
                "SH%":"mean",
                "PIM":"sum",
                "HITS":"sum",
                "SHOTS BLOCKED":"sum",
                "SAVES":"sum",
                "SV%":"mean"}).reset_index()

            df_week_summary.to_csv(f'{self.current_directory}/matchup_summaries_by_week/{self.year}_matchup_summary_week_{week}.csv', index=False)

