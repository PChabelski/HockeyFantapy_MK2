import subprocess
import datetime
import time
try:
    print("Starting daily fantasy hockey update...")
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    # Run in 'latest-live' mode
    subprocess.run(["python", "main.py", "--mode", "2"], check=True)

    print(f"✅ Daily run completed successfully for {today}")
except Exception as e:
    print(f'Ran into an error: {e}')
    time.sleep(30)