A git repository with the project this issue is about is checked out somewhere under / (commonly /<project-name>). Find it and work there.

 Add validation column to `rankings` and `full_rankings` computed from 1st CV fold
Add a `validation_score` column to `AutoMLSearch.rankings` and `AutoMLSearch.full_rankings` where the `validation_score` is computed from the 1st CV fold. Note: Don't calculate twice - take from first cv fold.