import yfpy


class year_instance:
    """
    Generic engine for running the code.
    """

    def __init__(self, control_file, current_directory, year):
        self.control_file       = control_file
        self.year               = year
        self.league_id          = str(self.control_file['Years'][str(year)]['league_id'])
        self.game_id            = str(self.control_file['Years'][str(year)]['game_id'])
        self.current_directory  = current_directory
        print(f'Initializing instance for Year {self.year} | League ID {self.league_id} | Game ID {self.game_id}')


        self.query = yfpy.query.YahooFantasySportsQuery(
                                        auth_dir=self.current_directory+'/private',
                                        league_id=self.league_id,
                                        game_id=self.game_id,
                                        game_code='nhl',
                                        all_output_as_json_str=False
                                             )

        print(dir(self.query))