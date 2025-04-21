from yfpy.query import YahooFantasySportsQuery
from pathlib import Path
import pandas as pd
import os

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

        print('Successfully initialized YahooFantasySportsQuery')
        print('Available functionality:')
        print(dir(self.query))

    def extract_yahoo_league_metadata(self):
        #get_league_draft_results
        # get_league_transactions
        #get_league_matchups_by_week
        # self.team_standings = self.query.get_team_standings(team_id) # this is for a particular team

        self.league_info = self.query.get_league_info()
        self.league_metadata = self.query.get_league_metadata().clean_data_dict()
        self.league_standings = self.query.get_league_standings()
        self.league_teams = self.query.get_league_teams()

        # print('Metadata extracted successfully')
        # print('League Info:')
        # print(self.league_info)
        print('League Metadata:')
        print(self.league_metadata.keys())
        df_league_metadata = pd.DataFrame(columns = self.league_metadata.keys())
        league_metadata_tuple = ()
        for column in self.league_metadata.keys():
            league_metadata_tuple = league_metadata_tuple + (self.league_metadata[column],)
        df_league_metadata.loc[len(df_league_metadata)]= league_metadata_tuple
        if not os.path.exists(f'{self.current_directory}/metadata'):
            os.mkdir(f'{self.current_directory}/metadata')

        df_league_metadata.to_csv(f'{self.current_directory}/metadata/league_metadata.csv', index=False)
        # print('League Standings:')
        # print(self.league_standings)
        # print('League Teams:')
        # print(self.league_teams)

        print('Dynamically generate a dataframe, where applicable')
