"""
analytics_engine.py

Post-extraction analytics engine for the fantasy hockey pipeline.

Defines ANALYTICS_ENGINE — the class responsible for all pandas-based aggregation,
metric calculation, and CSV output that runs after raw data has been collected by
generic.py / main.py.

All methods in this class operate exclusively on CSV files already on disk —
no Yahoo API or web scraping calls are made here.

Output directories created by this module:
    merged_extracts/<year>/         — per-day merged Yahoo + HR stat files
    merged_extracts_years/          — full-season combined stat files
    analytics_matchups/             — per-week head-to-head results
    analytics_power_ranks/          — monthly power rankings
    analytics_loyalty/              — GM roster loyalty scores
    analytics_keepers/              — keeper ROI stats
    analytics_draft/                — draft pick ROI stats
    analytics_faab/                 — FAAB spend analysis
    analytics_streamers/            — in-week pickup performance
    analytics_engagement/           — weekly GM engagement metrics
    analytics_hospital/             — player injury streak records
    csv_databases/                  — aggregated multi-year flat files for Power BI
    sql_tables/                     — DuckDB database (rebuilt by create_csv_databases)
    debug/                          — per-team per-week stat dumps for discrepancy diagnosis
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
from scipy.stats import zscore


pd.options.display.float_format = '{:,}'.format
pd.set_option('mode.chained_assignment', None)

class ANALYTICS_ENGINE:
    """
    Post-extraction analytics engine for a single fantasy hockey season.

    All methods read from CSVs produced by YEAR_INSTANCE (generic.py) and
    write their results to dedicated output folders. No API calls are made.

    Attributes:
        control_file (dict): Full runtime config from control_file.json.
        year (str|int): The season being processed.
        stats_for_year (list): Scoring category names for this season.
        league_id (str): Yahoo league ID.
        game_id (int): Yahoo game ID.
        current_directory (str): Absolute path to the project root.
        ref_list (pd.DataFrame): NHL team code reference table.
        master_metadata_file (pd.DataFrame): All-years player master (Yahoo↔HR mapping).
        df_league_teams (pd.DataFrame): Combined all-seasons team/GM info from csv_databases/.
        first_name_dict (dict): Long-form → short-form first name map (shared with generic.py).
        fp_score_dict_skaters (dict): Fantasy point weights per skater stat category.
        fp_score_dict_goalies (dict): Fantasy point weights per goalie stat category.
        months (dict): Integer month → month name map.
        goalie_req_bypass (dict): Season year → list of weeks where the 3-goalie-start
            minimum is waived (e.g. shortened weeks due to scheduling).
    """

    def __init__(self, control_file, current_directory, year):
        """
        Initialize the analytics engine for a given season.

        Loads reference data from disk (player master, team list) that is
        required by downstream analytics methods.

        Args:
            control_file (dict): Parsed content of control_file.json.
            current_directory (str): Absolute path to project root.
            year (str|int): Season year to process (e.g. 2025).
        """
        self.control_file       = control_file
        self.year               = year
        self.stats_for_year     = self.control_file["Years"][str(self.year)]['scoring_categories']
        self.league_id          = str(self.control_file['Years'][str(self.year)]['league_id'])
        self.game_id            = int(self.control_file['Years'][str(self.year)]['game_id'])
        self.current_directory  = current_directory
        print(f'[{time.ctime()}] Initializing instance for Year {self.year} | League ID {self.league_id} | Game ID {self.game_id}')

        # Load supporting reference files needed by analytics methods
        self.ref_list = pd.read_csv(f'{self.current_directory}/manual_data/Hockey_Team_Codes.csv')
        self.master_metadata_file = pd.read_csv(f'{self.current_directory}/manual_data/PLAYER_MASTER_DATA.csv')

        # csv_databases/LEAGUE_TEAMS.csv is the aggregated all-years teams file
        # produced by create_csv_databases() — used for GM name lookups
        self.df_league_teams = pd.read_csv('csv_databases/LEAGUE_TEAMS.csv')

        # Shared with generic.py — used wherever name normalization is needed
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

        # Fantasy point weights used to compute a single composite FP score per player per game.
        # Skater and goalie weights are kept separate because they use different stat columns.
        self.fp_score_dict_skaters = {
            'G':0.36,
            'A':0.24,
            '+/-':0.1,
            'PIM':0.1,
            'PPP':0.4,
            'SHP':0.7,
            'S':0.06,
            'HIT':0.1,
            'BLK':0.12
        }
        self.fp_score_dict_goalies = {
            'SV': 0.025,
            'SO': 1,
            'GA': -0.1,
            'W': 0.5,
            'L': -0.5
        }
        self.months = {
            1: 'January', 2: 'February', 3: 'March', 4: 'April',
            5: 'May', 6: 'June', 7: 'July', 8: 'August',
            9: 'September', 10: 'October', 11: 'November', 12: 'December'
        }

        # Weeks where the 3-start goalie minimum is bypassed — e.g. short weeks
        # caused by NHL scheduling anomalies where no team could realistically
        # get 3 goalie starts in a fantasy week.
        self.goalie_req_bypass = {
            2025:[12]
        }

######################################################################################################
######################################################################################################
######################################################################################################
    def helper_check_for_missed_starts(self, df):
        """
        Flag benched players who could have been slotted into an active lineup position.

        For each GM's roster on a given day, identifies skaters who:
            1. Were placed on the bench (SELECTED_POSITION == 'BN'), AND
            2. Actually played an NHL game that day (GAME_PLAYED == 1), AND
            3. Had at least one eligible fantasy position with an open slot.

        A player is flagged MISSED_START = 1 only if there was a valid slot they
        could have filled. Goalies are intentionally excluded from this check
        because benching a goalie can be a deliberate strategic decision.

        The MISSED_START flag is set directly on the input DataFrame in-place.

        Args:
            df (pd.DataFrame): A single-day merged roster+stat DataFrame with
                columns including OWNER_TEAM_NAME, SELECTED_POSITION, GAME_PLAYED,
                DISPLAY_POSITION, and NAME.

        Returns:
            pd.DataFrame: The same DataFrame with MISSED_START column updated.
        """
        for gm in df['OWNER_TEAM_NAME'].unique():
            if gm == 'FREE_AGENTS':
                continue
            else:
                df_gm = df[df['OWNER_TEAM_NAME']==gm]
                df_gm_benched=df_gm[df_gm['SELECTED_POSITION']=='BN']
                # Not doing goalies - sometimes these are tactically benched!
                df_gm_benched_w_games = df_gm_benched[(df_gm_benched['GAME_PLAYED']==1)&(df_gm_benched['DISPLAY_POSITION']!='G')]
                if len(df_gm_benched_w_games)==0:
                    # No benched skaters with games today — nothing to check
                    continue
                else:
                    # Calculate available slots for each position type.
                    # The UTIL slot can absorb any forward or D, so it reduces
                    # the effective "missed" count for any position.
                    # 2011 was the only year we didn't have a util spot
                    avail_UTIL_spots = 1-len(df_gm[(df_gm['SELECTED_POSITION']=='Util')&(df_gm['GAME_PLAYED']==1)]) if int(self.year)!= 2011 else 0
                    avail_C_spots = 2 -len(df_gm[(df_gm['SELECTED_POSITION']=='C')&(df_gm['GAME_PLAYED']==1)])
                    avail_LW_spots = 2 -len(df_gm[(df_gm['SELECTED_POSITION']=='LW')&(df_gm['GAME_PLAYED']==1)])
                    avail_RW_spots = 2 -len(df_gm[(df_gm['SELECTED_POSITION']=='RW')&(df_gm['GAME_PLAYED']==1)])
                    avail_D_spots = 4 -len(df_gm[(df_gm['SELECTED_POSITION']=='D')&(df_gm['GAME_PLAYED']==1)])
                    for player in df_gm_benched_w_games['NAME'].unique():
                        player_df = df_gm_benched_w_games[df_gm_benched_w_games['NAME']==player]
                        position = player_df['DISPLAY_POSITION'].iloc[0]
                        date = player_df['DATE'].iloc[0] # for diangostics
                        try:
                            # Some players have multiple eligible positions (e.g. "C,LW")
                            position_arr = position.split(',')
                        except:
                            position_arr = [position]

                        # Check each eligible position in order; flag on first valid open slot.
                        # Uses break to avoid double-counting a player across multiple positions.
                        for pos in position_arr:
                            if pos == 'C' and (avail_C_spots>0 or  avail_UTIL_spots>0):
                                #print(f'On {date} {player} from {gm} could have been played in a {pos} or UTIL spot')
                                df.loc[(df['PLAYER']==player)&(df['OWNER_TEAM_NAME']==gm),'MISSED_START'] = 1
                                break
                            elif pos == 'LW' and (avail_LW_spots>0 or  avail_UTIL_spots>0):
                                #print(f'On {date} {player} from {gm} could have been played in a {pos} or UTIL spot')
                                df.loc[(df['PLAYER']==player)&(df['OWNER_TEAM_NAME']==gm),'MISSED_START'] = 1

                                break
                            elif pos == 'RW' and (avail_RW_spots>0 or  avail_UTIL_spots>0):
                                #print(f'On {date} {player} from {gm} could have been played in a {pos} or UTIL spot')
                                df.loc[(df['PLAYER']==player)&(df['OWNER_TEAM_NAME']==gm),'MISSED_START'] = 1

                                break
                            elif pos == 'D' and (avail_D_spots>0 or  avail_UTIL_spots>0):
                                #print(f'On {date} {player} from {gm} could have been played in a {pos} or UTIL spot')
                                df.loc[(df['PLAYER']==player)&(df['OWNER_TEAM_NAME']==gm),'MISSED_START'] = 1

                                break
                            else:
                                continue

        return df

    def link_hr_days_to_yh_days(self):
        """
        Merge per-day Yahoo roster CSVs with Hockey Reference box score CSVs.

        For each date found in team_rosters_by_date/<year>/, loads the corresponding
        HR box score CSV from hr_data_extract/<year>/, joins them on HR_LINK_NAME
        (the unique player slug), computes derived fantasy stats (PPP, SHP, FP, W/L,
        TOI in decimal minutes, GAA), tags GAME_PLAYED, and runs the missed-start check.

        The outer merge (how='outer') ensures that:
            - Yahoo-rostered players with no HR data (rest days) are retained.
            - Free-agent players who played are retained for context.

        Output is written to merged_extracts/<year>/MERGED_<date>.csv.

        Side effects:
            - Creates merged_extracts/<year>/ directory if absent.
            - Writes one MERGED_<date>.csv per available roster date.
        """
        player_mapping = self.master_metadata_file[self.master_metadata_file['season']==int(self.year)]
        dates_df = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')

        df_all_stats = pd.DataFrame()

        for filename in glob.glob(f'{self.current_directory}/team_rosters_by_date/{self.year}/*.csv', recursive=True):
            yh_df = pd.read_csv(filename)
            # Extract date and year from the filename (format: <year>_rosters_<date>.csv)
            date = filename.split('/')[-1].split('_')[-1].split('.')[0]
            year = filename.split('\\')[-1].split('_')[0]
            try:
                week = dates_df[dates_df['date'] == date]['week'].iloc[0]
            except:
                # Date falls outside configured league weeks (e.g. preseason)
                print(f'{date} is outside of league weeks range, setting to 0')
                week = 0

            # Construct the path to the corresponding HR box score CSV
            hr_filename = filename.split('/')[0] + '/' + 'hr_data_extract' + f'/{year}/HR_{date}.csv'
            try:
                hr_df = pd.read_csv(hr_filename)

            except FileNotFoundError:
                print(f'File not found: {hr_filename}. Creating blank HR dataframe.')
                continue

            # Join Yahoo player_id → HR_LINK_NAME via the player mapping master
            yh_df = yh_df.merge(player_mapping[['yahoo_name', 'player_id', 'HR_LINK_NAME']], left_on="PLAYER_ID",
                                right_on="player_id", how="left")

            # Outer join on HR_LINK_NAME so free agents and rostered players both appear
            yh_df = yh_df.merge(hr_df, left_on="HR_LINK_NAME", right_on="HR_LINK_NAME", how="outer",
                                suffixes=('_YH', '_HR'))

            yh_df['WEEK'] = week
            yh_df['SEASON'] = year
            yh_df['DATE'] = date
            yh_df['MONTH'] = self.months[int(date.split('-')[1])]

            os.makedirs(f'{self.current_directory}/merged_extracts/{year}/', exist_ok=True)

            # Prefer Yahoo NAME for rostered players; fall back to HR PLAYER name for free agents
            yh_df['NAME'] = yh_df['NAME'].fillna(yh_df['PLAYER'])

            # Derive composite PP and SH stats from the component goal/assist columns
            yh_df['PPP'] = yh_df['GOALSPP']+yh_df['ASSISTSPP']
            yh_df['SHP'] = yh_df['GOALSSH']+yh_df['ASSISTSSH']

            # Convert goalie decision string ('W'/'L') to binary integer columns
            yh_df['W'] = yh_df['DEC'].apply(lambda x: 1 if x == 'W' else 0)
            yh_df['L'] = yh_df['DEC'].apply(lambda x: 1 if x == 'L' else 0)

            # Convert TOI from "MM:SS" string to decimal minutes for arithmetic
            yh_df['MINUTES'] = yh_df['TOI'].apply(lambda x: int(x.split(':')[0]) if pd.notna(x) else 0)
            yh_df['SECONDS'] = yh_df['TOI'].apply(lambda x: int(x.split(':')[1]) if pd.notna(x) else 0)
            yh_df['TOI'] = yh_df['MINUTES'] + yh_df['SECONDS'] / 60

            # GOALIE_TOI is only non-zero for goalies; used downstream to
            # distinguish goalies from skaters without relying on position strings
            yh_df['GOALIE_TOI'] = yh_df.apply(lambda x: x['TOI'] if x['DISPLAY_POSITION'] == 'G' else 0, axis=1)

            # GAA computed from per-game GA / TOI; empty string for skaters / no-game
            yh_df['GAA'] = yh_df.apply(lambda x: round(x['GA'] / x['GOALIE_TOI']  * 60, 3) if x['GOALIE_TOI']  > 0 else '', axis = 1)
            yh_df['SV'] = yh_df['SV']
            yh_df['SA'] = yh_df['SA']
            yh_df['GA'] = yh_df['GA']
            yh_df['SO'] = yh_df['SO']
            yh_df['W'] = yh_df['W']
            yh_df['L'] = yh_df['L']
            yh_df['GWG'] = yh_df['GOALSGW'].copy()

            # Fill missing ownership metadata for players not on any Yahoo roster
            yh_df['OWNER_TEAM_KEY'] = yh_df['OWNER_TEAM_KEY'].fillna('999.l.99999.t.99')
            yh_df['OWNER_TEAM_NAME'] = yh_df['OWNER_TEAM_NAME'].fillna('FREE_AGENTS')
            yh_df['OWNER_TEAM_GM'] = yh_df['OWNER_TEAM_GM'].fillna('FREE_AGENCY')

            # Compute composite fantasy point scores using the weighted stat dicts
            yh_df['FP_SKATER'] = 0
            for col in self.fp_score_dict_skaters.keys():
                yh_df['FP_SKATER'] += yh_df[col]*self.fp_score_dict_skaters[col]

            yh_df['FP_GOALIE'] = 0
            for col in self.fp_score_dict_goalies.keys():
                yh_df['FP_GOALIE'] += yh_df[col]*self.fp_score_dict_goalies[col]

            # Combined FP: one of the two components will always be ~0 per player
            yh_df['FP'] = yh_df['FP_SKATER'].fillna(0) + yh_df['FP_GOALIE'].fillna(0)

            # GAME_PLAYED = 1 if the player registered at least one goal (i.e., G is not null),
            # indicating they actually took the ice that day
            yh_df['GAME_PLAYED'] = yh_df['G'].apply(lambda x: 1 if pd.notnull(x) else 0)
            yh_df['MISSED_START'] = 0

            # Check all benched players with games for missed-start opportunities
            yh_df = self.helper_check_for_missed_starts(yh_df)

            # Trim to the canonical column set before writing to disk
            yh_df = yh_df[[
                'SEASON',
                'MONTH',
                'WEEK',
                'DATE',
                'NAME',
                'player_id',
                'HR_LINK_NAME',
                'PLAYER',
                'PLAYER_ID',
                'PLAYER_KEY',
                'GAME_PLAYED',
                'MISSED_START',
                'DISPLAY_POSITION',
                'INJURY_NOTE',
                'OWNER_TEAM_KEY',
                'OWNER_TEAM_NAME',
                'OWNER_TEAM_GM',
                'SELECTED_POSITION',
                'ELIGIBLE_POSITIONS',
                'PERCENT_OWNED',
                'PERCENT_OWNED_DELTA',
                'FP',
                'G',
                'A',
                'PTS',
                '+/-',
                'PIM',
                'PPP',
                'SHP',
                'GWG',
                'S',
                'S%',
                'SHFT',
                'TOI',
                'GOALIE_TOI',
                'TEAM',
                'ICF',
                'SAT‑F',
                'SAT‑A',
                'CF%',
                'CREL%',
                'ZSO',
                'ZSD',
                'OZS%',
                'HIT',
                'BLK',
                'W',
                'L',
                'GA',
                'SA',
                'SV',
                'SV%',
                'SO',
                'GAA'
            ]]
            # Drop any duplicate HR slugs (can occur when a player appears in
            # both basic and goalie tables for the same game)
            yh_df.drop_duplicates('HR_LINK_NAME', keep='first', inplace=True)
            yh_df.to_csv(f'{self.current_directory}/merged_extracts/{year}/MERGED_{date}.csv', index=False)


    def yearly_stat_roster_combiner(self):
        """
        Concatenate all daily merged CSVs for self.year into a single season file.

        Reads every MERGED_<date>.csv from merged_extracts/<year>/ and stacks them
        into one DataFrame, which is written to merged_extracts_years/<year>_ALL_DATA.csv.

        This file is the primary input for all downstream analytics methods.

        Side effects:
            - Creates merged_extracts_years/ directory if absent.
            - Writes merged_extracts_years/<year>_ALL_DATA.csv.
        """
        print(f'RUNNING ROSTER-STAT COMBINER FOR YEAR {self.year}')
        df_all_data =pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/merged_extracts/{self.year}/*.csv',recursive=True):
            df_ = pd.read_csv(file)
            df_all_data = pd.concat([df_all_data,df_])

        os.makedirs(f'{self.current_directory}/merged_extracts_years/', exist_ok=True)
        df_all_data.to_csv(f'{self.current_directory}/merged_extracts_years/{self.year}_ALL_DATA.csv', index=False)

    def matchup_analytics(self):
        """
        Compute per-week head-to-head matchup stats and quality scores for self.year.

        For each week and each matchup in the league scoreboard, aggregates the
        active-roster stats for both teams from the merged season file. Computes:
            - Raw stat totals (G, A, SOG, HIT, BLK, SV, etc.)
            - FP and benched FP totals
            - Missed starts and failed goalie requirement flags
            - Category-by-category win/loss (CALCULATED_SCORE / CALCULATED_RESULT)
            - Cross-validation against Yahoo's official score (SCORE_CHECK / RESULT_CHECK)
            - WEEK_QS: a normalized quality score (0-10) based on percentile rank
              across all categories for that week
            - YEAR_QS: the same quality score computed across the full season

        A goalie requirement rule is enforced: teams with fewer than 3 goalie starts
        in a week receive penalty values for all goalie stats, unless the week is
        in self.goalie_req_bypass.

        Any score discrepancies (RESULT_CHECK == False) are looked up in
        control_file['matchup_metadata'] and printed with their explanation and status.

        Debug CSVs with the per-team per-week raw stats are written to debug/ for
        post-hoc investigation.

        Side effects:
            - Creates analytics_matchups/ and debug/ directories if absent.
            - Writes analytics_matchups/<year>_matchup_data.csv.
            - Writes debug/<year>_<team>_week_<N>_results_for_debug.csv per matchup side.
        """

        print(f'RUNNING MATCHUP ANALYTICS FOR YEAR {self.year}')
        cf_year = self.control_file['Years'][str(self.year)]
        df_merged = pd.read_csv(f'merged_extracts_years/{self.year}_ALL_DATA.csv',low_memory=False)

        # Rename columns to their canonical scoring category names
        df_merged.rename({'PTS': 'P', 'GOALSPP': 'PPG', 'ASSISTSPP': 'PPA', 'GOALSSH': 'SHG', 'ASSISTSSH': 'SHA',
                          'GOALSGW': 'GWG', 'HIT': 'HIT', 'BLK': 'BLK', 'S': 'SOG'}, axis=1, inplace=True)

        # Column schema for the output matchup results DataFrame
        basic_cols =   ['SEASON', 'PRIMARY_MONTH','WEEK', 'MATCHUP', 'TEAM_NAME', 'TEAM_KEY', 'GM_NAME','RESULT', 'SCORE','GAME_DAYS', 'G', 'A', 'P',
                             '+/-', 'PIM', 'PPP', 'SHP', 'SOG', 'SH%', 'GWG', 'HIT', 'BLK', 'SV', 'SO', 'GA', 'SA',
                             'SV%', 'GOALIE_TOI', 'W', 'L', 'GAA', 'SKATER_STARTS','BENCH_STARTS', 'GOALIE_STARTS',
                             'FAILED_GOALIE_REQUIREMENT','FP','BENCH_FP','MISSED_START']


        df_year_matchup_results = pd.DataFrame()
        df_league_scoreboard = pd.read_csv(
            f'{self.current_directory}/league_scoreboards_by_week/{self.year}_league_scoreboards.csv')

        for week in df_league_scoreboard['week'].unique():
            df_week = df_league_scoreboard[df_league_scoreboard['week'] == week]
            df_week_results = pd.DataFrame(columns=basic_cols)
            game_days = len(df_merged[df_merged['WEEK']==int(week)]['DATE'].unique())

            if len(df_merged[df_merged['WEEK']==int(week)])==0:
                print(f'Week {week} has no data yet! Skipping matchup analytics for this week')
                continue

            # Use the first date of the week to tag the month (for power rankings)
            primary_month = df_merged[df_merged['WEEK']==int(week)]['MONTH'].iloc[0]

            for matchup in df_week['matchup'].unique():
                df_matchup_results = pd.DataFrame(columns=basic_cols)
                df_matchup = df_week[df_week['matchup'] == matchup]
                winner_team_key = df_matchup['winner_team_key'].iloc[0]
                team_a_points = df_matchup['team_a_total_points'].iloc[0]
                team_b_points = df_matchup['team_b_total_points'].iloc[0]
                team_a_wlt_result = df_matchup['team_a_result'].iloc[0]
                team_b_wlt_result = df_matchup['team_b_result'].iloc[0]
                team_a_key = df_matchup['team_a_key'].iloc[0]
                team_a_name = df_matchup['team_a_name'].iloc[0]
                team_b_key = df_matchup['team_b_key'].iloc[0]
                team_b_name = df_matchup['team_b_name'].iloc[0]

                # Filter to active roster players (exclude bench and IR spots)
                df_team_a_stats = df_merged[
                    (df_merged['OWNER_TEAM_KEY'] == team_a_key) & (df_merged['WEEK'] == week) &
                    (df_merged['SELECTED_POSITION'] != 'BN') &
                    (df_merged['SELECTED_POSITION'] != 'IR+') &
                    (df_merged['SELECTED_POSITION'] != 'IR')&
                    (df_merged['SELECTED_POSITION'] != 'NA')]
                gm_name = df_team_a_stats['OWNER_TEAM_GM'].iloc[0]

                # Bench filter: BN, IR+, IR, NA are all non-scoring slots
                df_team_a_benched = df_merged[
                    (df_merged['OWNER_TEAM_KEY'] == team_a_key) & (df_merged['WEEK'] == week) & (
                    (df_merged['SELECTED_POSITION'] == 'BN') |
                    (df_merged['SELECTED_POSITION'] == 'IR+') |
                    (df_merged['SELECTED_POSITION'] == 'IR') |
                    (df_merged['SELECTED_POSITION'] == 'NA'))]

                # Count of skaters and goalies who actually played (TOI > 0) this week
                benched_starts = len(
                    df_team_a_benched[
                        (df_team_a_benched['SELECTED_POSITION'] != 'G') & (df_team_a_benched['TOI'] != 0)])

                skater_starts = len(
                    df_team_a_stats[(df_team_a_stats['SELECTED_POSITION'] != 'G') & (df_team_a_stats['TOI'] != 0)])
                goalie_starts = len(
                    df_team_a_stats[(df_team_a_stats['SELECTED_POSITION'] == 'G') & (df_team_a_stats['TOI'] != 0)])

                # Exclude goalies from skater stat sums to avoid contaminating totals
                df_a_stats_no_goalies = df_team_a_stats[df_team_a_stats['DISPLAY_POSITION']!='G']

                goals = df_a_stats_no_goalies['G'].sum()
                assists = df_a_stats_no_goalies['A'].sum()
                points = df_a_stats_no_goalies['P'].sum()
                plus_minus = df_a_stats_no_goalies['+/-'].sum()
                pim = df_a_stats_no_goalies['PIM'].sum()
                ppp = df_a_stats_no_goalies['PPP'].sum()
                shp = df_a_stats_no_goalies['SHP'].sum()
                sog = df_a_stats_no_goalies['SOG'].sum()
                hits = df_a_stats_no_goalies['HIT'].sum()
                blocks = df_a_stats_no_goalies['BLK'].sum()
                sog_pct = round(goals / sog,4)  if sog > 0 else 0
                gwg = df_a_stats_no_goalies['GWG'].sum()
                fp = df_a_stats_no_goalies['FP'].sum()
                missed_starts = df_team_a_benched['MISSED_START'].sum()
                benched_fp = df_team_a_benched['FP'].sum()

                # Goalie requirement: fewer than 3 starts → apply penalty values
                # to ensure non-compliant teams lose all goalie categories.
                # Bypass list accounts for weeks where 3 starts was impossible.
                if goalie_starts < 3 and week not in self.goalie_req_bypass.get(int(self.year),[]):
                    sv = 0
                    so = -1
                    ga = 100
                    sa = 1000
                    sv_pct = 0
                    goalie_toi = 0
                    win = -1
                    loss = 10
                    gaa = 50
                    goalie_fail_flag = 1

                else:

                    sv = df_team_a_stats['SV'].sum()
                    so = df_team_a_stats['SO'].sum()
                    ga = df_team_a_stats['GA'].sum()
                    sa = df_team_a_stats['SA'].sum()
                    sv_pct = round(sv / sa,4) if sa > 0 else 0
                    goalie_toi = df_team_a_stats['GOALIE_TOI'].sum()
                    win = df_team_a_stats['W'].sum()
                    loss = df_team_a_stats['L'].sum()
                    gaa = round(ga / goalie_toi * 60,3) if goalie_toi > 0 else 0
                    goalie_fail_flag = 0

                df_matchup_results.loc[len(df_matchup_results)] = (self.year,primary_month, week, matchup, team_a_name, team_a_key,gm_name,
                                                                   team_a_wlt_result, team_a_points,game_days, goals, assists,
                                                                   points, plus_minus, pim, ppp, shp, sog, sog_pct,
                                                                   gwg, hits, blocks, sv, so, ga, sa, sv_pct,
                                                                   goalie_toi, win, loss, gaa, skater_starts,benched_starts,
                                                                   goalie_starts, goalie_fail_flag,fp,benched_fp,missed_starts)

                os.makedirs(f'{self.current_directory}/debug/', exist_ok=True)
                clean_a_name = re.sub(r'[^a-zA-Z0-9]', '', team_a_name)
                df_team_a_stats.to_csv(f'{self.current_directory}/debug/{self.year}_{clean_a_name}_week_{week}_results_for_debug.csv', index=False)

                # --- Repeat stat aggregation for team B (identical logic) ---
                df_team_b_stats = df_merged[
                    (df_merged['OWNER_TEAM_KEY'] == team_b_key) & (df_merged['WEEK'] == week) &
                    (df_merged['SELECTED_POSITION'] != 'BN') &
                    (df_merged['SELECTED_POSITION'] != 'IR+') &
                    (df_merged['SELECTED_POSITION'] != 'IR')&
                    (df_merged['SELECTED_POSITION'] != 'NA')]
                gm_name = df_team_b_stats['OWNER_TEAM_GM'].iloc[0]

                df_team_b_benched = df_merged[
                    (df_merged['OWNER_TEAM_KEY'] == team_b_key) & (df_merged['WEEK'] == week) & (
                    (df_merged['SELECTED_POSITION'] == 'BN') |
                    (df_merged['SELECTED_POSITION'] == 'IR+') |
                    (df_merged['SELECTED_POSITION'] == 'IR') |
                    (df_merged['SELECTED_POSITION'] == 'NA'))]

                benched_starts  = len(
                    df_team_b_benched[(df_team_b_benched['SELECTED_POSITION'] != 'G') & (df_team_b_benched['TOI'] != 0)])

                skater_starts = len(
                    df_team_b_stats[(df_team_b_stats['SELECTED_POSITION'] != 'G') & (df_team_b_stats['TOI'] != 0)])
                goalie_starts = len(
                    df_team_b_stats[(df_team_b_stats['SELECTED_POSITION'] == 'G') & (df_team_b_stats['TOI'] != 0)])
                df_b_stats_no_goalies = df_team_b_stats[df_team_b_stats['DISPLAY_POSITION']!='G']


                goals = df_b_stats_no_goalies['G'].sum()
                assists = df_b_stats_no_goalies['A'].sum()
                points = df_b_stats_no_goalies['P'].sum()
                plus_minus = df_b_stats_no_goalies['+/-'].sum()
                pim = df_b_stats_no_goalies['PIM'].sum()
                ppp = df_b_stats_no_goalies['PPP'].sum()
                shp = df_b_stats_no_goalies['SHP'].sum()
                sog = df_b_stats_no_goalies['SOG'].sum()
                hits = df_b_stats_no_goalies['HIT'].sum()
                blocks = df_b_stats_no_goalies['BLK'].sum()
                sog_pct = round(goals / sog,4) if sog > 0 else 0
                gwg = df_b_stats_no_goalies['GWG'].sum()
                fp = df_b_stats_no_goalies['FP'].sum()
                missed_starts = df_team_b_benched['MISSED_START'].sum()
                benched_fp = df_team_b_benched['FP'].sum()
                if goalie_starts < 3 and week not in self.goalie_req_bypass.get(int(self.year),[]):
                    sv = 0
                    so = -1
                    ga = 100
                    sa = 1000
                    sv_pct = 0
                    goalie_toi = 0
                    win = -1
                    loss = 10
                    gaa = 50
                    goalie_fail_flag = 1
                else:
                    sv = df_team_b_stats['SV'].sum()
                    so = df_team_b_stats['SO'].sum()
                    ga = df_team_b_stats['GA'].sum()
                    sa = df_team_b_stats['SA'].sum()
                    sv_pct = round(sv / sa,4)  if sa > 0 else 0
                    goalie_toi = df_team_b_stats['GOALIE_TOI'].sum()
                    win = df_team_b_stats['W'].sum()
                    loss = df_team_b_stats['L'].sum()
                    gaa = round(ga / goalie_toi * 60,3) if goalie_toi > 0 else 0
                    goalie_fail_flag = 0

                clean_b_name = re.sub(r'[^a-zA-Z0-9]', '', team_b_name)
                df_team_b_stats.to_csv(f'{self.current_directory}/debug/{self.year}_{clean_b_name}_week_{week}_results_for_debug.csv', index=False)

                df_matchup_results.loc[len(df_matchup_results)] = (self.year, primary_month, week, matchup, team_b_name, team_b_key,gm_name,
                                                                   team_b_wlt_result, team_b_points, game_days,goals, assists,
                                                                   points, plus_minus, pim, ppp, shp, sog, sog_pct,
                                                                   gwg, hits, blocks, sv, so, ga, sa, sv_pct,
                                                                   goalie_toi, win, loss, gaa, skater_starts,benched_starts,
                                                                   goalie_starts, goalie_fail_flag,fp,benched_fp,missed_starts)

                # --- Category-by-category scoring ---
                # Tally how many scoring categories each team wins this matchup.
                # GAA and L are "lower is better"; all other categories are higher-is-better.
                team_a_total_score = 0
                team_b_total_score = 0

                for category in cf_year['scoring_categories']:
                    team_a_result = df_matchup_results[category].iloc[0]
                    team_b_result = df_matchup_results[category].iloc[1]

                    if category not in ['GAA', 'L']:
                        # Higher value wins the category
                        if team_a_result > team_b_result:
                            team_a_cat_score = 1
                            team_b_cat_score = 0
                        elif team_b_result > team_a_result:
                            team_b_cat_score = 1
                            team_a_cat_score = 0
                        else:
                            team_a_cat_score = 0
                            team_b_cat_score = 0
                    else:
                        # Lower value wins the category (GAA, Losses)
                        if team_a_result < team_b_result:
                            team_a_cat_score = 1
                            team_b_cat_score = 0
                        elif team_b_result < team_a_result:
                            team_b_cat_score = 1
                            team_a_cat_score = 0
                        else:
                            team_a_cat_score = 0
                            team_b_cat_score = 0
                    team_a_total_score += team_a_cat_score
                    team_b_total_score += team_b_cat_score

                df_matchup_results.loc[0, 'CALCULATED_SCORE'] = int(team_a_total_score)
                df_matchup_results.loc[1, 'CALCULATED_SCORE'] = int(team_b_total_score)

                # Derive CALCULATED_RESULT from the category tally
                if team_a_total_score > team_b_total_score:
                    df_matchup_results.loc[0, 'CALCULATED_RESULT'] = 'WIN'
                    df_matchup_results.loc[1, 'CALCULATED_RESULT'] = 'LOSS'
                elif team_a_total_score < team_b_total_score:
                    df_matchup_results.loc[0, 'CALCULATED_RESULT'] = 'LOSS'
                    df_matchup_results.loc[1, 'CALCULATED_RESULT'] = 'WIN'
                else:
                    df_matchup_results.loc[0, 'CALCULATED_RESULT'] = 'TIE'
                    df_matchup_results.loc[1, 'CALCULATED_RESULT'] = 'TIE'

                # Convert Yahoo's binary win/loss into a 2-point / 0-point scale
                # to make the YAHOO_POINTS column comparable to CALCULATED_POINTS
                if team_a_wlt_result == 'WIN':
                    df_matchup_results.loc[0, 'YAHOO_POINTS'] = 2
                    df_matchup_results.loc[1, 'YAHOO_POINTS'] = 0
                elif team_b_wlt_result == 'WIN':
                    df_matchup_results.loc[0, 'YAHOO_POINTS'] = 0
                    df_matchup_results.loc[1, 'YAHOO_POINTS'] = 2
                else:
                    df_matchup_results.loc[0, 'YAHOO_POINTS'] = 1
                    df_matchup_results.loc[1, 'YAHOO_POINTS'] = 1

                if df_matchup_results.loc[0, 'CALCULATED_RESULT'] == 'WIN':
                    df_matchup_results.loc[0, 'CALCULATED_POINTS'] = 2
                    df_matchup_results.loc[1, 'CALCULATED_POINTS'] = 0
                elif df_matchup_results.loc[1, 'CALCULATED_RESULT'] == 'WIN':
                    df_matchup_results.loc[0, 'CALCULATED_POINTS'] = 0
                    df_matchup_results.loc[1, 'CALCULATED_POINTS'] = 2
                else:
                    df_matchup_results.loc[0, 'CALCULATED_POINTS'] = 1
                    df_matchup_results.loc[1, 'CALCULATED_POINTS'] = 1


                # --- Data quality cross-validation ---
                # Compare our calculated result against Yahoo's official result.
                # Discrepancies can arise from missing HR data, late-reported stats,
                # or roster edge cases. The control_file stores known explanations.
                df_matchup_results.loc[0, 'SCORE_CHECK'] = df_matchup_results.loc[0, 'CALCULATED_SCORE'] == \
                                                           df_matchup_results.loc[0, 'SCORE']

                df_matchup_results.loc[1, 'SCORE_CHECK'] = df_matchup_results.loc[1, 'CALCULATED_SCORE'] == \
                                                           df_matchup_results.loc[1, 'SCORE']
                df_matchup_results.loc[0, 'RESULT_CHECK'] = df_matchup_results.loc[0, 'CALCULATED_RESULT'] == \
                                                            df_matchup_results.loc[0, 'RESULT']
                df_matchup_results.loc[1, 'RESULT_CHECK'] = df_matchup_results.loc[1, 'CALCULATED_RESULT'] == \
                                                            df_matchup_results.loc[1, 'RESULT']

                for i in [0,1]:
                    if (df_matchup_results.loc[i, 'RESULT_CHECK'] == False) :
                        gm_name = self.df_league_teams[(self.df_league_teams['team_key']== df_matchup_results.loc[i, 'TEAM_KEY'])&(self.df_league_teams['season']==int(self.year))]['gm_name'].values[0]
                        try:
                            # Look up the explanation and resolution status from the control file
                            metadata = self.control_file['matchup_metadata'][f'Y{str(self.year)}-W{str(week)}-M{str(matchup)}'][gm_name]['text']
                            status = self.control_file['matchup_metadata'][f'Y{str(int(self.year))}-W{str(int(week))}-M{str(int(matchup))}'][gm_name]['status']
                        except:
                            metadata = 'Need to analyze and update metadata json!'
                            status = 'TBD'
                        print(f'Found a score discrepancy for {self.year} Week {week} Matchup {matchup} - {metadata} - {gm_name} was {status}')

                    else:
                        metadata = ''
                        status = ''
                    df_matchup_results.loc[i,'METADATA'] = metadata
                    df_matchup_results.loc[i,'STATUS'] = status


                df_week_results = pd.concat([df_week_results, df_matchup_results],
                                                    ignore_index=True)

            # --- Weekly Quality Score (WEEK_QS) ---
            # For each scoring category, rank all teams by percentile within the week.
            # Average the percentile ranks across all categories and scale to 0-10.
            # Normalizing by category count means QS is comparable across seasons
            # with different numbers of scoring categories.
            df_week_results['WEEK_QS'] = 0
            categories_count = 0
            for category in cf_year['scoring_categories']:
                if category not in ['L','GAA']:
                    df_week_results['WEEK_QS'] +=  df_week_results[category].rank(pct=True)
                else:
                    # Lower-is-better categories: invert the percentile rank
                    df_week_results['WEEK_QS'] +=  df_week_results[category].rank(pct=True,ascending=False)
                categories_count+=1
            df_week_results['WEEK_QS']  = round(df_week_results['WEEK_QS']/categories_count*10,3)
            df_week_results['WEEK_QS_RANK']=df_week_results['WEEK_QS'].rank()
            df_year_matchup_results = pd.concat([df_year_matchup_results, df_week_results],
                                                ignore_index=True)

        # --- Yearly Quality Score (YEAR_QS) ---
        # Same as WEEK_QS but ranked across the entire season rather than per week.
        df_year_matchup_results['YEAR_QS'] = 0
        categories_count = 0
        for category in cf_year['scoring_categories']:
            if category not in ['L', 'GAA']:
                df_year_matchup_results['YEAR_QS'] += df_year_matchup_results[category].rank(pct=True)
            else:
                df_year_matchup_results['YEAR_QS'] += df_year_matchup_results[category].rank(pct=True, ascending=False)
            categories_count += 1
        df_year_matchup_results['YEAR_QS'] = round(df_year_matchup_results['YEAR_QS'] / categories_count * 10,3)

        os.makedirs(f'{self.current_directory}/analytics_matchups/', exist_ok=True)
        df_year_matchup_results.to_csv(f'analytics_matchups/{self.year}_matchup_data.csv', index=False)

    def power_ranking_analytics(self):
        """
        Compute monthly power rankings for self.year from matchup data.

        Groups matchup results by month and GM, aggregating cumulative stats
        (Yahoo points, FP, QS, missed starts, goalie failures) into a POWER_SCORE.

        The POWER_SCORE formula (subject to tuning):
            RANK_YAHOO_POINTS
            + 0.5 * (RANK_TOTAL_FP + RANK_AVG_FP + RANK_TOTAL_QS + RANK_AVG_QS)
            - 0.5 * RANK_MISSED_STARTS
            - RANK_GOALIE_FAILURES
            - RANK_BENCHED_FP

        Higher POWER_SCORE → better rank. All rank inputs are percentile-based
        (0.0–1.0) so the composite score is dimensionless and season-agnostic.

        Side effects:
            - Creates analytics_power_ranks/ directory if absent.
            - Writes analytics_power_ranks/<year>_power_ranks.csv.
        """
        print(f'RUNNING POWER RANKING ANALYTICS FOR YEAR {self.year}')
        cf_year = self.control_file['Years'][str(self.year)]
        baseline_col_list = ['SEASON','MONTH','GM','YAHOO_POINTS','SKATER_STARTS','BENCHED_STARTS','GOALIE_STARTS','MISSED_STARTS','GOALIE_FAILURES','TOTAL_FP','AVG_FP','TOTAL_QS','AVG_QS','BENCHED_FP','POWER_SCORE','POWER_RANK']
        df_power_rankings = pd.DataFrame(columns=baseline_col_list)
        df_matchups = pd.read_csv(f'analytics_matchups/{self.year}_matchup_data.csv', low_memory=False)

        for month in df_matchups['PRIMARY_MONTH'].unique():
            df_month = df_matchups[df_matchups['PRIMARY_MONTH']==month]
            df_power_rankings_month = pd.DataFrame(columns=baseline_col_list)

            for gm in df_month['GM_NAME'].unique():
                df_month_gm = df_month[df_month['GM_NAME']==gm]

                # Aggregate this GM's monthly totals
                missed_starts = df_month_gm['MISSED_START'].sum()
                benched_fp = df_month_gm['BENCH_FP'].sum()
                failed_goalies = df_month_gm['FAILED_GOALIE_REQUIREMENT'].sum()
                yahoo_points = df_month_gm['YAHOO_POINTS'].sum()
                goalie_starts = df_month_gm['GOALIE_STARTS'].sum()
                skater_starts = df_month_gm['SKATER_STARTS'].sum()
                bench_starts = df_month_gm['BENCH_STARTS'].sum()

                fp = df_month_gm['FP'].sum()
                quality_total = df_month_gm['WEEK_QS'].sum()
                fp_avg = df_month_gm['FP'].mean()
                quality_avg = df_month_gm['WEEK_QS'].mean()

                # Placeholders — filled in after all GMs are processed
                power_score = 0
                power_rank = 0

                df_power_rankings_month.loc[len(df_power_rankings_month)]=(self.year,month,gm,yahoo_points,skater_starts,bench_starts,goalie_starts,missed_starts,failed_goalies,fp,fp_avg,quality_total,quality_avg,benched_fp,power_score,power_rank)


            # Rank each metric by percentile within the month before compositing
            categories_count = 0
            for category in ['YAHOO_POINTS','SKATER_STARTS','BENCHED_STARTS','GOALIE_STARTS','MISSED_STARTS','GOALIE_FAILURES','TOTAL_FP','AVG_FP','TOTAL_QS','AVG_QS','BENCHED_FP']:
                df_power_rankings_month[f'RANK_{category}'] =  df_power_rankings_month[category].rank(pct=True)
                categories_count+=1

            '''
            Spitballing some formula ideas.
            60% on QS and FP (mean and totals)


            0.25*RANK_TOTAL_QS + 0.25*RANK_TOTAL_FP +

            '''
            # Composite power score: win-based contribution + FP + QS quality,
            # penalized for managerial mistakes (missed starts, goalie failures, benched FP)
            df_power_rankings_month['POWER_SCORE'] = (df_power_rankings_month['RANK_YAHOO_POINTS'] +
                                                      0.5*df_power_rankings_month['RANK_TOTAL_FP'] +
                                                      0.5*df_power_rankings_month['RANK_AVG_FP'] +
                                                      0.5 * df_power_rankings_month['RANK_TOTAL_QS'] +
                                                      0.5 * df_power_rankings_month['RANK_AVG_QS'] -
                                                      0.5*df_power_rankings_month['RANK_MISSED_STARTS'] -
                                                      df_power_rankings_month['RANK_GOALIE_FAILURES'] -
                                                      df_power_rankings_month['RANK_BENCHED_FP'])
            # Rank 1 = best (ascending=False)
            df_power_rankings_month['POWER_RANK'] = df_power_rankings_month['POWER_SCORE'].rank(pct=False,ascending=False,method='first')
            df_power_rankings = pd.concat([df_power_rankings,df_power_rankings_month])

        os.makedirs(f'{self.current_directory}/analytics_power_ranks/', exist_ok=True)
        df_power_rankings.to_csv(f'analytics_power_ranks/{self.year}_power_ranks.csv', index=False)

    def loyalty_analytics(self):
        """
        Compute a loyalty score for each GM in self.year.

        Loyalty score measures what fraction of a GM's drafted and kept players
        they retained on their roster for the full season. A score of 1.0 (100%)
        means they never dropped anyone they drafted or designated as a keeper.

        Separately tracks keeper loyalty and draftee loyalty, then combines them
        into one weighted fraction:
            loyalty_score = (draft_loyalty + keeper_loyalty) / (draftee_count + keeper_count)

        Args:
            None — reads from league_transactions/<year>_transactions.csv and
            league_drafts/<year>_league_draft.csv.

        Side effects:
            - Creates analytics_loyalty/ directory if absent.
            - Writes analytics_loyalty/<year>_loyalty_data.csv.
        """
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        draft_df = pd.read_csv(f'league_drafts/{self.year}_league_draft.csv')

        df_loyalty = pd.DataFrame(columns=['SEASON','OWNER_TEAM_GM','LOYALTY_SCORE','KEEPERS_LEFT','KEEPER_COUNT','DRAFTEES_LEFT','DRAFTEE_COUNT'])

        for gm_key in draft_df['DESTINATION_KEY'].unique():
            gm_draft = draft_df[(draft_df['DESTINATION_KEY']==gm_key)]
            gm_name = gm_draft['GM_NAME_DESTINATION'].values[0]
            count_keepers = len(gm_draft[gm_draft['KEEPER']=='KEEPER'])
            keeper_loyalty = count_keepers
            count_draftees = len(gm_draft[gm_draft['KEEPER']=='NO'])
            draft_loyalty = count_draftees

            # Start at 100% and subtract one for each player dropped
            loyalty_score = (draft_loyalty+keeper_loyalty)/(count_keepers+count_draftees)

            for keeper in gm_draft[(gm_draft['KEEPER']=='KEEPER')]['NAME']:
                # A drop transaction from this team for this player means they didn't keep them
                if len(trans_df[(trans_df['NAME']==keeper)&(trans_df['SOURCE_KEY']==gm_key)&(trans_df['TRANSACTION_TYPE']=='drop')])>0:
                    keeper_loyalty = keeper_loyalty-1
                    loyalty_score = (draft_loyalty + keeper_loyalty) / (count_keepers + count_draftees)


            for draftee in gm_draft[(gm_draft['KEEPER']=='NO')]['NAME']:
                if len(trans_df[(trans_df['NAME']==draftee)&(trans_df['SOURCE_KEY']==gm_key)&(trans_df['TRANSACTION_TYPE']=='drop')])>0:
                    draft_loyalty = draft_loyalty-1
                    loyalty_score = (draft_loyalty + keeper_loyalty) / (count_keepers + count_draftees)

            df_loyalty.loc[len(df_loyalty)]=(self.year,gm_name,loyalty_score,keeper_loyalty,count_keepers,draft_loyalty,count_draftees)

        os.makedirs(f'{self.current_directory}/analytics_loyalty/', exist_ok=True)
        df_loyalty.to_csv(f'analytics_loyalty/{self.year}_loyalty_data.csv', index=False)

    def keeper_analytics(self):
        """
        Evaluate keeper ROI for each GM in self.year.

        For each keeper designation, computes the FP and games played (GP) the
        player produced while on that GM's roster. If the keeper was subsequently
        dropped, only counts production up to the first drop date.

        Also records all-season FP/GP for the same player (regardless of ownership)
        as a baseline for comparing keeper value to the player's actual potential.

        Side effects:
            - Creates analytics_keepers/ directory if absent.
            - Writes analytics_keepers/<year>_keeper_stats.csv.
        """
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        draft_df = pd.read_csv(f'league_drafts/{self.year}_league_draft.csv')
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        keeper_df_output = pd.DataFrame(columns=['SEASON','GM','KEEPER','FP','GP','DROPPED','ALL_FP','ALL_GP'])

        for gm in draft_df['GM_NAME_DESTINATION'].unique():
            df_keepers = draft_df[(draft_df['GM_NAME_DESTINATION']==gm)&(draft_df['KEEPER']=='KEEPER')]
            for keeper in df_keepers['NAME'].unique():
                drop_data = trans_df[(trans_df['NAME']==keeper)&(trans_df['TRANSACTION_TYPE']=='drop')&(trans_df['GM_NAME_SOURCE']==gm)]
                # All-season stats for this player regardless of who owned them
                all_fp = df_stats[(df_stats['NAME']==keeper)]['FP'].sum()
                all_gp = df_stats[(df_stats['NAME'] == keeper)]['GAME_PLAYED'].sum()
                if len(drop_data)>0:
                    drop_status = True
                    drop_date = drop_data['TRANSACTION_DATE'].iloc[0]
                    # Only count production before the drop date
                    keeper_fp = df_stats[(df_stats['NAME']==keeper)&(df_stats['DATE']<drop_date)]['FP'].sum()
                    keeper_gp = df_stats[(df_stats['NAME']==keeper)&(df_stats['DATE']<drop_date)]['GAME_PLAYED'].sum()
                else:
                    drop_status = False
                    # Kept all season — use full totals
                    keeper_fp = all_fp
                    keeper_gp = all_gp

                keeper_df_output.loc[len(keeper_df_output)] = (self.year,gm,keeper,keeper_fp,keeper_gp,drop_status,all_fp,all_gp)
        os.makedirs(f'{self.current_directory}/analytics_keepers/', exist_ok=True)
        keeper_df_output.to_csv(f'analytics_keepers/{self.year}_keeper_stats.csv', index=False)

    def draft_analytics(self):
        """
        Evaluate draft pick ROI for each GM in self.year.

        For each non-keeper draft pick, computes the FP and GP the player produced
        while on that GM's roster. If dropped, only counts production up to the
        first drop date (using the earliest drop if they were dropped multiple times).

        Also records the draft round and pick number for positional value analysis.

        Side effects:
            - Creates analytics_draft/ directory if absent.
            - Writes analytics_draft/<year>_draft_stats.csv.
        """
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        draft_df = pd.read_csv(f'league_drafts/{self.year}_league_draft.csv')
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_ALL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        draft_df_output = pd.DataFrame(columns=['SEASON','GM','DRAFTEE','PICK_ROUND','PICK_NUMBER','FP','GP','DROPPED','ALL_FP','ALL_GP'])

        for gm in draft_df['GM_NAME_DESTINATION'].unique():
            # Only analyze non-keeper draft picks
            df_draftees = draft_df[(draft_df['GM_NAME_DESTINATION']==gm)&(draft_df['KEEPER']=='NO')]
            for draftee in df_draftees['NAME'].unique():
                draft_round = df_draftees[df_draftees['NAME']==draftee]['DRAFT_ROUND'].iloc[0]
                draft_pick = df_draftees[df_draftees['NAME']==draftee]['DRAFT_PICK'].iloc[0]

                drop_data = trans_df[(trans_df['NAME']==draftee)&(trans_df['TRANSACTION_TYPE']=='drop')&(trans_df['GM_NAME_SOURCE']==gm)]
                all_fp = df_stats[(df_stats['NAME']==draftee)]['FP'].sum()
                all_gp = df_stats[(df_stats['NAME'] == draftee)]['GAME_PLAYED'].sum()
                if len(drop_data)>0:
                    # Sort ascending to get the first (earliest) drop date
                    drop_data.sort_values('TRANSACTION_DATE',ascending=True,inplace=True)
                    drop_status = True
                    drop_date = drop_data['TRANSACTION_DATE'].iloc[0]
                    draft_fp = df_stats[(df_stats['NAME']==draftee)&(df_stats['DATE']<drop_date)]['FP'].sum()
                    draft_gp = df_stats[(df_stats['NAME']==draftee)&(df_stats['DATE']<drop_date)]['GAME_PLAYED'].sum()
                else:
                    drop_status = False
                    draft_fp = all_fp
                    draft_gp = all_gp

                draft_df_output.loc[len(draft_df_output)] = (self.year,gm,draftee,draft_round,draft_pick,draft_fp,draft_gp,drop_status,all_fp,all_gp)
        os.makedirs(f'{self.current_directory}/analytics_draft/', exist_ok=True)
        draft_df_output.to_csv(f'analytics_draft/{self.year}_draft_stats.csv', index=False)


    def FAAB_analytics(self):
        """
        Evaluate Free Agent Acquisition Budget (FAAB) spending efficiency for self.year.

        For each FAAB transaction (add with a non-null bid), computes the FP and GP
        the acquired player produced while on that GM's roster (up to the first drop
        date if the player was later released). Useful for measuring how much value
        each dollar of FAAB spend returned.

        If no FAAB data exists for the year (e.g. pre-FAAB seasons), writes a
        single placeholder row so downstream tooling doesn't fail on a missing file.

        Side effects:
            - Creates analytics_faab/ directory if absent.
            - Writes analytics_faab/<year>_faab_stats.csv.
        """
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        faab_df_output = pd.DataFrame(columns=['SEASON','GM','PLAYER','FAAB_COST','FP','GP','DROPPED'])

        for gm in trans_df['GM_NAME_DESTINATION'].unique():
            # Only transactions with a non-null FAAB bid represent paid acquisitions
            df_faabers = trans_df[(trans_df['GM_NAME_DESTINATION']==gm)&(~trans_df['FAAB_BID'].isna())]
            for faaber in df_faabers['NAME'].unique():
                faab_cost = df_faabers[df_faabers['NAME']==faaber]['FAAB_BID'].iloc[0]

                drop_data = trans_df[(trans_df['NAME']==faaber)&(trans_df['TRANSACTION_TYPE']=='drop')&(trans_df['GM_NAME_SOURCE']==gm)]
                all_fp = df_stats[(df_stats['NAME']==faaber)]['FP'].sum()
                all_gp = df_stats[(df_stats['NAME'] == faaber)]['GAME_PLAYED'].sum()
                if len(drop_data)>0:
                    drop_data.sort_values('TRANSACTION_DATE',ascending=True,inplace=True)
                    drop_status = True
                    drop_date = drop_data['TRANSACTION_DATE'].iloc[0]
                    faab_fp = df_stats[(df_stats['NAME']==faaber)&(df_stats['DATE']<drop_date)]['FP'].sum()
                    faab_gp = df_stats[(df_stats['NAME']==faaber)&(df_stats['DATE']<drop_date)]['GAME_PLAYED'].sum()
                else:
                    drop_status = False
                    faab_fp = all_fp
                    faab_gp = all_gp

                faab_df_output.loc[len(faab_df_output)] = (self.year,gm,faaber,faab_cost,faab_fp,faab_gp,drop_status)

        # Write a placeholder row for years without FAAB (e.g. waiver-priority seasons)
        if len(faab_df_output)==0:
            faab_df_output.loc[len(faab_df_output)] = (self.year, 'N/A', 'N/A', 0, 0, 0, False)

        os.makedirs(f'{self.current_directory}/analytics_faab/', exist_ok=True)
        faab_df_output.to_csv(f'analytics_faab/{self.year}_faab_stats.csv', index=False)

    def streamer_analytics(self):
        """
        Evaluate the performance of in-week player pickups (streamers) for self.year.

        A "streamer" is any player added via a transaction during the season.
        For each add, captures the FP and GP produced by that player during
        the week they were picked up, along with ownership percentage stats
        (which proxy for how much buzz the player generated that week).

        Side effects:
            - Creates analytics_streamers/ directory if absent.
            - Writes analytics_streamers/<year>_steamers_stats.csv.
        """
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        # Only add transactions are streamers — drops and trades are excluded
        trans_df = trans_df[trans_df['TRANSACTION_TYPE']=='add']
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        streamer_df_output = pd.DataFrame(columns=['SEASON','WEEK','GM','PLAYER','FP','GP','HIGH_OWNERSHIP_CHANGE','LOW_OWNERSHIP_CHANGE','MEAN_OWNERSHIP_CHANGE','HIGH_OWNERSHIP','LOW_OWNERSHIP','MEAN_OWNERSHIP'])

        for gm in trans_df['GM_NAME_DESTINATION'].unique():
            for week in trans_df['WEEK'].unique():
                trans_gm_week_df = trans_df[(trans_df['WEEK']==week)&(trans_df['GM_NAME_DESTINATION']==gm)]
                for name in trans_gm_week_df['NAME'].unique():
                    # Stats filtered to the week of pickup and the GM who picked them up
                    fp = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['FP'].sum()
                    gp = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['GAME_PLAYED'].sum()
                    # Ownership delta captures whether the pickup was trending up (high delta = buzz)
                    avg_percent_own_delta = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['PERCENT_OWNED_DELTA'].mean()
                    max_percent_own_delta = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['PERCENT_OWNED_DELTA'].max()
                    min_percent_own_delta = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['PERCENT_OWNED_DELTA'].min()
                    avg_percent_own = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['PERCENT_OWNED'].mean()
                    max_percent_own = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['PERCENT_OWNED'].max()
                    min_percent_own = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['PERCENT_OWNED'].min()

                    streamer_df_output.loc[len(streamer_df_output)] = (self.year,week,gm,name,fp,gp,max_percent_own_delta,min_percent_own_delta,avg_percent_own_delta,max_percent_own,min_percent_own,avg_percent_own)
        os.makedirs(f'{self.current_directory}/analytics_streamers/', exist_ok=True)
        streamer_df_output.to_csv(f'analytics_streamers/{self.year}_steamers_stats.csv', index=False)

    def engagement_analytics(self):
        """
        Track weekly GM engagement signals for self.year.

        Engagement is measured across three dimensions per week per GM:
            - ADDS: number of player pickups made (max 5 per week)
            - MISSED_STARTS: players benched who could have started
            - FAILED_GOALIES: weeks the 3-start goalie minimum was not met

        An aggregated 'ALL' row is appended per GM summarizing the entire season.

        Side effects:
            - Creates analytics_engagement/ directory if absent.
            - Writes analytics_engagement/<year>_engagement_stats.csv.
        """
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        trans_df = trans_df[trans_df['TRANSACTION_TYPE'] == 'add']
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_ALL_DATA.csv', low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])
        df_matchup_data = pd.read_csv(f'analytics_matchups/{self.year}_matchup_data.csv', low_memory=False)

        engagement_df_output = pd.DataFrame(columns=['SEASON', 'WEEK', 'GM', 'MAX_POSSIBLE_ADDS', 'ADDS', 'MISSED_STARTS', 'FAILED_GOALIES'])
        # Maximum possible adds in the season = num weeks × 5 moves/week
        max_adds = trans_df['WEEK'].max()*5
        for gm in df_matchup_data['GM_NAME'].unique():
            engagement_df_output_gm = pd.DataFrame(columns=engagement_df_output.columns)

            for week in trans_df['WEEK'].unique():
                trans_gm_week_df = trans_df[(trans_df['WEEK'] == week) & (trans_df['GM_NAME_DESTINATION'] == gm)]
                adds_week = len(trans_gm_week_df)

                # Pull missed starts and goalie failures directly from the matchup analytics output
                matchup_gm_week = df_matchup_data[(df_matchup_data['WEEK'] == week) & (df_matchup_data['GM_NAME'] == gm)]
                if len(matchup_gm_week)>0:
                    MISSED_START = matchup_gm_week['MISSED_START'].iloc[0]
                    FAILED_GOALIE_REQUIREMENT = matchup_gm_week['FAILED_GOALIE_REQUIREMENT'].iloc[0]
                    engagement_df_output_gm.loc[len(engagement_df_output_gm)] = (self.year,week,gm,5,adds_week,MISSED_START,FAILED_GOALIE_REQUIREMENT)
                else:
                    # No matchup data: team was eliminated or week 0 (post-draft, pre-season)
                    MISSED_START = 0
                    FAILED_GOALIE_REQUIREMENT = 0
                    engagement_df_output_gm.loc[len(engagement_df_output_gm)] = (self.year,week,gm,5,adds_week,MISSED_START,FAILED_GOALIE_REQUIREMENT)

            # Season-total summary row for this GM
            max_adds_gm = engagement_df_output_gm['ADDS'].sum()
            all_missed_starts = engagement_df_output_gm['MISSED_STARTS'].sum()
            all_missed_goalies = engagement_df_output_gm['FAILED_GOALIES'].sum()
            engagement_df_output_gm.loc[len(engagement_df_output_gm)] = (self.year, 'ALL', gm, max_adds, max_adds_gm,all_missed_starts, all_missed_goalies)
            engagement_df_output = pd.concat([engagement_df_output_gm,engagement_df_output])


        os.makedirs(f'{self.current_directory}/analytics_engagement/', exist_ok=True)
        engagement_df_output.to_csv(f'analytics_engagement/{self.year}_engagement_stats.csv', index=False)

    def hospital_analytics(self):
        """
        Track player injury streaks for self.year.

        Identifies all players with a non-null INJURY_NOTE in the merged stat data,
        then groups consecutive injured days into discrete "trips to the hospital."
        Each trip is characterized by start date, end date, injury description,
        days missed, and which GM owned the player at the time.

        Consecutive days are detected via cumsum on a diff-based day-gap indicator:
        when the gap between two consecutive dates is not exactly 1 day, a new
        trip is started.

        Side effects:
            - Creates analytics_hospital/ directory if absent.
            - Writes analytics_hospital/<year>_hospital_data.csv.
        """
        df_all = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        hurt_df = df_all[(~df_all['INJURY_NOTE'].isna())]
        print(f'Number of hurt players: {len(hurt_df)}')
        df_hospital = pd.DataFrame(columns=['SEASON','PATIENT_REG_ID','PATIENT','START','END','INJURY','DAYS_AWAY', 'OWNER_TEAM_GM'])
        for patient in hurt_df['NAME'].unique():
            player_df = hurt_df[hurt_df['NAME']==patient]

            total_days = len(player_df)
            player_df['DATE'] = pd.to_datetime(player_df['DATE'])
            player_df.sort_values('DATE',inplace=True)

            # Group consecutive dates: a gap of more than 1 day signals a new injury trip.
            # cumsum on the boolean creates a monotonically increasing group ID.
            player_df['DATE_GROUP'] = (player_df['DATE'].diff().dt.days.ne(1)).cumsum()

            for group_count in player_df['DATE_GROUP'].unique():
                player_group_df = player_df[player_df['DATE_GROUP']==group_count]
                group_start = str(player_group_df['DATE'].min())
                group_end = str(player_group_df['DATE'].max())
                # Use the first day's injury note as representative for the whole trip
                injury = player_group_df['INJURY_NOTE'].iloc[0]
                OWNER_TEAM_GM = player_group_df['OWNER_TEAM_GM'].iloc[0]
                time_in_group = len(player_group_df)
                # Unique trip ID: start_date-player_name
                patient_reg_id = group_start.split(' ')[0]+'-'+patient
                df_hospital.loc[len(df_hospital)] = (self.year,patient_reg_id,patient,group_start,group_end,injury,time_in_group, OWNER_TEAM_GM)

        os.makedirs(f'{self.current_directory}/analytics_hospital/', exist_ok=True)
        df_hospital.to_csv(f'analytics_hospital/{self.year}_hospital_data.csv', index=False)


    def create_csv_databases(self):
        """
        Regenerate all aggregated multi-year flat-file databases in csv_databases/.

        Reads every per-year CSV from each analytics output folder and concatenates
        them into single flat files. These files are the data source for Power BI
        dashboards and the RAG pipeline.

        Also rebuilds the DuckDB database (sql_tables/fantasy_database.duckdb) with
        three tables: all_roster_data, all_transaction_data, all_draft_data.

        Databases created:
            TRANSACTIONS.csv        — all transactions + drafts, sorted by date
            LEAGUE_TEAMS.csv        — all team/GM records across all seasons
            LEAGUE_WEEKS_AND_DATES.csv
            LEAGUE_STAT_CATEGORIES.csv
            LEAGUE_STANDINGS.csv
            LEAGUE_SCOREBOARDS.csv
            LEAGUE_METADATA.csv
            DRAFT_ANALYTICS.csv
            KEEPER_ANALYTICS.csv
            FAAB_ANALYTICS.csv
            STREAMERS_ANALYTICS.csv
            ENGAGEMENT_ANALYTICS.csv
            POWER_RANK_ANALYTICS.csv
            MATCHUPS.csv            — includes ALL_TIME_QS computed across all seasons
            ALL_DATA.csv            — full merged roster+stat data
            ALL_DATA_TRUNCATED.csv  — same with internal/advanced columns dropped

        Side effects:
            - Creates csv_databases/ and sql_tables/ directories if absent.
            - Overwrites all files listed above.
            - Closes the DuckDB connection when done.
        """
        print('Running the csv "database" generator...')
        os.makedirs(f'{self.current_directory}/csv_databases/', exist_ok=True)

        print(f'[{time.ctime()}] Creating/updating TRANSACTIONS/DRAFTS table {self.year}')
        # all transaction / draft data ============================================================
        df_all_trans =pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_transactions/**/*.csv',recursive=True):
            df_ = pd.read_csv(file)
            df_all_trans = pd.concat([df_all_trans,df_])
        for draft_file in glob.glob(f'{self.current_directory}/league_drafts/**/*.csv',recursive=True):
            df_ = pd.read_csv(draft_file)
            df_all_trans = pd.concat([df_all_trans,df_])
        df_all_trans.sort_values('TRANSACTION_DATE',inplace=True)
        df_all_trans.to_csv(f'{self.current_directory}/csv_databases/TRANSACTIONS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating TEAMS table {self.year}')
        # league teams =====================================================================
        df_league_teams=pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_teams/*.csv'):
            df_ = pd.read_csv(file)
            df_league_teams = pd.concat([df_league_teams,df_])
        df_league_teams.to_csv(f'{self.current_directory}/csv_databases/LEAGUE_TEAMS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating WEEKS AND DATES table {self.year}')
        # league weeks and dates =====================================================================
        df_dates = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_weeks_and_dates/*.csv'):
            df_ = pd.read_csv(file)
            df_dates = pd.concat([df_dates, df_])
        df_dates.to_csv(f'{self.current_directory}/csv_databases/LEAGUE_WEEKS_AND_DATES.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating STAT CATS table {self.year}')
        # league stat cats =====================================================================
        df_stat_cats = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_stat_categories/*.csv'):
            df_ = pd.read_csv(file)
            df_stat_cats = pd.concat([df_stat_cats, df_])
        df_stat_cats.to_csv(f'{self.current_directory}/csv_databases/LEAGUE_STAT_CATEGORIES.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating STANDINGS table {self.year}')
        # league standings =====================================================================
        df_standings = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_standings/*.csv'):
            df_ = pd.read_csv(file)
            df_standings = pd.concat([df_standings, df_])
        df_standings.to_csv(f'{self.current_directory}/csv_databases/LEAGUE_STANDINGS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating SCOREBOARD table {self.year}')
        # league scoreboards =====================================================================
        df_scoreboards = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_scoreboards_by_week/*.csv'):
            df_ = pd.read_csv(file)
            df_scoreboards = pd.concat([df_scoreboards, df_])
        df_scoreboards.to_csv(f'{self.current_directory}/csv_databases/LEAGUE_SCOREBOARDS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating LEAGUE METADATA table {self.year}')
        # league metadata =====================================================================
        df_metadata = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/league_metadata/*.csv'):
            df_ = pd.read_csv(file)
            df_metadata = pd.concat([df_metadata, df_])
        df_metadata.to_csv(f'{self.current_directory}/csv_databases/LEAGUE_METADATA.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating DRAFT ANALYTICS table {self.year}')
        # league metadata =====================================================================
        df_drf = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_draft/*.csv'):
            df_ = pd.read_csv(file)
            df_drf = pd.concat([df_drf, df_])
        df_drf.to_csv(f'{self.current_directory}/csv_databases/DRAFT_ANALYTICS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating KEEPER ANALYTICS table {self.year}')
        # league metadata =====================================================================
        df_kp = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_keepers/*.csv'):
            df_ = pd.read_csv(file)
            df_kp = pd.concat([df_kp, df_])
        df_kp.to_csv(f'{self.current_directory}/csv_databases/KEEPER_ANALYTICS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating FAAB ANALYTICS table {self.year}')
        # league metadata =====================================================================
        df_zz = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_faab/*.csv'):
            df_ = pd.read_csv(file)
            df_zz = pd.concat([df_zz, df_])
        df_zz.to_csv(f'{self.current_directory}/csv_databases/FAAB_ANALYTICS.csv', index=False)


        print(f'[{time.ctime()}] Creating/updating streamers ANALYTICS table {self.year}')
        # league metadata =====================================================================
        df_zz = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_streamers/*.csv'):
            df_ = pd.read_csv(file)
            df_zz = pd.concat([df_zz, df_])
        df_zz.to_csv(f'{self.current_directory}/csv_databases/STREAMERS_ANALYTICS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating streamers ENGAGEMENT table {self.year}')
        # league metadata =====================================================================
        df_zz = pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_engagement/*.csv'):
            df_ = pd.read_csv(file)
            df_zz = pd.concat([df_zz, df_])
        df_zz.to_csv(f'{self.current_directory}/csv_databases/ENGAGEMENT_ANALYTICS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating POWER RANKS table {self.year}')
        # matchup analytics =====================================================================
        df_zz=pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_power_ranks/*.csv'):
            df_ = pd.read_csv(file)
            df_zz = pd.concat([df_zz,df_])
        df_zz.to_csv(f'{self.current_directory}/csv_databases/POWER_RANK_ANALYTICS.csv', index=False)


        print(f'[{time.ctime()}] Creating/updating MATCHUP table {self.year}')
        # matchup analytics =====================================================================
        df_matchups=pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/analytics_matchups/*.csv'):
            df_ = pd.read_csv(file)
            df_matchups = pd.concat([df_matchups,df_])

        # ALL_TIME_QS: same percentile-rank formula as YEAR_QS but applied across
        # ALL seasons simultaneously, enabling cross-season performance comparisons.
        df_matchups['ALL_TIME_QS'] = 0
        categories_count = 0
        for category in  self.control_file['Years'][str(self.year)]['scoring_categories']:
            if category not in ['L', 'GAA']:
                df_matchups['ALL_TIME_QS'] += df_matchups[category].rank(pct=True)
            else:
                df_matchups['ALL_TIME_QS'] += df_matchups[category].rank(pct=True, ascending=False)
            categories_count += 1
        df_matchups['ALL_TIME_QS'] = round(df_matchups['ALL_TIME_QS'] / categories_count * 10,3)
        df_matchups.to_csv(f'{self.current_directory}/csv_databases/MATCHUPS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating ROSTERSTAT table {self.year}')
        # all rosterstat data
        df_all_data =pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/merged_extracts_years/**/*.csv',recursive=True):
            df_ = pd.read_csv(file,low_memory = False)
            df_all_data = pd.concat([df_all_data,df_])

        df_all_data.to_csv(f'{self.current_directory}/csv_databases/ALL_DATA.csv', index=False)

        # Truncated version drops internal ID columns and advanced stats
        # that aren't needed for Power BI dashboards
        droppable_columns = ['OWNER_TEAM_KEY',
                             'ELIGIBLE_POSITIONS',
                             'ICF',
                             'SAT‑F',
                             'SAT‑A',
                             'CF%',
                             'CREL%',
                             'ZSO',
                             'ZSD',
                             'OZS%',
                             'SHFT',
                             'player_id',
                             'HR_LINK_NAME',
                             'PLAYER',
                             'PLAYER_ID',
                             'PLAYER_KEY'
                             ]
        for col in droppable_columns:
            df_all_data.drop([col], axis=1,inplace=True)
        df_all_data.to_csv(f'{self.current_directory}/csv_databases/ALL_DATA_TRUNCATED.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating DuckDB table from all available data for year {self.year}')
        # Rebuild the DuckDB database from the CSV files on disk.
        # Using read_csv_auto with glob patterns to load all years at once.
        con = duckdb.connect("sql_tables/fantasy_database.duckdb")

        # Create the roster tables
        con.execute("""
            CREATE OR REPLACE TABLE all_roster_data AS
            SELECT *
            FROM read_csv_auto('merged_extracts/**/*.csv')
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
