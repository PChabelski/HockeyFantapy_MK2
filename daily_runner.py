import subprocess
import datetime

print("Starting daily fantasy hockey update...")
today = datetime.datetime.now().strftime("%Y-%m-%d")

# Run in 'latest-live' mode
subprocess.run(["python", "main.py", "--mode", "2"], check=True)

print(f"✅ Daily run completed successfully for {today}")
