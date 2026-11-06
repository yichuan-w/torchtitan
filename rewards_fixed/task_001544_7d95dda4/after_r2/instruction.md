I need you to design DNA primers for a set of target sequences while minimizing off-target binding against a background genome. I have set up an internal alignment API that calculates off-target binding scores, but the services are not fully configured or running.

Here are the details:
1. **Service Configuration:**
   In `/app/services/`, there is a simple alignment microservice system consisting of a Flask API (`api.py`) and a Redis cache. 
   - The API uses Redis to cache alignment results. You need to edit `api.py` or provide the correct environment variables so it connects to a local Redis instance on port `6379` (database `0`).
   - Start the Redis server and the Flask API (on port `5000`). Make sure they are running in the background.

2. **Primer Design Constraints:**
   - I have provided a file `/home/user/targets.fasta` containing 5 target DNA sequences (each 100bp).
   - For each target, you must select one 20-bp Forward primer and one 20-bp Reverse primer.
   - The Forward primer must be an exact contiguous 20-bp substring from the *first 40 bases* (index 0 to 39) of the target sequence.
   - The Reverse primer must be the reverse complement of an exact contiguous 20-bp substring from the *last 40 bases* (index 60 to 99) of the target sequence.
   
3. **Minimizing Off-Targets (Orchestration):**
   - The API exposes an endpoint `POST http://localhost:5000/score` for scoring primer sequences. This score represents the maximum off-target binding affinity against a background genome (`/home/user/background.fasta`). The service enforces its configured request quota, so use the interface it provides efficiently enough to score the full candidate set.
   - Write a Python script `/home/user/design.py` that iterates through all valid 20-bp candidate primers for each target, queries the API to get their off-target scores, and selects the Forward and Reverse primer pair with the **lowest** off-target score for each target.

4. **Output:**
   - Your script must save the final selected primers to `/home/user/optimized_primers.csv` with exactly three columns: `Target_ID`, `Forward_Primer`, `Reverse_Primer`.
   - Run your script so that the CSV file is generated.

Your goal is to ensure the sum of the off-target scores of all 10 selected primers is as low as possible. An automated evaluator will parse your CSV and independently calculate the off-target scores.
