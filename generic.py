from yfpy.query import YahooFantasySportsQuery
from bs4 import BeautifulSoup, Comment
import requests
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import os
import time


class YEAR_INSTANCE:
    """
    Generic engine for running the code.
    """

    def __init__(self, control_file, current_directory, year):
        self.control_file       = control_file
        self.year               = year
        self.league_id          = str(self.control_file['Years'][str(year)]['league_id'])
        self.game_id            = int(self.control_file['Years'][str(year)]['game_id'])
        self.current_directory  = current_directory
        print(f'Initializing instance for Year {self.year} | League ID {self.league_id} | Game ID {self.game_id}')


        self.query = YahooFantasySportsQuery(league_id=self.league_id,
                                        game_id=self.game_id,
                                        game_code='nhl',
                                        yahoo_consumer_key="dj0yJmk9elVQcHZ2RTBMWGlBJmQ9WVdrOVUwRkpkazFGU1ZjbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PWY4",
                                        yahoo_consumer_secret="8cb89479045212bcf8892d6e0fbbb96cf2acfe51",
                                        save_token_data_to_env_file=True,
                                        env_file_location=Path(f"{current_directory}/private"),
                                        all_output_as_json_str = False
                                             )

        self.query.save_access_token_data_to_env_file(
            env_file_location=Path(f"{current_directory}/private"),
            save_json_to_var_only=True
        )

    def extract_yahoo_league_metadata(self):
        """
        Extracts and saves Yahoo Fantasy Sports league metadata as a CSV file.
        Optimized for performance by directly appending data to the DataFrame.
        """
        # Retrieve and clean league metadata
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

        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # 2012 the API CANNOT PARSE THE TEAMS; MANUALLY GENERATE INSTEAD!
        if self.year == 2012:
            print('[extract_yahoo_league_standings] - Not processing standings for 2012 as Yahoo API bugs out')
            return
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        league_standings = self.query.get_league_standings().clean_data_dict()['teams']
        # League standings are presented as team objects, with additional standings metadata
        team_standings = []
        for team_results_obj in league_standings:
            team_results = team_results_obj['team'].clean_data_dict()
            team_standings.append({
                'name': team_results.get('name', ''),
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

        # Fetch and parse the webpage
        page = requests.get(f"https://www.hockey-reference.com/leagues/NHL_{self.year + 1}_games.html")
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
                stripped_name = player_name.strip().replace(" ", "").lower()
                players.append({
                    'player_name': player_name,
                    'display_position': player_data.get('display_position', '?'),
                    'player_key':player_key,
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
        self.stat_categories = self.query.get_game_stat_categories_by_game_id(self.game_id).clean_data_dict()['stats']
        stat_arr = []
        for stat_data in self.stat_categories:
            stat = stat_data['stat'].clean_data_dict()
            print(stat)
            stat_arr.append({
                'season': self.year,
                'display_name': stat.get('display_name', '?'),
                'name': stat.get('name', '?'),
                'stat_id': stat.get('stat_id', '?'),
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

    def extract_yahoo_draft_results(self):
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
                                             'keeper'])

        draft_arr = []
        df_players = pd.read_csv(f'{self.current_directory}/player_metadata/player_metadata_master_list.csv')
        df_teams = pd.read_csv(f'{self.current_directory}/league_teams/{str(self.year)}_league_teams.csv')
        draft = self.query.get_league_draft_results()
        print(len(draft))
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
                'keeper': keeper_check
            })
            #print(draft_pick, draft_time, draft_type, pick_number, pick_round, player_id, team_key, player_name, gm_name, team_name, source_key, source, source, keeper_check)

        # Create a DataFrame from the list of dictionaries
        self.df_draft = pd.DataFrame(draft_arr)
        output_dir = f'{self.current_directory}/league_drafts'
        os.makedirs(output_dir, exist_ok=True)

        # Save the DataFrame to a CSV file
        output_file = f'{output_dir}/{self.year}_league_draft.csv'
        self.df_draft.to_csv(output_file, index=False)