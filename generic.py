"""
generic.py

Core data extraction engine for the fantasy hockey pipeline.

Defines YEAR_INSTANCE — the primary class responsible for all online data
pulls. Every public method either:
    - Queries the Yahoo Fantasy Sports API via yfpy, or
    - Scrapes Hockey Reference (HR) box scores / schedules via requests + BeautifulSoup,
    - or performs fuzzy name-matching between the two data sources.

Outputs are written as year-partitioned CSVs consumed downstream by
analytics_engine.py.

External dependencies:
    yfpy        — Yahoo Fantasy Sports API wrapper
    rapidfuzz   — fast fuzzy string matching
    BeautifulSoup — HTML parsing for Hockey Reference scraping
    duckdb      — SQL layer (imported but reserved for future use)
    openai      — imported but reserved for future NLP/RAG use
"""

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
    Extraction engine for a single fantasy hockey season.

    Wraps the Yahoo Fantasy Sports API and Hockey Reference scraper for one
    configured year. On instantiation, several metadata extractions run
    automatically so that downstream methods have roster, schedule, and
    player-mapping data available immediately.

    Attributes:
        control_file (dict): Full runtime config loaded from control_file.json.
        year (str|int): The season year being processed (e.g. 2025).
        stats_for_year (list): Scoring category names for this season.
        league_id (str): Yahoo league ID for this season.
        game_id (int): Yahoo game ID for this season.
        current_directory (str): Absolute path to the project root.
        dates_to_check (list[str]): ISO date strings to process (YYYY-MM-DD).
        query (YahooFantasySportsQuery): Authenticated Yahoo API client.
        schedule_df (pd.DataFrame): NHL game schedule scraped from Hockey Reference.
        df_league_metadata (pd.DataFrame): League-level metadata from Yahoo.
        df_league_teams (pd.DataFrame): Team and GM info for this season.
        df_league_standings (pd.DataFrame): Final standings from Yahoo.
        df_weeks (pd.DataFrame): Mapping of dates to fantasy weeks.
        df_matchup_metadata (pd.DataFrame): Head-to-head scoreboard by week.
        df_player_metadata (pd.DataFrame): Year-filtered player master list.
        df_otheryear_player_metadata (pd.DataFrame): Player rows for other seasons.
        master_metadata_file (pd.DataFrame): Combined all-years player master.
        ref_list (pd.DataFrame): NHL team code reference table.
        first_name_dict (dict): Common long-form → short-form first name map.
    """

    def __init__(self, control_file, current_directory, year, dates_to_check):
        """
        Initialize the extraction engine for a given season.

        Authenticates with the Yahoo API, then automatically runs a fixed set
        of metadata extractions (league info, teams, scoreboard, weeks, standings,
        NHL schedule, player metadata, and the Yahoo<->HR name mapper).

        Args:
            control_file (dict): Parsed content of control_file.json.
            current_directory (str): Absolute path to project root.
            year (str|int): Season year to process (e.g. 2025).
            dates_to_check (list[str]): Dates to extract data for (YYYY-MM-DD).
        """
        self.control_file       = control_file
        self.year               = year
        self.stats_for_year     = self.control_file["Years"][str(self.year)]['scoring_categories']
        self.league_id          = str(self.control_file['Years'][str(self.year)]['league_id'])
        self.game_id            = int(self.control_file['Years'][str(self.year)]['game_id'])
        self.current_directory  = current_directory
        self.dates_to_check     = dates_to_check
        print(f'[{time.ctime()}] Initializing instance for Year {self.year} | League ID {self.league_id} | Game ID {self.game_id}')

        # Authenticate with Yahoo Fantasy Sports API.
        # OAuth tokens are stored in the private/ folder and refreshed automatically.
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

        # Persist the refreshed access token back to the .env file in private/
        self.query.save_access_token_data_to_env_file(
            env_file_location=Path(f"{current_directory}/private"),
            save_json_to_var_only=True
        )

        # Auto-run fixed metadata extractions on every instantiation.
        # These populate instance attributes used by all downstream methods.
        self.extract_yahoo_league_metadata()
        self.extract_yahoo_league_teams()
        self.extract_league_scoreboard_by_week()
        self.extract_league_weeks_and_dates()
        self.extract_yahoo_league_standings()

        # NHL team code reference — used for schedule and HR data linking
        self.ref_list = pd.read_csv(f'{self.current_directory}/manual_data/Hockey_Team_Codes.csv')
        self.NHL_schedule_parser()

        # Canonical first-name abbreviation map.
        # Used by fuzzy_match_players_to_hr to normalize names before matching
        # (e.g. "Alexander Ovechkin" → "Alex Ovechkin") so that HR and Yahoo
        # name variants resolve to the same normalized form.
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
        """
        Build and maintain the canonical player master list for this season.

        Pulls every player in the Yahoo league for self.year. For any player
        not already in the master CSV (manual_data/PLAYER_MASTER_DATA.csv),
        adds a new row with HR_REVIEW_FLAG=True so the name mapper will
        attempt to link them to a Hockey Reference record.

        After updating the year-slice, calls mapping_hr_to_yh_names() to
        run fuzzy matching on all unmatched rows, then writes the merged
        all-years master back to disk.

        Side effects:
            - Sets self.df_player_metadata (year-filtered player master).
            - Sets self.df_otheryear_player_metadata (other seasons' rows).
            - Sets self.master_metadata_file (all-years combined).
            - Writes manual_data/PLAYER_MASTER_DATA.csv.
        """
        print(f'[{time.ctime()}] Extracting yahoo player metadata for year {self.year}')
        player_metadata = self.query.get_league_players()
        output_dir = f'{self.current_directory}/player_metadata'
        os.makedirs(output_dir, exist_ok=True)

        try:
            # Attempt to load the existing master file; fall back to empty if missing
            self.df_all_player_metadata = pd.read_csv(f'{self.current_directory}/manual_data/PLAYER_MASTER_DATA.csv')
            print(f'[{time.ctime()}] Successfully loaded PLAYER_MASTER_DATA file ({len(self.df_all_player_metadata)} entries)')
        except:
            self.df_all_player_metadata = pd.DataFrame(columns = ['season','yahoo_name','player_id','HR_MATCH_NAME','HR_LINK_NAME','HR_MATCH_SCORE','HR_REVIEW_FLAG','HR_COLLISION_FLAG'])
            print(f'[{time.ctime()}] Could not find PLAYER_MASTER_DATA file! Creating one now...')

        # Split master into current-year rows (to update) and all other years (to preserve)
        self.df_player_metadata = self.df_all_player_metadata[self.df_all_player_metadata['season']==int(self.year)]
        self.df_otheryear_player_metadata = self.df_all_player_metadata[self.df_all_player_metadata['season']!=int(self.year)]
        print(f'[{time.ctime()}] Filtered master list down to all {self.year} entries: ({len(self.df_player_metadata)} entries)')


        for i  in range(0,len(player_metadata)):

            try:
                player_data = player_metadata[i].clean_data_dict()
            except:
                # Some years wrap each player in an extra {'player': ...} envelope
                player_data = player_metadata[i]['player'].clean_data_dict()
                #print(f'Year {self.year} bugged out trying to extract: {player_data}')

            player_id = player_data['player_id']
            yahoo_name = player_data.get('name', {}).get('full', 'UNKNOWN NAME')

            # Skip players already in the master list for this year
            if player_id in self.df_player_metadata['player_id'].values:
                continue
            else:
                # New player: add with HR_REVIEW_FLAG=True so the mapper picks them up
                print(f'Adding {player_id} {yahoo_name} to MASTER DATA LIST')
                self.df_player_metadata.loc[len(self.df_player_metadata)] = (self.year, yahoo_name,player_id, '', '',np.nan,True,True)

        # Run fuzzy matching against HR data for all unmatched rows in this year
        self.mapping_hr_to_yh_names()

        # Reconstruct the all-years master and write back to disk
        self.master_metadata_file = pd.concat([self.df_player_metadata, self.df_otheryear_player_metadata])


        self.master_metadata_file.to_csv(f'manual_data/PLAYER_MASTER_DATA.csv', index=False)

    def mapping_hr_to_yh_names(self):
        """
        Fuzzy-match Yahoo player names to Hockey Reference player names.

        Loads all HR CSVs for self.year, extracts unique PLAYER/HR_LINK_NAME
        pairs, then runs fuzzy_match_players_to_hr() against any player in
        self.df_player_metadata where HR_REVIEW_FLAG is True (i.e. not yet
        confirmed as matched). Logs a warning if any HR name maps to more than
        one Yahoo player_id (collision).

        Side effects:
            - Updates self.df_player_metadata with match results.
            - Sleeps 10 seconds after completion (rate-limit courtesy buffer).
        """
        print(f'[{time.ctime()}] Running the HR<->yahoo player metadata mapper for year {self.year}')

        # Concatenate all per-date HR CSVs for this year into one frame
        df_hr_year = pd.DataFrame()
        for filename in glob.glob(f'{self.current_directory}/hr_data_extract/{self.year}/*.csv', recursive=True):
            hr_df = pd.read_csv(filename)
            df_hr_year = pd.concat([df_hr_year, hr_df], ignore_index=True)

        # Only need the name columns for matching — deduplicate to one row per player
        df_hr_year_just_names = df_hr_year[['PLAYER', 'HR_LINK_NAME']].drop_duplicates().reset_index(drop=True)
        print(f'[{time.ctime()}] Fuzzy-matching yahoo player metadata to HR names for year {self.year}')

        # Split into already-matched (skip) and unmatched (run fuzzy match on)
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
        # Recombine matched and freshly-matched rows
        self.df_player_metadata = pd.concat([df_player_metadata_unmatched, df_player_metadata_matched])

        # Warn if the same HR name links to multiple Yahoo player_ids —
        # this indicates a name collision that needs manual resolution.
        duplicate_hr_matches = self.df_player_metadata.groupby(['HR_LINK_NAME'])['player_id'].nunique()
        duplicate_hr_matches = duplicate_hr_matches[duplicate_hr_matches > 1]
        if not duplicate_hr_matches.empty:
            print(f'[{time.ctime()}] WARNING: Duplicate HR matches found for year {self.year}:')
            print(duplicate_hr_matches)

        time.sleep(10)


    def extract_yahoo_league_metadata(self):
        """
        Extract and save Yahoo Fantasy league metadata for self.year.

        Fetches top-level league info (name, start/end dates, num teams, etc.)
        via the Yahoo API and writes it to league_metadata/<year>_league_metadata.csv.

        Side effects:
            - Sets self.df_league_metadata.
            - Creates league_metadata/ directory if absent.
            - Writes league_metadata/<year>_league_metadata.csv.
        """
        print(f'[{time.ctime()}] Extracting yahoo league metadata for year {self.year}')
        league_metadata = self.query.get_league_metadata().clean_data_dict()
        # Wrap the single metadata dict in a list so pd.DataFrame produces one row
        self.df_league_metadata = pd.DataFrame([league_metadata])
        output_dir = f'{self.current_directory}/league_metadata'
        os.makedirs(output_dir, exist_ok=True)
        output_file = f'{output_dir}/{self.year}_league_metadata.csv'
        self.df_league_metadata.to_csv(output_file, index=False)

    def extract_yahoo_league_teams(self):
        """
        Extract and save Yahoo Fantasy league team and GM info for self.year.

        Fetches the full team list from Yahoo, normalizes GM display names
        (some GMs have used different Yahoo nicknames across seasons or have
        deleted accounts), and writes the result to league_teams/<year>_league_teams.csv.

        Known API limitation: 2012 data cannot be parsed by yfpy — the pre-built
        CSV is loaded directly instead.

        Side effects:
            - Sets self.df_league_teams.
            - Creates league_teams/ directory if absent.
            - Writes league_teams/<year>_league_teams.csv.

        CSV columns:
            season, name, team_id, team_key, number_of_moves, number_of_trades,
            waiver_priority, faab_balance, clinched_playoffs, team_logo_url,
            email, felo_score, felo_tier, gm_image_url, gm_name.
        """
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # 2012 the API CANNOT PARSE THE TEAMS; MANUALLY GENERATE INSTEAD!
        print(f'[{time.ctime()}] Extracting yahoo league team info for year {self.year}')
        if int(self.year) == 2012:
            print('[extract_yahoo_league_teams] - Not processing teams for 2012 as Yahoo API bugs out. Load the premade one.')
            self.df_league_teams = pd.read_csv(f'{self.current_directory}/league_teams/{self.year}_league_teams.csv')
            return
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        league_teams = self.query.get_league_teams()
        team_data = []
        for team_obj in league_teams:
            team = team_obj.clean_data_dict()
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            # YAHOO API ERROR-HANDLING - CONSIDER MOVING THESE INTO YEAR-METHODS
            # Safely handle the 'managers' field if it's a list
            # This occurs in 2014 when Yusko co-managed with someone else.
            managers = team.get('managers', {})
            if isinstance(managers, list) and managers:
                # Co-manager present — use the first manager in the list
                manager = managers[0]['manager'].clean_data_dict()
                print('[extract_yahoo_league_teams] - Co-manager found, using first manager')
            else:
                manager = managers['manager'].clean_data_dict()
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            # Data quality update - force the names of each GM to be the same across seasons
            # Some GMs have different names in different seasons, etc. Some old accounts have been removed and replaced with --hidden--
            gm_name =  manager.get('nickname', '')
            team_name = team.get('name', '').decode('utf-8')

            # Normalize team names / GM nicknames that changed across seasons
            # or were entered inconsistently. Each block handles one known case.
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
        self.df_league_teams = pd.DataFrame(team_data)
        output_dir = f'{self.current_directory}/league_teams'
        os.makedirs(output_dir, exist_ok=True)

        output_file = f'{output_dir}/{self.year}_league_teams.csv'
        self.df_league_teams.to_csv(output_file, index=False)

    def extract_yahoo_league_standings(self):
        """
        Extract and save final league standings for self.year.

        Fetches the Yahoo standings endpoint (which returns teams with win/loss/tie
        totals and playoff seed), then writes the result sorted by total_points
        descending to league_standings/<year>_league_standings.csv.

        Known API limitation: 2012 standings cannot be parsed — method returns
        early without writing anything.

        Side effects:
            - Sets self.df_league_standings.
            - Creates league_standings/ directory if absent.
            - Writes league_standings/<year>_league_standings.csv.
        """
        print(f'[{time.ctime()}] extracting yahoo league standings for year {self.year}')

        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        # 2012 the API CANNOT PARSE THE TEAMS; MANUALLY GENERATE INSTEAD!
        if int(self.year) == 2012:
            print(f'[{time.ctime()}] [extract_yahoo_league_standings] - Not processing standings for 2012 as Yahoo API bugs out')
            return
        # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

        league_standings = self.query.get_league_standings().clean_data_dict()['teams']
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

        self.df_league_standings = pd.DataFrame(team_standings)
        output_dir = f'{self.current_directory}/league_standings'
        os.makedirs(output_dir, exist_ok=True)

        output_file = f'{output_dir}/{self.year}_league_standings.csv'
        # Sort best-to-worst by accumulated fantasy points before saving
        self.df_league_standings.sort_values('total_points', ascending=False, inplace=True)
        self.df_league_standings.to_csv(output_file, index=False)

    def NHL_schedule_parser(self):
        """
        Scrape the full NHL regular-season schedule for self.year from Hockey Reference.

        Fetches the season games table and extracts a URL_KEY per game that is
        used by parse_HR_data() to build per-game box score URLs.

        The URL_KEY is embedded in hidden HTML cell attributes (csk) and is not
        present in the standard pandas read_html() output, so a secondary BeautifulSoup
        pass is required to extract it.

        Side effects:
            - Sets self.schedule_df (schedule with URL_KEY column).
            - Creates season_schedules/ directory if absent.
            - Writes season_schedules/<year>_NHL_Schedule.csv.
        """
        print(f'[{time.ctime()}] grabbing nhl schedule data from year {self.year}')
        # HR uses season-end year in the URL (e.g. 2025-26 season → NHL_2026)
        url = f"https://www.hockey-reference.com/leagues/NHL_{int(self.year)+1}_games.html"
        print(url)
        page = requests.get(url)
        soup = BeautifulSoup(page.content, "html.parser")
        tables = soup.find_all('table')
        self.schedule_df = pd.read_html(str(tables[0]))[0]

        # Second pass: extract URL_KEY from hidden 'csk' cell attributes.
        # The csk attribute contains "HOME.AWAY" codes; when home and away share
        # a substring, the cell holds the box score key for that game.
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
        output_dir = f'{self.current_directory}/season_schedules'
        os.makedirs(output_dir, exist_ok=True)
        self.schedule_df.to_csv(f'{output_dir}/{self.year}_NHL_Schedule.csv', index=False)

    def identical_names_handler(self, df, dbtype):
        """
        Disambiguate players who share the exact same name by appending middle names.

        Two known collisions exist in the dataset:
            - Sebastian Aho (Carolina Hurricanes vs Ottawa Senators)
            - Elias Pettersson (Vancouver Canucks vs another Pettersson)

        For Yahoo data, disambiguation is by player_id (Yahoo's numeric ID).
        For Hockey Reference data, disambiguation is by PLAYER_CODE (HR's slug).

        Args:
            df (pd.DataFrame): Roster or stat DataFrame containing a name column.
            dbtype (str): Either 'yahoo' (uses PLAYER_ID) or 'hr' (uses PLAYER_CODE)
                          to select the correct disambiguation key.

        Returns:
            pd.DataFrame: Input DataFrame with colliding name rows updated in-place
                          to use the disambiguated middle-name form.
        """
        # Maps each known duplicate name to the disambiguating key→new-name mapping
        # for both Yahoo (player_id) and HR (player_code) data sources.
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
        # HR data uses 'PLAYER', Yahoo data uses 'NAME'
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
        """
        Build and save the mapping of calendar dates to fantasy weeks for self.year.

        Expands each game week from the Yahoo API into individual date rows,
        tagging each date as SEASON, PLAYOFFS, or POST-SEASON based on the
        scoreboard metadata already extracted.

        Depends on self.df_matchup_metadata being set (via extract_league_scoreboard_by_week)
        and self.df_league_metadata being set (via extract_yahoo_league_metadata).

        Side effects:
            - Sets self.df_weeks.
            - Creates league_weeks_and_dates/ directory if absent.
            - Writes league_weeks_and_dates/<year>_league_weeks_and_dates.csv.
        """
        print(f'[{time.ctime()}] extracting league weeks and dates for year {self.year}')

        self.game_weeks = self.query.get_game_weeks_by_game_id(self.game_id)

        # end_week from league metadata marks the last regular-season week
        week_max = self.df_league_metadata['end_week'].iloc[0]
        week_arr = []
        for week_data in self.game_weeks:
            week_data = week_data.clean_data_dict()
            week = week_data.get('week', '?')
            week_start = week_data.get('start', '?')
            week_end = week_data.get('end', '?')

            # Tag the week type: playoff weeks appear in the scoreboard;
            # weeks beyond end_week with no scoreboard entry are post-season filler.
            is_playoff_week = 'PLAYOFFS' if 1 in self.df_matchup_metadata[self.df_matchup_metadata['week'].astype(int)==week]['is_playoff'].unique() else 'POST-SEASON' if week>week_max else 'SEASON'

            # Expand each week into one row per calendar date
            date_range = pd.date_range(start=week_start, end=week_end)
            for date in date_range:
                week_arr.append({
                    'season': self.year,
                    'week': week,
                    'date': date,
                    'playoff_week':is_playoff_week
                })
        self.df_weeks = pd.DataFrame(week_arr)
        output_dir = f'{self.current_directory}/league_weeks_and_dates'
        os.makedirs(output_dir, exist_ok=True)

        output_file = f'{output_dir}/{self.year}_league_weeks_and_dates.csv'
        self.df_weeks.to_csv(output_file, index=False)

    def extract_league_scoreboard_by_week(self):
        """
        Extract and save head-to-head matchup results for every week of self.year.

        Iterates over all weeks found in the existing league_weeks_and_dates CSV
        (which must already exist on disk). For each week, fetches the Yahoo
        scoreboard to capture each matchup's two teams, their scores, and the result.

        Side effects:
            - Sets self.df_matchup_metadata (used by extract_league_weeks_and_dates).
            - Creates league_scoreboards_by_week/ directory if absent.
            - Writes league_scoreboards_by_week/<year>_league_scoreboards.csv.
        """
        print(f'[{time.ctime()}] extracting league scoreboard information for year {self.year}')

        # Requires the weeks CSV to already exist — generated in a prior season setup step
        self.df_weeks = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')
        matchup_arr = []
        for week in self.df_weeks['week'].unique():
            try:
                week_matchup_data = self.query.get_league_scoreboard_by_week(chosen_week=int(week)).clean_data_dict()['matchups']
            except:
                # Some seasons have extra "bye" weeks at the end with no matchups
                print(f'No matchups in week {week} for year {self.year} (likely extra playoff week)')
                continue
            matchup_count = 0
            for matchup in week_matchup_data:
                matchup_count +=1
                matchup_data = matchup['matchup'].clean_data_dict()
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
                # Derive W/L/T from raw point totals since Yahoo API sometimes omits result
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

        self.df_matchup_metadata = pd.DataFrame(matchup_arr)
        output_dir = f'{self.current_directory}/league_scoreboards_by_week'
        os.makedirs(output_dir, exist_ok=True)

        output_file = f'{output_dir}/{self.year}_league_scoreboards.csv'
        self.df_matchup_metadata.to_csv(output_file, index=False)

    def extract_yahoo_transactions(self):
        """
        Extract and save all transactions (adds, drops, trades) for self.year.

        Fetches the full transaction log from Yahoo and normalizes each transaction
        type into a flat row format. add/drop transactions produce one row each;
        add/drop combos produce two rows; trades produce one row per player/pick moved.

        Outputs to league_transactions/<year>_transactions.csv.

        Side effects:
            - Writes league_transactions/<year>_transactions.csv.

        Note:
            Returns early (with a printed warning) if the weeks or teams CSVs
            are not yet available on disk.
        """
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
        for i in range(0, len(transactions)):

            trans = transactions[i]
            trans_type = trans.type
            trans_time = datetime.fromtimestamp(trans.timestamp)
            trans_datetime = trans_time.strftime('%Y-%m-%d')
            try:
                # Map the transaction date back to the fantasy week it occurred in
                trans_week = df_weeks[df_weeks['date'] == trans_datetime]['week'].values[0]
            except:
                # Transactions outside the league calendar (e.g. pre-season) get week 0
                trans_week = 0

            trans_id = str(self.year) + "_" + str(trans_week) + "_Trans_" + str(trans.transaction_id)
            trans_status = trans.status

            try:  # some years we have no faab_bid
                faab_bid = trans.faab_bid
            except:
                faab_bid = np.nan

            # --- ADD: player picked up from free agency / waivers ---
            if trans_type == 'add':
                player_id = trans.players[0].player_key
                player_name = trans.players[0].name.full
                destination = trans.players[0].transaction_data.destination_team_name
                destination_key = trans.players[0].transaction_data.destination_team_key
                destination_gm = self.df_league_teams[self.df_league_teams['team_key']==destination_key]['gm_name'].values[0]
                source = 'Free Agency'
                source_key = '99.l.99.t.99'
                source_type = trans.players[0].transaction_data.source_type
                # Distinguish waiver claims from direct free-agent adds
                waiver_check = 'YES' if source_type == 'waivers' else 'NO'
                df_trans.loc[len(df_trans)] = (self.year, trans_week, trans_time, trans_type,
                                               trans_id, trans_status, player_id,
                                               player_name, '','', faab_bid, source, source_key,
                                               destination, destination_key, waiver_check, '', destination_gm, source)

            # --- DROP: player released to free agency ---
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

            # --- ADD/DROP: simultaneous add and drop, stored as two separate rows ---
            elif trans_type == 'add/drop':
                try:
                    faab_bid = trans.faab_bid
                except:
                    faab_bid = np.nan

                # First player in the list is always the add
                add = trans.players[0]
                player_id = add.player_key
                player_name = add.name.full
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

                # Second player in the list is always the drop
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

            # --- TRADE: one row per player or draft pick involved ---
            elif trans_type == 'trade':
                if len(trans.picks) > 0:
                    # Process draft picks traded — always an even number
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

                        # Construct a human-readable pick name: "<next_year> <GM> Round <N> Draft Pick"
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
                    # Single-player trade (typically accompanied by picks)
                    player = trans.players[0]
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
                    # Multi-player trade: one row per player
                    for plr in range(0, len(trans.players)):
                        player = trans.players[plr]
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
                # Commissioner adjustments — not yet modelled, silently skip
                trans_status = trans.status
                trans_time = datetime.fromtimestamp(trans.timestamp)
                trans_id = str(self.year) + "_" + str(trans_week) + "_" + str(trans.transaction_id)

        df_trans.columns = df_trans.columns.str.upper()
        df_trans.to_csv(f'{self.current_directory}/league_transactions/{self.year}_transactions.csv', index=False)

    def extract_yahoo_draft_results(self):
        """
        Extract and save the draft results for self.year.

        Fetches each pick from the Yahoo draft API, resolves player names via
        the player metadata master, and tags each pick as KEEPER or NO.
        Draft picks and keeper designations are stored in a single flat CSV
        formatted identically to the transactions CSV (enabling the two files
        to be concatenated for unified analysis).

        Side effects:
            - Sets self.df_draft.
            - Creates league_drafts/ directory if absent.
            - Writes league_drafts/<year>_league_draft.csv.
        """
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
            # The player_key includes a game/year prefix — strip to get just the numeric ID
            player_id = draft_pick.get('player_key', '').split('.')[-1]
            team_key = draft_pick.get('team_key', '')
            draft_id = str(self.year) + "_0_Draft_" + str(pick_number)

            player_name = df_players[df_players['player_id'] == int(player_id)]['yahoo_name'].values[0]
            gm_name = df_teams[df_teams['team_key'] == team_key]['gm_name'].values[0]
            team_name = df_teams[df_teams['team_key'] == team_key]['name'].values[0]
            source_key = '99.l.99.t.99'
            source = 'Free Agency'
            try:
                # Keeper check: look up whether this player is listed as a keeper
                # in the control_file for this team/year. Falls back to NO for
                # pre-2018 seasons where keeper data wasn't tracked.
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

        self.df_draft = pd.DataFrame(draft_arr)
        output_dir = f'{self.current_directory}/league_drafts'
        os.makedirs(output_dir, exist_ok=True)

        output_file = f'{output_dir}/{self.year}_league_draft.csv'
        self.df_draft.columns = self.df_draft.columns.str.upper()
        self.df_draft.to_csv(output_file, index=False)

    def extract_yahoo_rosters(self):
        """
        Extract and save each team's daily roster and lineup positions for all dates in self.dates_to_check.

        For each date, queries Yahoo for every team's roster on that day, capturing
        the player's selected fantasy slot (C, LW, BN, IR+, etc.), injury notes,
        and ownership percentages. Stats are intentionally excluded here —
        they are pulled more accurately from Hockey Reference via parse_HR_data().

        One CSV is written per date to team_rosters_by_date/<year>/<year>_rosters_<date>.csv.

        Side effects:
            - Creates team_rosters_by_date/<year>/ directory if absent.
            - Writes one CSV per date.
            - Sleeps 10 seconds between dates to respect Yahoo API rate limits.
        """
        print(f'[{time.ctime()}] Getting yahoo team roster player info for year {self.year}')
        df_teams = pd.read_csv(f'{self.current_directory}/league_teams/{self.year}_league_teams.csv')
        for date in self.dates_to_check:
            iter_date = date
            print(f'[{time.ctime()}] Processing date: {iter_date}')
            date_arr = []
            df_roster_stats_date = pd.DataFrame()
            for team_code in df_teams['team_id'].unique():
                team_id = df_teams[df_teams['team_id'] == team_code]['team_key'].values[0]
                team_name = df_teams[df_teams['team_id'] == team_code]['name'].values[0]
                team_gm = df_teams[df_teams['team_id'] == team_code]['gm_name'].values[0]
                roster = self.query.get_team_roster_player_info_by_date(team_id=team_code, chosen_date=iter_date)
                if not roster:
                    print(f'No roster found for team {team_code} on date {iter_date}')
                    continue
                for player in roster:
                    player_daily_metadata = player.clean_data_dict()
                    player_metadata_dict = {}

                    player_metadata_dict={
                        'SEASON': self.year,
                        'NAME': player_daily_metadata.get('name', {}).get('full', 'Unknown Player'),
                        'PLAYER_ID': player_daily_metadata.get('player_id', 0),
                        'PLAYER_KEY': player_daily_metadata.get('player_key', '?'),
                        'DISPLAY_POSITION': player_daily_metadata.get('display_position', '?'),
                        'INJURY_NOTE': player_daily_metadata.get('injury_note', ''),
                        'OWNER_TEAM_KEY': team_id,
                        'OWNER_TEAM_NAME': team_name,
                        'OWNER_TEAM_GM': team_gm,
                        'SELECTED_POSITION': player_daily_metadata.get('selected_position', {}).get('position', '?'),
                        'ELIGIBLE_POSITIONS': player_daily_metadata.get('eligible_positions', []),
                        'PERCENT_OWNED': player_daily_metadata.get('percent_owned', {}).get('value', 0),
                        'PERCENT_OWNED_DELTA': player_daily_metadata.get('percent_owned', {}).get('delta', 0.0),
                    }
                    # No need for stats, I will grab them from the fine folks at hockeyreference
                    date_arr.append(player_metadata_dict)

            date_df = pd.DataFrame(date_arr)
            # Handle the two known same-name player collisions before saving
            date_df = self.identical_names_handler(date_df, 'yahoo') if len(date_df) > 0 else date_df
            output_dir = f'{self.current_directory}/team_rosters_by_date/{self.year}'
            os.makedirs(output_dir, exist_ok=True)
            output_file = f"{output_dir}/{self.year}_rosters_{iter_date}.csv"
            date_df['DATE'] = iter_date
            date_df.to_csv(output_file, index=False)
            time.sleep(10)  # Sleep for 10 seconds to avoid API rate limits
            iter_date = date_df['DATE'].iloc[0]
            date_df['UID'] = date_df['DATE'] + '_' + date_df['PLAYER_ID'].astype(str)
            date_df['RUNDATE'] = time.ctime()
            date_df.drop(columns=['PLAYER_KEY'], inplace=True)
            date_df = date_df.rename(columns={'OWNER_TEAM_KEY':'OWNER_TEAM_ID'})

    def parse_HR_data(self):
        """
        Scrape per-player box score stats from Hockey Reference for all dates in self.dates_to_check.

        For each date, looks up the corresponding NHL games from self.schedule_df
        (populated by NHL_schedule_parser), then scrapes each game's box score page
        to extract:
            - Skater stats: G, A, PTS, +/-, PIM, PP/SH goals & assists, shots, TOI, advanced metrics
            - Goalie stats: DEC (W/L/OTL), GA, SA, SV, SV%, SO

        Home and away team stats are extracted from separate HTML tables, merged,
        and written to hr_data_extract/<year>/HR_<date>.csv.

        A 5-second sleep between individual game requests and a 30-second sleep
        between dates are included to avoid overwhelming Hockey Reference's servers.

        Side effects:
            - Creates hr_data_extract/<year>/ directory if absent.
            - Writes hr_data_extract/<year>/HR_<date>.csv per date.
        """
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

                # --- Home team skater stats ---
                # Table index 2: home team skaters (basic + goals breakdown)
                home_df = pd.read_html(StringIO(str(tables[2])))[0]
                # Flatten the two-level header: keep 'Goals'/'Assists' labels, blank others
                home_df = home_df.rename(columns=lambda x: x if x in ['Goals', 'Assists'] else '', level=0)
                home_df.columns = [f"{col[0]}{col[1]}" for col in home_df.columns]
                home_df['TEAM'] = tables[2]['id'].split('_')[0]

                # Extract HR_LINK_NAME from hidden data-append-csv cell attributes —
                # this is the slug used to uniquely identify each player in HR URLs.
                rows = tables[2].find_all(['th', 'tr'])
                row_count = 0
                for row in rows:
                    cells = row.find_all('td')
                    for cell in cells:
                        if 'data-append-csv' in cell.attrs:
                            link_name = cell['data-append-csv']
                            home_df.at[row_count, 'HR_LINK_NAME'] = link_name
                            row_count += 1

                # Table index 6: home team advanced skater stats (CF%, etc.)
                adv_df = pd.read_html(StringIO(str(tables[6])))[0]
                home_df = home_df.merge(adv_df, how='left', on='Player')

                # Table index 3: home team goalie stats
                goalie_df = pd.read_html(StringIO(str(tables[3])))[0]
                goalie_df.columns = [f"{col[1]}" for col in goalie_df.columns]
                goalie_df.drop(columns=['Rk', 'PIM', 'TOI'], inplace=True)
                home_df = home_df.merge(goalie_df, how='left', on='Player')
                # Remove the TOTAL row that HR appends at the bottom of each table
                home_df = home_df[home_df['Player'] != 'TOTAL']

                # --- Away team skater stats ---
                # Table index 4: away team skaters (same structure as home)
                away_df = pd.read_html(StringIO(str(tables[4])))[0]
                away_df = away_df.rename(columns=lambda x: x if x in ['Goals', 'Assists'] else '', level=0)
                away_df.columns = [f"{col[0]}{col[1]}" for col in away_df.columns]
                away_df['TEAM'] = tables[4]['id'].split('_')[0]
                rows = tables[4].find_all(['th', 'tr'])
                row_count = 0
                for row in rows:
                    cells = row.find_all('td')
                    for cell in cells:
                        if 'data-append-csv' in cell.attrs:
                            link_name = cell['data-append-csv']
                            away_df.at[row_count, 'HR_LINK_NAME'] = link_name
                            row_count += 1

                # Table index 13: away team advanced stats
                adv_df = pd.read_html(StringIO(str(tables[13])))[0]
                away_df = away_df.merge(adv_df, how='left', on='Player')

                # Table index 5: away team goalie stats
                goalie_df = pd.read_html(StringIO(str(tables[5])))[0]
                goalie_df.columns = [f"{col[1]}" for col in goalie_df.columns]
                goalie_df.drop(columns=['Rk', 'PIM', 'TOI'], inplace=True)
                away_df = away_df.merge(goalie_df, how='left', on='Player')
                away_df = away_df[away_df['Player'] != 'TOTAL']

                # Combine home and away into one frame for the game
                merge_df = pd.concat([home_df, away_df]).reset_index(drop=True)
                merge_df.drop(columns=['Rk'], inplace=True)
                date_df = pd.concat([date_df, merge_df]).reset_index(drop=True)
                print(f'Completed extraction for game {url_code} on date {date}')
                time.sleep(5)  # Be polite and avoid overwhelming the server

            # Ensure all expected columns exist even if absent from today's box scores
            # (e.g. no shutouts → SO column may be missing)
            for col in ['PLAYER',	'G',	'A',	'PTS',	'+/-',	'PIM',	'GOALSEV'	,'GOALSPP'	,'GOALSSH',	'GOALSGW',	'ASSISTSEV'	,'ASSISTSPP',	'ASSISTSSH',	'S'	,'S%',	'SHFT',	'TOI',	'TEAM',	'HR_LINK_NAME',	'ICF',	'SAT‑F',	'SAT‑A',	'CF%',	'CREL%',	'ZSO',	'ZSD',	'OZS%',	'HIT',	'BLK',	'DEC',	'GA',	'SA',	'SV',	'SV%',	'SO']:
                if col not in date_df.columns.to_list():
                    date_df[col] = ''

            date_df['DATE'] = date
            season = int(self.year)
            date_df['SEASON'] = season
            date_df.columns = date_df.columns.str.strip().str.upper()
            # Deduplicate on HR_LINK_NAME — skaters who played on two teams in one
            # day (rare) or any scraping artifact rows
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
        Fuzzy-match Yahoo player names against Hockey Reference player names.

        Uses RapidFuzz token_sort_ratio scoring with aggressive ASCII normalization
        and first-name abbreviation substitution to handle common spelling variants
        (e.g. "Alexander" → "Alex", accented characters stripped). Flags any match
        below review_threshold for manual review and detects collisions where the
        same HR name was matched to multiple Yahoo players.

        Args:
            player_df (pd.DataFrame): Yahoo player rows to be matched. Only
                rows with HR_REVIEW_FLAG=True should be passed in.
            hr_df (pd.DataFrame): Reference DataFrame containing HR player names
                and their URL slugs.
            player_name_col (str): Column in player_df containing Yahoo display names.
            hr_name_col (str): Column in hr_df containing HR display names.
            hr_link_name_col (str): Column in hr_df containing HR URL slugs (e.g. "crosbsi01").
            output_match_col (str): Name of the column to write matched HR display names into.
                Defaults to "HR_MATCH_NAME".
            output_link_col (str): Name of the column to write matched HR slugs into.
                Defaults to "HR_LINK_NAME".
            output_score_col (str): Name of the column to write fuzzy match scores into.
                Defaults to "HR_MATCH_SCORE".
            collision_flag_col (str): Name of the column to write collision flags into.
                Defaults to "HR_COLLISION_FLAG".
            review_flag_col (str): Name of the column to write review flags into.
                Defaults to "HR_REVIEW_FLAG". Set to True if score < review_threshold
                or if no match was found above score_cutoff.
            score_cutoff (int): Minimum fuzzy score (0-100) for a match to be
                considered at all. Defaults to 80.
            review_threshold (int): Minimum score (0-100) for a match to be
                considered confirmed (HR_REVIEW_FLAG=False). Defaults to 90.

        Returns:
            pd.DataFrame: A copy of player_df with match results written into the
                output columns. The "_norm_player_name" working column is removed
                before returning.
        """

        import re
        import unicodedata

        try:
            from rapidfuzz import fuzz
            from rapidfuzz import process as rf_process
        except ImportError:
            raise ImportError("RapidFuzz is required. Install via: pip install rapidfuzz")

        # -------------------------------------------------------------------
        # HARD normalization (ASCII only)
        # Converts accented characters, applies first-name abbreviations,
        # lowercases, removes all non-alphabetic characters, and collapses
        # internal whitespace. Produces a canonical form for fuzzy comparison.
        # -------------------------------------------------------------------
        def normalize(name):
            if pd.isna(name):
                return ""

            name = str(name)
            first_name = name.split(' ', 1)[0]
            last_part = name.split(' ', 1)[1]
            # Substitute long-form first names with their common short form
            first_name = self.first_name_dict[first_name] if first_name in self.first_name_dict.keys() else first_name
            name = first_name + ' ' + last_part

            # Strip diacritics (é → e, ø → o, etc.)
            name = unicodedata.normalize("NFKD", name)
            name = name.encode("ascii", "ignore").decode("ascii")

            name = name.lower()

            # Remove anything that isn't a letter or space
            name = re.sub(r"[^a-z\s]", " ", name)

            # Collapse multiple spaces into one
            name = re.sub(r"\s+", " ", name).strip()

            return name

        # -------------------------------------------------------------------
        # Build HR lookup tables
        # Normalize all HR names and create a dict keyed by normalized name
        # for O(1) retrieval after fuzzy matching resolves the best match.
        # -------------------------------------------------------------------
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

        # -------------------------------------------------------------------
        # Single-name fuzzy match function
        # Returns (HR display name, HR slug, match score) or (None, None, None)
        # if no match exceeds score_cutoff.
        # -------------------------------------------------------------------
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

        # Normalize all Yahoo player names in the input DataFrame
        df = player_df.copy()
        df["_norm_player_name"] = df[player_name_col].apply(normalize)

        # Apply match_one to every row and unpack the three-tuple results
        results = df["_norm_player_name"].apply(match_one)

        df[output_match_col] = results.apply(lambda x: x[0])
        df[output_link_col] = results.apply(lambda x: x[1])
        df[output_score_col] = results.apply(lambda x: x[2])

        # Flag for review: no match found, or match score below confidence threshold
        df[review_flag_col] = (
                df[output_score_col].isna() |
                (df[output_score_col] < review_threshold)
        )

        # Collision detection: if the same HR name was matched to more than one
        # Yahoo player_id, both rows get collision_flag=True for manual review.
        collision_counts = (
            df
            .dropna(subset=[output_match_col])
            .groupby(output_match_col)
            .size()
        )

        collisions = collision_counts[collision_counts > 1].index

        df[collision_flag_col] = df[output_match_col].isin(collisions)

        # Remove the internal working column before returning
        df.drop(columns=["_norm_player_name"], inplace=True)

        return df
