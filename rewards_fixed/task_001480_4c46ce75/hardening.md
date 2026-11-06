# Hardening rationale

## Observed strategy

All 12 rewarded attempts install or invoke Tesseract, OCR `/app/manifest.png` with its default page segmentation, normalize each returned line, fuzzy-match it to `/app/catalog.csv`, and write the two requested reports. Attempts 02, 03, 06, 08, 10, and 12 inspect the default Tesseract output first; none checks page orientation. Attempts 01, 04, 05, 07, 09, and 11 call `pytesseract.image_to_string`, also with default segmentation. The current clean, upright image therefore spares every attempt the orientation handling that scanned-document OCR requires.

Before hardening, the rewarded outputs are broadly the intended workflow rather than test edits or hard-coded answers. However, attempts 01, 03, and 12 remove the quantity prefix from `ocr_text`, despite the instruction requiring the raw image line, and still pass the seed verifier. This reward defect will be repaired separately in `run/repair.json` and the scratch verifier.

## Primary sources and reproduced behavior

- Tesseract documentation, pinned commit: https://raw.githubusercontent.com/tesseract-ocr/tessdoc/11ab4f99a5c30f2769b33a8135e7b569ccb9c633/ImproveQuality.md — its page-segmentation table says mode 1 is “Automatic page segmentation with OSD” while mode 3, the default, is “Fully automatic page segmentation, but no OSD.”
- Tesseract issue 2919: https://api.github.com/repos/tesseract-ocr/tesseract/issues/2919 — the reporter using Tesseract 4.1.1 states that text in the wrong orientation must be rotated separately based on mode 0 OSD information when using a segmentation mode without OSD.
- Tesseract 4.1.1 source: https://raw.githubusercontent.com/tesseract-ocr/tesseract/4.1.1/src/api/baseapi.cpp — `GetOsdText` describes `Rotate` as the clockwise rotation needed to make the page upright.
- Pinned runtime artifact: `osd.traineddata` from https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/osd.traineddata with SHA-256 `9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff`.

In the seed sandbox, `convert /app/manifest.png -rotate 180 /tmp/upside.png && tesseract /tmp/upside.png stdout` produced reversed gibberish including `9877 suoydiedns xp`, while `tesseract /tmp/upside.png stdout --psm 0` reported `Orientation in degrees: 180` and `Rotate: 180`. `tesseract /tmp/upside.png stdout --psm 1` recovered the four inventory lines. This confirms the documented behavior with the task's actual Tesseract 4.1.1 installation and image.

## Proposed change and new decision

The supplied manifest will be a 180-degree scan rather than an already-upright scan. The goal, catalog, report paths, schemas, and matching work remain unchanged. A solver must now notice that plausible OCR output is wrong, diagnose orientation, and either use a page-segmentation mode with OSD or explicitly detect and apply the required rotation before OCR. The OCR output identifies a real symptom and mode 0 exposes orientation data, but default mode does not prescribe the correct remediation.

This is one modest operational step beyond the demonstrated workflow: the attempts already install and invoke Tesseract, inspect OCR text, and iterate when line splitting is wrong. They now need to select a suitable Tesseract mode or preprocess a real scan condition before reusing the same matching pipeline.

## Student-fix trials

From fresh resets of the revised image:

1. I applied the fix suggested by the immediately visible warning, using the reported fallback resolution explicitly (`tesseract /app/manifest.png stdout --dpi 70` through the same matching pipeline). This still returned upside-down gibberish and graded `reward=0.0`; the first content failure was the wrong product-ID sequence.
2. I applied the first recovery used across the successful traces when OCR dependencies might be absent: `apt-get update && apt-get install --reinstall -y tesseract-ocr`, then reran the default OCR and matching pipeline. Tesseract remained 4.1.1, returned the same gibberish, and graded `reward=0.0`; the first content failure was again the wrong product-ID sequence.

Neither obvious response passes. The successful reference instead selects OSD-enabled page segmentation after investigating the orientation.

## Attempt replays and prediction

I replayed the complete command streams of successful attempts 03 and 12, each after a fresh `./sandbox reset`, without starting anything by hand. Attempt 03 stopped at its normal final file inspection and graded `reward=0.0`: it produced IDs `105, 101, 103, 101`, failing the exact four-item match sequence (and its raw OCR values lacked the quantity prefixes). Attempt 12 stopped at its normal report inspection and graded `reward=0.0`: it produced `101, 101, 101, 101`, failing the exact match sequence and raw-line checks. Both continued through execution rather than crashing, but their default Tesseract call never corrected the scan orientation.

Expected next-group result: **6 of 12**. Every measured path is disrupted, while the needed diagnosis is exposed by Tesseract's mode-0 output and the remedy is a one-option change or one ImageMagick rotation, so capable attempts that validate OCR plausibility should recover.
