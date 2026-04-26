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
    Generic engine for running the post-online data scripts. Bulk analytics / transforms / calculations
    """

    def __init__(self, control_file, current_directory, year):
        self.control_file       = control_file
        self.year               = year
        self.stats_for_year     = self.control_file["Years"][str(self.year)]['scoring_categories']
        self.league_id          = str(self.control_file['Years'][str(self.year)]['league_id'])
        self.game_id            = int(self.control_file['Years'][str(self.year)]['game_id'])
        self.current_directory  = current_directory
        print(f'[{time.ctime()}] Initializing instance for Year {self.year} | League ID {self.league_id} | Game ID {self.game_id}')

        # run some of the metadata extraction functions
        self.ref_list = pd.read_csv(f'{self.current_directory}/manual_data/Hockey_Team_Codes.csv')
        self.master_metadata_file = pd.read_csv(f'{self.current_directory}/manual_data/PLAYER_MASTER_DATA.csv')
        # extract the csv databases - wrap in a try-except block just in case of a fresh install and no applicable databases
        self.df_league_teams = pd.read_csv('csv_databases/LEAGUE_TEAMS.csv')



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

        self.goalie_req_bypass = {
            2025:[12]
        }

######################################################################################################
######################################################################################################
######################################################################################################
    def helper_check_for_missed_starts(self, df):
        '''
        For each merged day file, check if each GM was missing any player starts
        For each player on a GM team, check if benched players had room on any roster position
        Ie, Look at DISPLAY_POSITION (split by comma); for each display position, check if that GM already had those positions filled up

        '''
        # start by filtering down to the non-free agent teams

        for gm in df['OWNER_TEAM_NAME'].unique():
            if gm == 'FREE_AGENTS':
                continue
            else:
                df_gm = df[df['OWNER_TEAM_NAME']==gm]
                df_gm_benched=df_gm[df_gm['SELECTED_POSITION']=='BN']
                # Not doing goalies - sometimes these are tactically benched!
                df_gm_benched_w_games = df_gm_benched[(df_gm_benched['GAME_PLAYED']==1)&(df_gm_benched['DISPLAY_POSITION']!='G')]
                if len(df_gm_benched_w_games)==0:
                    # they had no benched players playing -> continue
                    continue
                else:
                    # Count how many spots they have available -> if there are no spots, no sense in checking
                    # 2011 was the only year we didn't have a util spot

                    avail_UTIL_spots = 1-len(df_gm[(df_gm['SELECTED_POSITION']=='Util')&(df_gm['GAME_PLAYED']==1)]) if int(self.year)!= 2011 else 0
                    # Apply the UTIL spot to the D, C, LW, RW positions
                    avail_C_spots = 2 -len(df_gm[(df_gm['SELECTED_POSITION']=='C')&(df_gm['GAME_PLAYED']==1)])
                    avail_LW_spots = 2 -len(df_gm[(df_gm['SELECTED_POSITION']=='LW')&(df_gm['GAME_PLAYED']==1)])
                    avail_RW_spots = 2 -len(df_gm[(df_gm['SELECTED_POSITION']=='RW')&(df_gm['GAME_PLAYED']==1)])
                    avail_D_spots = 4 -len(df_gm[(df_gm['SELECTED_POSITION']=='D')&(df_gm['GAME_PLAYED']==1)])
                    for player in df_gm_benched_w_games['NAME'].unique():
                        player_df = df_gm_benched_w_games[df_gm_benched_w_games['NAME']==player]
                        position = player_df['DISPLAY_POSITION'].iloc[0]
                        date = player_df['DATE'].iloc[0] # for diangostics
                        try:
                            position_arr = position.split(',')
                        except:
                            position_arr = [position]
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
        player_mapping = self.master_metadata_file[self.master_metadata_file['season']==int(self.year)]
        dates_df = pd.read_csv(f'{self.current_directory}/league_weeks_and_dates/{self.year}_league_weeks_and_dates.csv')

        df_all_stats = pd.DataFrame()

        for filename in glob.glob(f'{self.current_directory}/team_rosters_by_date/{self.year}/*.csv', recursive=True):
            yh_df = pd.read_csv(filename)
            date = filename.split('/')[-1].split('_')[-1].split('.')[0]
            year = filename.split('\\')[-1].split('_')[0]
            try:
                week = dates_df[dates_df['date'] == date]['week'].iloc[0]
            except:
                print(f'{date} is outside of league weeks range, setting to 0')
                week = 0
            hr_filename = filename.split('/')[0] + '/' + 'hr_data_extract' + f'/{year}/HR_{date}.csv'
            try:
                hr_df = pd.read_csv(hr_filename)

            except FileNotFoundError:
                print(f'File not found: {hr_filename}. Creating blank HR dataframe.')
                continue

            # Map the Yahoo names to HR names using the player_mapping
            yh_df = yh_df.merge(player_mapping[['yahoo_name', 'player_id', 'HR_LINK_NAME']], left_on="PLAYER_ID",
                                right_on="player_id", how="left")
            # Link to HR data using HR_LINK_NAME
            yh_df = yh_df.merge(hr_df, left_on="HR_LINK_NAME", right_on="HR_LINK_NAME", how="outer",
                                suffixes=('_YH', '_HR'))
            # And finally add the week info so we can run some matchup analytics

            yh_df['WEEK'] = week
            yh_df['SEASON'] = year
            yh_df['DATE'] = date
            yh_df['MONTH'] = self.months[int(date.split('-')[1])]
            # THIS SHOULD HAVE EVERYTHING NOW

            os.makedirs(f'{self.current_directory}/merged_extracts/{year}/', exist_ok=True)
            yh_df['NAME'] = yh_df['NAME'].fillna(yh_df['PLAYER'])
            yh_df['PPP'] = yh_df['GOALSPP']+yh_df['ASSISTSPP']
            yh_df['SHP'] = yh_df['GOALSSH']+yh_df['ASSISTSSH']
            yh_df['W'] = yh_df['DEC'].apply(lambda x: 1 if x == 'W' else 0)#.fillna(0)
            yh_df['L'] = yh_df['DEC'].apply(lambda x: 1 if x == 'L' else 0)#.fillna(0)
            yh_df['MINUTES'] = yh_df['TOI'].apply(lambda x: int(x.split(':')[0]) if pd.notna(x) else 0)
            yh_df['SECONDS'] = yh_df['TOI'].apply(lambda x: int(x.split(':')[1]) if pd.notna(x) else 0)
            yh_df['TOI'] = yh_df['MINUTES'] + yh_df['SECONDS'] / 60
            yh_df['GOALIE_TOI'] = yh_df.apply(lambda x: x['TOI'] if x['DISPLAY_POSITION'] == 'G' else 0, axis=1)
            yh_df['GAA'] = yh_df.apply(lambda x: round(x['GA'] / x['GOALIE_TOI']  * 60, 3) if x['GOALIE_TOI']  > 0 else '', axis = 1)#.fillna(0)
            yh_df['SV'] = yh_df['SV']#.fillna(0)
            yh_df['SA'] = yh_df['SA']#.fillna(0)
            yh_df['GA'] = yh_df['GA']#.fillna(0)
            yh_df['SO'] = yh_df['SO']#.fillna(0)
            yh_df['W'] = yh_df['W']#.fillna(0)
            yh_df['L'] = yh_df['L']#.fillna(0)
            yh_df['GWG'] = yh_df['GOALSGW'].copy()

            # Clean up some of the metadata things
            yh_df['OWNER_TEAM_KEY'] = yh_df['OWNER_TEAM_KEY'].fillna('999.l.99999.t.99')
            yh_df['OWNER_TEAM_NAME'] = yh_df['OWNER_TEAM_NAME'].fillna('FREE_AGENTS')
            yh_df['OWNER_TEAM_GM'] = yh_df['OWNER_TEAM_GM'].fillna('FREE_AGENCY')


            yh_df['FP_SKATER'] = 0
            for col in self.fp_score_dict_skaters.keys():
                yh_df['FP_SKATER'] += yh_df[col]*self.fp_score_dict_skaters[col]


            yh_df['FP_GOALIE'] = 0
            for col in self.fp_score_dict_goalies.keys():
                yh_df['FP_GOALIE'] += yh_df[col]*self.fp_score_dict_goalies[col]

            yh_df['FP'] = yh_df['FP_SKATER'].fillna(0) + yh_df['FP_GOALIE'].fillna(0)
            # Let's make a column that tags if a player on our list actually played a game. Use goals as a filter
            yh_df['GAME_PLAYED'] = yh_df['G'].apply(lambda x: 1 if pd.notnull(x) else 0)
            yh_df['MISSED_START'] = 0
            # Run the missed game checker
            yh_df = self.helper_check_for_missed_starts(yh_df)

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
            yh_df.drop_duplicates('HR_LINK_NAME', keep='first', inplace=True)
            yh_df.to_csv(f'{self.current_directory}/merged_extracts/{year}/MERGED_{date}.csv', index=False)


    def yearly_stat_roster_combiner(self):
        print(f'RUNNING ROSTER-STAT COMBINER FOR YEAR {self.year}')
        # all rosterstat data
        df_all_data =pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/merged_extracts/{self.year}/*.csv',recursive=True):
            df_ = pd.read_csv(file)
            df_all_data = pd.concat([df_all_data,df_])

        os.makedirs(f'{self.current_directory}/merged_extracts_years/', exist_ok=True)
        df_all_data.to_csv(f'{self.current_directory}/merged_extracts_years/{self.year}_ALL_DATA.csv', index=False)

    def matchup_analytics(self):

        print(f'RUNNING MATCHUP ANALYTICS FOR YEAR {self.year}')
        cf_year = self.control_file['Years'][str(self.year)]
        # con = duckdb.connect("sql_tables/fantasy_database.duckdb")
        # this should be replaced with a query but for now let's use the csv
        # also all the extra data quality updates - should we do this in the extraction step?
        # query to grab all the stitched data in the year
        #df_merged = con.execute(f"SELECT * FROM all_roster_data where SEASON == {self.year}").df()
        df_merged = pd.read_csv(f'merged_extracts_years/{self.year}_ALL_DATA.csv',low_memory=False)
        df_merged.rename({'PTS': 'P', 'GOALSPP': 'PPG', 'ASSISTSPP': 'PPA', 'GOALSSH': 'SHG', 'ASSISTSSH': 'SHA',
                          'GOALSGW': 'GWG', 'HIT': 'HIT', 'BLK': 'BLK', 'S': 'SOG'}, axis=1, inplace=True)

        # convert the GOALIE_TOI from string to total minutes
        ##

        basic_cols =   ['SEASON', 'PRIMARY_MONTH','WEEK', 'MATCHUP', 'TEAM_NAME', 'TEAM_KEY', 'GM_NAME','RESULT', 'SCORE','GAME_DAYS', 'G', 'A', 'P',
                             '+/-', 'PIM', 'PPP', 'SHP', 'SOG', 'SH%', 'GWG', 'HIT', 'BLK', 'SV', 'SO', 'GA', 'SA',
                             'SV%', 'GOALIE_TOI', 'W', 'L', 'GAA', 'SKATER_STARTS','BENCH_STARTS', 'GOALIE_STARTS',
                             'FAILED_GOALIE_REQUIREMENT','FP','BENCH_FP','MISSED_START']


        df_year_matchup_results = pd.DataFrame()
        df_league_scoreboard = pd.read_csv(
            f'{self.current_directory}/league_scoreboards_by_week/{self.year}_league_scoreboards.csv')
        for week in df_league_scoreboard['week'].unique():
            df_week = df_league_scoreboard[df_league_scoreboard['week'] == week]
            df_week_results = pd.DataFrame(
                    columns=basic_cols)
            game_days = len(df_merged[df_merged['WEEK']==int(week)]['DATE'].unique())
            if len(df_merged[df_merged['WEEK']==int(week)])==0:
                print(f'Week {week} has no data yet! Skipping matchup analytics for this week')
                continue
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

                df_team_a_stats = df_merged[
                    (df_merged['OWNER_TEAM_KEY'] == team_a_key) & (df_merged['WEEK'] == week) &
                    (df_merged['SELECTED_POSITION'] != 'BN') &
                    (df_merged['SELECTED_POSITION'] != 'IR+') &
                    (df_merged['SELECTED_POSITION'] != 'IR')&
                    (df_merged['SELECTED_POSITION'] != 'NA')]
                gm_name = df_team_a_stats['OWNER_TEAM_GM'].iloc[0]
                df_team_a_benched = df_merged[
                    (df_merged['OWNER_TEAM_KEY'] == team_a_key) & (df_merged['WEEK'] == week) & (
                    (df_merged['SELECTED_POSITION'] == 'BN') |
                    (df_merged['SELECTED_POSITION'] == 'IR+') |
                    (df_merged['SELECTED_POSITION'] == 'IR') |
                    (df_merged['SELECTED_POSITION'] == 'NA'))]

                benched_starts = len(
                    df_team_a_benched[
                        (df_team_a_benched['SELECTED_POSITION'] != 'G') & (df_team_a_benched['TOI'] != 0)])

                skater_starts = len(
                    df_team_a_stats[(df_team_a_stats['SELECTED_POSITION'] != 'G') & (df_team_a_stats['TOI'] != 0)])
                goalie_starts = len(
                    df_team_a_stats[(df_team_a_stats['SELECTED_POSITION'] == 'G') & (df_team_a_stats['TOI'] != 0)])

                # I need to add something here to prevent goalie stats getting rolled into the skater stats...
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
                if goalie_starts < 3 and week not in self.goalie_req_bypass.get(int(self.year),[]):
                    sv = 0
                    so = -1
                    ga = 100
                    sa = 1000
                    sv_pct = 0
                    # Need to convert the TOI string to total minutes
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
                    # Need to convert the TOI string to total minutes
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
                    # Need to convert the TOI string to total minutes
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

                team_a_total_score = 0
                team_b_total_score = 0
                # Now that we've extracted everything, let's manually compare the hr-based stats to the yahoo scoreboard stats to make sure they line up

                for category in cf_year['scoring_categories']:
                    team_a_result = df_matchup_results[category].iloc[0]
                    team_b_result = df_matchup_results[category].iloc[1]

                    if category not in ['GAA', 'L']:

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
                    #df_matchup_results.loc[0, f'{category}_SCORE'] = int(team_a_cat_score)
                    #df_matchup_results.loc[1, f'{category}_SCORE'] = int(team_b_cat_score)
                df_matchup_results.loc[0, 'CALCULATED_SCORE'] = int(team_a_total_score)
                df_matchup_results.loc[1, 'CALCULATED_SCORE'] = int(team_b_total_score)

                if team_a_total_score > team_b_total_score:
                    df_matchup_results.loc[0, 'CALCULATED_RESULT'] = 'WIN'
                    df_matchup_results.loc[1, 'CALCULATED_RESULT'] = 'LOSS'
                elif team_a_total_score < team_b_total_score:
                    df_matchup_results.loc[0, 'CALCULATED_RESULT'] = 'LOSS'
                    df_matchup_results.loc[1, 'CALCULATED_RESULT'] = 'WIN'
                else:
                    df_matchup_results.loc[0, 'CALCULATED_RESULT'] = 'TIE'
                    df_matchup_results.loc[1, 'CALCULATED_RESULT'] = 'TIE'

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


                # Data quality metrics

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
            # now do the weekly slicing==============================================================
            df_week_results['WEEK_QS'] = 0
            categories_count = 0
            for category in cf_year['scoring_categories']:
                if category not in ['L','GAA']:
                    df_week_results['WEEK_QS'] +=  df_week_results[category].rank(pct=True)
                else:
                    df_week_results['WEEK_QS'] +=  df_week_results[category].rank(pct=True,ascending=False)
                categories_count+=1
            df_week_results['WEEK_QS']  = round(df_week_results['WEEK_QS']/categories_count*10,3) # standardize the QS across every year we have different categories
            df_week_results['WEEK_QS_RANK']=df_week_results['WEEK_QS'].rank()
            df_year_matchup_results = pd.concat([df_year_matchup_results, df_week_results],
                                                ignore_index=True)

        # now do any yearly slicing ======================================================================
        df_year_matchup_results['YEAR_QS'] = 0
        categories_count = 0
        for category in cf_year['scoring_categories']:
            if category not in ['L', 'GAA']:
                df_year_matchup_results['YEAR_QS'] += df_year_matchup_results[category].rank(pct=True)
            else:
                df_year_matchup_results['YEAR_QS'] += df_year_matchup_results[category].rank(pct=True, ascending=False)
            categories_count += 1
        df_year_matchup_results['YEAR_QS'] = round(df_year_matchup_results['YEAR_QS'] / categories_count * 10,3)  # standardize the QS across every year we have different categories

        os.makedirs(f'{self.current_directory}/analytics_matchups/', exist_ok=True)
        df_year_matchup_results.to_csv(f'analytics_matchups/{self.year}_matchup_data.csv', index=False)

    def power_ranking_analytics(self):
        '''
        This attempts to aggregate the matchups into monthly chunks that are then scrutinized for power rankings
        (instead of power ranking each week, do it by month!)
        '''
        print(f'RUNNING POWER RANKING ANALYTICS FOR YEAR {self.year}')
        cf_year = self.control_file['Years'][str(self.year)]
        # con = duckdb.connect("sql_tables/fantasy_database.duckdb")
        # this should be replaced with a query but for now let's use the csv
        # also all the extra data quality updates - should we do this in the extraction step?
        # query to grab all the stitched data in the year
        # df_merged = con.execute(f"SELECT * FROM all_roster_data where SEASON == {self.year}").df()
        baseline_col_list = ['SEASON','MONTH','GM','YAHOO_POINTS','SKATER_STARTS','BENCHED_STARTS','GOALIE_STARTS','MISSED_STARTS','GOALIE_FAILURES','TOTAL_FP','AVG_FP','TOTAL_QS','AVG_QS','BENCHED_FP','POWER_SCORE','POWER_RANK']
        df_power_rankings = pd.DataFrame(columns=baseline_col_list)
        df_matchups = pd.read_csv(f'analytics_matchups/{self.year}_matchup_data.csv', low_memory=False)
        for month in df_matchups['PRIMARY_MONTH'].unique():
            df_month = df_matchups[df_matchups['PRIMARY_MONTH']==month]
            df_power_rankings_month = pd.DataFrame(columns=baseline_col_list)

            for gm in df_month['GM_NAME'].unique():
                df_month_gm = df_month[df_month['GM_NAME']==gm]

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
                power_score = 0
                power_rank = 0

                df_power_rankings_month.loc[len(df_power_rankings_month)]=(self.year,month,gm,yahoo_points,skater_starts,bench_starts,goalie_starts,missed_starts,failed_goalies,fp,fp_avg,quality_total,quality_avg,benched_fp,power_score,power_rank)


            categories_count = 0
            for category in ['YAHOO_POINTS','SKATER_STARTS','BENCHED_STARTS','GOALIE_STARTS','MISSED_STARTS','GOALIE_FAILURES','TOTAL_FP','AVG_FP','TOTAL_QS','AVG_QS','BENCHED_FP']:
                df_power_rankings_month[f'RANK_{category}'] =  df_power_rankings_month[category].rank(pct=True)
                categories_count+=1

            '''
            Spitballing some formula ideas. 
            60% on QS and FP (mean and totals)
            
            
            0.25*RANK_TOTAL_QS + 0.25*RANK_TOTAL_FP + 
            
            '''
            df_power_rankings_month['POWER_SCORE'] = (df_power_rankings_month['RANK_YAHOO_POINTS'] +
                                                      0.5*df_power_rankings_month['RANK_TOTAL_FP'] +
                                                      0.5*df_power_rankings_month['RANK_AVG_FP'] +
                                                      0.5 * df_power_rankings_month['RANK_TOTAL_QS'] +
                                                      0.5 * df_power_rankings_month['RANK_AVG_QS'] -
                                                      0.5*df_power_rankings_month['RANK_MISSED_STARTS'] -
                                                      df_power_rankings_month['RANK_GOALIE_FAILURES'] -
                                                      df_power_rankings_month['RANK_BENCHED_FP'])
            df_power_rankings_month['POWER_RANK'] = df_power_rankings_month['POWER_SCORE'].rank(pct=False,ascending=False,method='first')
            df_power_rankings = pd.concat([df_power_rankings,df_power_rankings_month])

        os.makedirs(f'{self.current_directory}/analytics_power_ranks/', exist_ok=True)
        df_power_rankings.to_csv(f'analytics_power_ranks/{self.year}_power_ranks.csv', index=False)
        # con.close()

    def loyalty_analytics(self):
        '''
        This script calculates the loyalty score for each GM in a season
        Loyalty score is defined as the  number of players on your roster you have drafted / kept that REMAIN on your roster as the season progressed
        a score of 100% indicates you never dropped anyone you drafted or designated as keeper
        '''

        # con = duckdb.connect("sql_tables/fantasy_database.duckdb")
        # trans_df = con.execute(f"SELECT * FROM all_transaction_data where SEASON == {self.year}").df()
        # draft_df = con.execute(f"SELECT * FROM all_draft_data where SEASON == {self.year}").df()
        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        draft_df = pd.read_csv(f'league_drafts/{self.year}_league_draft.csv')

        df_loyalty = pd.DataFrame(columns=['SEASON','OWNER_TEAM_GM','LOYALTY_SCORE','KEEPERS_LEFT','KEEPER_COUNT','DRAFTEES_LEFT','DRAFTEE_COUNT'])

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
                    #print(f'{gm_name} dropped keeper {keeper}, thus lowering loyalty_score to {loyalty_score} ')


            for draftee in gm_draft[(gm_draft['KEEPER']=='NO')]['NAME']:
                # Look to see if this player was ever dropped in the transactions list
                if len(trans_df[(trans_df['NAME']==draftee)&(trans_df['SOURCE_KEY']==gm_key)&(trans_df['TRANSACTION_TYPE']=='drop')])>0:
                    draft_loyalty = draft_loyalty-1
                    loyalty_score = (draft_loyalty + keeper_loyalty) / (count_keepers + count_draftees)
                    #print(f'{gm_name} dropped draftee {draftee}, thus lowering loyalty_score to {loyalty_score} ')

            df_loyalty.loc[len(df_loyalty)]=(self.year,gm_name,loyalty_score,keeper_loyalty,count_keepers,draft_loyalty,count_draftees)

        os.makedirs(f'{self.current_directory}/analytics_loyalty/', exist_ok=True)
        df_loyalty.to_csv(f'analytics_loyalty/{self.year}_loyalty_data.csv', index=False)
        # con.close()

    def keeper_analytics(self):
        '''
        Determine the relative strength of keeper for each GM, on the basis of games kept / FP generated

        '''


        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        draft_df = pd.read_csv(f'league_drafts/{self.year}_league_draft.csv')
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        keeper_df_output = pd.DataFrame(columns=['SEASON','GM','KEEPER','FP','GP','DROPPED','ALL_FP','ALL_GP'])

        for gm in draft_df['GM_NAME_DESTINATION'].unique():
            df_keepers = draft_df[(draft_df['GM_NAME_DESTINATION']==gm)&(draft_df['KEEPER']=='KEEPER')]
            for keeper in df_keepers['NAME'].unique():
                # check if the player was ever dropped this season by the drafting GM
                drop_data = trans_df[(trans_df['NAME']==keeper)&(trans_df['TRANSACTION_TYPE']=='drop')&(trans_df['GM_NAME_SOURCE']==gm)]
                all_fp = df_stats[(df_stats['NAME']==keeper)]['FP'].sum()
                all_gp = df_stats[(df_stats['NAME'] == keeper)]['GAME_PLAYED'].sum()
                if len(drop_data)>0:
                    drop_status = True
                    drop_date = drop_data['TRANSACTION_DATE'].iloc[0]
                    #print(f'{gm} dropped {keeper} on {drop_date}')
                    keeper_fp = df_stats[(df_stats['NAME']==keeper)&(df_stats['DATE']<drop_date)]['FP'].sum()
                    keeper_gp = df_stats[(df_stats['NAME']==keeper)&(df_stats['DATE']<drop_date)]['GAME_PLAYED'].sum()
                else:
                    drop_status = False
                    keeper_fp = all_fp
                    keeper_gp = all_gp

                keeper_df_output.loc[len(keeper_df_output)] = (self.year,gm,keeper,keeper_fp,keeper_gp,drop_status,all_fp,all_gp)
        os.makedirs(f'{self.current_directory}/analytics_keepers/', exist_ok=True)
        keeper_df_output.to_csv(f'analytics_keepers/{self.year}_keeper_stats.csv', index=False)

    def draft_analytics(self):
        '''
        Determine the relative strength of draft pick for each GM, on the basis of games kept / FP generated

        '''


        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        draft_df = pd.read_csv(f'league_drafts/{self.year}_league_draft.csv')
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_ALL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        draft_df_output = pd.DataFrame(columns=['SEASON','GM','DRAFTEE','PICK_ROUND','PICK_NUMBER','FP','GP','DROPPED','ALL_FP','ALL_GP'])

        for gm in draft_df['GM_NAME_DESTINATION'].unique():
            df_draftees = draft_df[(draft_df['GM_NAME_DESTINATION']==gm)&(draft_df['KEEPER']=='NO')]
            for draftee in df_draftees['NAME'].unique():
                # check if the player was ever dropped this season by the drafting GM
                draft_round = df_draftees[df_draftees['NAME']==draftee]['DRAFT_ROUND'].iloc[0]
                draft_pick = df_draftees[df_draftees['NAME']==draftee]['DRAFT_PICK'].iloc[0]

                drop_data = trans_df[(trans_df['NAME']==draftee)&(trans_df['TRANSACTION_TYPE']=='drop')&(trans_df['GM_NAME_SOURCE']==gm)]
                all_fp = df_stats[(df_stats['NAME']==draftee)]['FP'].sum()
                all_gp = df_stats[(df_stats['NAME'] == draftee)]['GAME_PLAYED'].sum()
                if len(drop_data)>0:
                    drop_data.sort_values('TRANSACTION_DATE',ascending=True,inplace=True)
                    drop_status = True
                    drop_date = drop_data['TRANSACTION_DATE'].iloc[0] # first drop instance
                    #print(f'{gm} dropped {draftee} on {drop_date}')
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
        '''
        Determine the spend of each GM per year, on who, and how fruitful the spend was
        '''


        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        faab_df_output = pd.DataFrame(columns=['SEASON','GM','PLAYER','FAAB_COST','FP','GP','DROPPED'])

        for gm in trans_df['GM_NAME_DESTINATION'].unique():
            df_faabers = trans_df[(trans_df['GM_NAME_DESTINATION']==gm)&(~trans_df['FAAB_BID'].isna())]
            for faaber in df_faabers['NAME'].unique():
                # check if the player was ever dropped this season by the drafting GM
                faab_cost = df_faabers[df_faabers['NAME']==faaber]['FAAB_BID'].iloc[0]

                drop_data = trans_df[(trans_df['NAME']==faaber)&(trans_df['TRANSACTION_TYPE']=='drop')&(trans_df['GM_NAME_SOURCE']==gm)]
                all_fp = df_stats[(df_stats['NAME']==faaber)]['FP'].sum()
                all_gp = df_stats[(df_stats['NAME'] == faaber)]['GAME_PLAYED'].sum()
                if len(drop_data)>0:
                    drop_data.sort_values('TRANSACTION_DATE',ascending=True,inplace=True)
                    drop_status = True
                    drop_date = drop_data['TRANSACTION_DATE'].iloc[0] # first drop instance
                    #print(f'{gm} dropped {draftee} on {drop_date}')
                    faab_fp = df_stats[(df_stats['NAME']==faaber)&(df_stats['DATE']<drop_date)]['FP'].sum()
                    faab_gp = df_stats[(df_stats['NAME']==faaber)&(df_stats['DATE']<drop_date)]['GAME_PLAYED'].sum()
                else:
                    drop_status = False
                    faab_fp = all_fp
                    faab_gp = all_gp

                faab_df_output.loc[len(faab_df_output)] = (self.year,gm,faaber,faab_cost,faab_fp,faab_gp,drop_status)

        if len(faab_df_output)==0:
            faab_df_output.loc[len(faab_df_output)] = (self.year, 'N/A', 'N/A', 0, 0, 0, False)

        os.makedirs(f'{self.current_directory}/analytics_faab/', exist_ok=True)
        faab_df_output.to_csv(f'analytics_faab/{self.year}_faab_stats.csv', index=False)

    def streamer_analytics(self):
        '''
        Streamer = player picked up during the week
        How well did streamers pan out
        '''


        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        trans_df = trans_df[trans_df['TRANSACTION_TYPE']=='add']
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])

        streamer_df_output = pd.DataFrame(columns=['SEASON','WEEK','GM','PLAYER','FP','GP','HIGH_OWNERSHIP_CHANGE','LOW_OWNERSHIP_CHANGE','MEAN_OWNERSHIP_CHANGE','HIGH_OWNERSHIP','LOW_OWNERSHIP','MEAN_OWNERSHIP'])

        for gm in trans_df['GM_NAME_DESTINATION'].unique():
            for week in trans_df['WEEK'].unique():
                trans_gm_week_df = trans_df[(trans_df['WEEK']==week)&(trans_df['GM_NAME_DESTINATION']==gm)]
                for name in trans_gm_week_df['NAME'].unique():
                    # check if the player was ever dropped this season by the drafting GM
                    fp = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['FP'].sum()
                    gp = df_stats[(df_stats['NAME']==name)&(df_stats['WEEK']==week)&(df_stats['OWNER_TEAM_GM']==gm)]['GAME_PLAYED'].sum()
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
        '''
        Did you use all your moves in a week (5)
        Did you have a missed goalie start?
        Did you have missed starts?
        '''

        trans_df = pd.read_csv(f'league_transactions/{self.year}_transactions.csv')
        trans_df = trans_df[trans_df['TRANSACTION_TYPE'] == 'add']
        df_stats = pd.read_csv(f'merged_extracts_years/{self.year}_ALL_DATA.csv', low_memory=False)
        trans_df['TRANSACTION_DATE'] = pd.to_datetime(trans_df['TRANSACTION_DATE'])
        df_stats['DATE'] = pd.to_datetime(df_stats['DATE'])
        df_matchup_data = pd.read_csv(f'analytics_matchups/{self.year}_matchup_data.csv', low_memory=False)

        engagement_df_output = pd.DataFrame(columns=['SEASON', 'WEEK', 'GM', 'MAX_POSSIBLE_ADDS', 'ADDS', 'MISSED_STARTS', 'FAILED_GOALIES'])
        max_adds = trans_df['WEEK'].max()*5
        for gm in df_matchup_data['GM_NAME'].unique():
            engagement_df_output_gm = pd.DataFrame(columns=engagement_df_output.columns)

            for week in trans_df['WEEK'].unique():
                trans_gm_week_df = trans_df[(trans_df['WEEK'] == week) & (trans_df['GM_NAME_DESTINATION'] == gm)]
                adds_week = len(trans_gm_week_df)
                # now extract the missed stats from matchups
                matchup_gm_week = df_matchup_data[(df_matchup_data['WEEK'] == week) & (df_matchup_data['GM_NAME'] == gm)]
                if len(matchup_gm_week)>0:
                    MISSED_START = matchup_gm_week['MISSED_START'].iloc[0]
                    FAILED_GOALIE_REQUIREMENT = matchup_gm_week['FAILED_GOALIE_REQUIREMENT'].iloc[0]
                    engagement_df_output_gm.loc[len(engagement_df_output_gm)] = (self.year,week,gm,5,adds_week,MISSED_START,FAILED_GOALIE_REQUIREMENT)
                else:
                    # They were eliminated from playoff contention OR it is week 0 (post draft pre start)
                    MISSED_START = 0
                    FAILED_GOALIE_REQUIREMENT = 0
                    engagement_df_output_gm.loc[len(engagement_df_output_gm)] = (self.year,week,gm,5,adds_week,MISSED_START,FAILED_GOALIE_REQUIREMENT)
            max_adds_gm = engagement_df_output_gm['ADDS'].sum()
            all_missed_starts = engagement_df_output_gm['MISSED_STARTS'].sum()
            all_missed_goalies = engagement_df_output_gm['FAILED_GOALIES'].sum()
            engagement_df_output_gm.loc[len(engagement_df_output_gm)] = (self.year, 'ALL', gm, max_adds, max_adds_gm,all_missed_starts, all_missed_goalies)
            engagement_df_output = pd.concat([engagement_df_output_gm,engagement_df_output])


        os.makedirs(f'{self.current_directory}/analytics_engagement/', exist_ok=True)
        engagement_df_output.to_csv(f'analytics_engagement/{self.year}_engagement_stats.csv', index=False)

    def hospital_analytics(self):
        #con = duckdb.connect("sql_tables/fantasy_database.duckdb")
        #hurt_df = con.execute(f"SELECT * FROM all_roster_data where SEASON == {self.year} and INJURY_NOTE is not NULL").df()
        df_all = pd.read_csv(f'merged_extracts_years/{self.year}_AlL_DATA.csv',low_memory=False)
        hurt_df = df_all[(~df_all['INJURY_NOTE'].isna())]
        print(f'Number of hurt players: {len(hurt_df)}')
        df_hospital = pd.DataFrame(columns=['SEASON','PATIENT_REG_ID','PATIENT','START','END','INJURY','DAYS_AWAY', 'OWNER_TEAM_GM'])
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
                group_start = str(player_group_df['DATE'].min())
                group_end = str(player_group_df['DATE'].max())
                injury = player_group_df['INJURY_NOTE'].iloc[0]
                OWNER_TEAM_GM = player_group_df['OWNER_TEAM_GM'].iloc[0]
                time_in_group = len(player_group_df)
                patient_reg_id = group_start.split(' ')[0]+'-'+patient
                #print(f'{patient} was in the hospital from {group_start} to {group_end} ({time_in_group} days) with a(n) {injury} issue [{patient_reg_id}]')
                df_hospital.loc[len(df_hospital)] = (self.year,patient_reg_id,patient,group_start,group_end,injury,time_in_group, OWNER_TEAM_GM)

        os.makedirs(f'{self.current_directory}/analytics_hospital/', exist_ok=True)
        df_hospital.to_csv(f'analytics_hospital/{self.year}_hospital_data.csv', index=False)


    def create_csv_databases(self):
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

        # Inserting some juicy analytics here
        # ======================================================================
        df_matchups['ALL_TIME_QS'] = 0
        categories_count = 0
        for category in  self.control_file['Years'][str(self.year)]['scoring_categories']:
            if category not in ['L', 'GAA']:
                df_matchups['ALL_TIME_QS'] += df_matchups[category].rank(pct=True)
            else:
                df_matchups['ALL_TIME_QS'] += df_matchups[category].rank(pct=True, ascending=False)
            categories_count += 1
        df_matchups['ALL_TIME_QS'] = round(df_matchups['ALL_TIME_QS'] / categories_count * 10,3)  # standardize the QS across every year we have different categories
        # ======================================================================
        df_matchups.to_csv(f'{self.current_directory}/csv_databases/MATCHUPS.csv', index=False)

        print(f'[{time.ctime()}] Creating/updating ROSTERSTAT table {self.year}')
        # all rosterstat data
        df_all_data =pd.DataFrame()
        for file in glob.glob(f'{self.current_directory}/merged_extracts_years/**/*.csv',recursive=True):
            df_ = pd.read_csv(file,low_memory = False)
            df_all_data = pd.concat([df_all_data,df_])

        df_all_data.to_csv(f'{self.current_directory}/csv_databases/ALL_DATA.csv', index=False)
        # let's also create one that has fewer columns
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
        # Create table from CSV and persist
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

