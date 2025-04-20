import json
import time
import os
import logging
import warnings
from datetime import datetime
from datetime import timedelta
import pandas as pd
import numpy as np
from generic import YEAR_INSTANCE


pd.options.display.float_format = '{:,}'.format
np.seterr(divide='ignore')
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings('error')
logging.getLogger("yfpy.query").setLevel(level=logging.INFO)

current_directory = os.getcwd()

print("GOOD DAY! FANTASY HOCKEY 2025 VERSION")
with open(f'{current_directory}/control_file.json', 'r') as f:
    control_file = json.loads(f.read())

# I can move these into the generalized system
today = (datetime.now()).strftime('%Y-%m-%d')
yesterday = (datetime.now() - timedelta(1)).strftime('%Y-%m-%d')
print(f'Today: {today} >><< Yesterday: {yesterday}')
run_type = control_file['run_type']
years_to_check = control_file['Years']
years_to_check = [int(x) for x in years_to_check.keys() if years_to_check[x]['status'] == "RUN"]
print(f'Running the following years: {years_to_check}')
# ============================================================================================
for year in years_to_check:
    yahoo_api_instance = YEAR_INSTANCE(control_file, current_directory, year)
    yahoo_api_instance.extract_yahoo_league_metadata()