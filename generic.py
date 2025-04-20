from yfpy.query import YahooFantasySportsQuery
from pathlib import Path


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


        self.query = YahooFantasySportsQuery(
                                        league_id=self.league_id,
                                        game_id=self.game_id,
                                        game_code='nhl',
                                        yahoo_consumer_key="dj0yJmk9elVQcHZ2RTBMWGlBJmQ9WVdrOVUwRkpkazFGU1ZjbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PWY4",
                                        yahoo_consumer_secret="8cb89479045212bcf8892d6e0fbbb96cf2acfe51",
                                        save_token_data_to_env_file=True,
                                        env_file_location=Path(f"{current_directory}/private")
                                             )

        self.query.save_access_token_data_to_env_file(
            env_file_location=Path(f"{current_directory}/private"),
            save_json_to_var_only=True
        )

        print('Successfully initialized YahooFantasySportsQuery')
        print('Available functionality:')
        print(dir(self.query))