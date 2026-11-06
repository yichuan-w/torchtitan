I need you to automate our incoming inventory matching workflow. We receive scanned shipping manifests as images, and we need to match the items listed on them to our master product catalog.

Here is what you need to do:
1. Extract the text from the scanned manifest located at `/app/manifest.png`. You may need to install and use an OCR tool like Tesseract (`sudo apt-get update && sudo apt-get install tesseract-ocr -y`).
2. Clean and normalize the extracted text. The OCR output might contain noise, punctuation, or varied casing. You should tokenize and normalize the text (e.g., lowercase, remove special characters).
3. We have a master product catalog located at `/app/catalog.csv` (which has columns `product_id` and `product_name`). You need to match each extracted line from the manifest to the single closest `product_name` in the catalog. Since the OCR text might have typos or abbreviations, you should compute string similarity (e.g., Levenshtein distance, Jaccard similarity, or TF-IDF cosine similarity) to find the best match.
4. Generate a template-based text report of the matches and save it to `/home/user/matches.json`. The JSON must be a list of dictionaries with exactly these keys:
   `[{"ocr_text": "<raw_text_from_image_line>", "matched_product_id": "<best_match_id_from_catalog>"}]`
   Make sure you drop any empty lines from the OCR output before matching.
5. Create a human-readable summary report using template-based generation at `/home/user/report.txt`. For each match, generate a line using this exact template:
   `Match: [{product_id}] {normalized_catalog_name} <- {raw_ocr_text}`

The goal is to get the highest possible matching accuracy despite OCR errors. Please write a Python script (or scripts) to perform this entire pipeline and execute it to produce the final files.