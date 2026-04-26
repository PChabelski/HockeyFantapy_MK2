import rag_libraries
import pandas as pd

df = pd.read_csv('merged_extracts_years/2025_ALL_DATA.csv')

# Ask user whether to re-index or load from existing
print("Options:")
print("  1 — Load existing index (fast, ~30 seconds)")
print("  2 — Re-index from scratch (slow, 8-12 minutes)")
choice = input("Choose 1 or 2: ").strip()

if choice == "2":
    rag_libraries.index(df)
else:
    rag_libraries.load_existing(df)

# Query loop
while True:
    query = input('\nAsk a question about the 2025 season (type EXIT to stop): ').strip()

    if query.upper() == 'EXIT':
        print("Goodbye.")
        break

    if not query:
        continue

    answer = rag_libraries.ask(query)
    print(f"\n{answer}")