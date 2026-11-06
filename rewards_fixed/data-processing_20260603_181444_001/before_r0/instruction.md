We acquired two competitors and inherited their customer databases. The prior team dumped exports from all three systems into `/app/` but never finished the integration. Your job: produce a single clean, deduplicated customer database from these three files.

The source files in `/app/`:

- **`customers_mainframe.dat`** — Export from an old mainframe. Fixed-width format (position-based columns, no delimiters, no header row). The fields are: customer_id, first_name, last_name, email, phone, address, city, state, zip, country, signup_date. You'll need to determine column widths by examining the data. Some lines may be corrupt (wrong length) — skip those.

- **`customers_crm.csv`** — CSV export from a CRM system. Has headers. I've spotted some issues: the text encoding seems wrong (accented names look garbled), and I noticed a few dates that don't use the standard format. Some rows may be malformed — skip those. There may be other problems.

- **`customers_web.jsonl`** — JSON Lines export from the web platform (one JSON object per line). The API changed at some point so not all records use the same field names. I've seen flat fields and nested name objects. Some lines may contain invalid JSON — skip those.

- **`customers_support.db`** — SQLite database from the support ticketing system. Contains a `customers` table with the same schema fields.

### What to produce in `/app/`:

**1. `/app/customers_unified.csv`** — A deduplicated customer database. CSV with these columns in order:
`unified_id,first_name,last_name,email,phone,address,city,state,zip,country,signup_date,source_systems`

Requirements:
- unified_id: assign U0001, U0002, U0003, ...
- source_systems: which source files contributed (comma-separated: "mainframe,crm,web")
- All text clean and readable — fix any corruptions
- All dates normalized to YYYY-MM-DD
- Country codes normalized to 2-letter ISO (US, CA, DE, FR, SE, MX, PT)

**2. `/app/dedup_report.json`** — Shows which original records were merged. Map each unified_id to:
```json
{"merged_from": ["id1", "id2", ...], "email": "..."}
```

**3. `/app/data_quality_report.json`** — Catalog every data problem found and fixed. Use this exact structure:

```json
{
  "invalid_emails": ["email1", "email2"],
  "date_format_issues": [
    {"record_id": "CRM-005", "original_value": "08/15/2020", "normalized_to": "2020-08-15"}
  ],
  "encoding_issues_fixed": [
    {"record_id": "CRM-002", "fields": ["first_name", "last_name", "city"]}
  ],
  "corrupt_records_skipped": [
    {"source": "mainframe", "record_id": "00000999", "reason": "wrong line length"}
  ],
  "total_issues_found": 15
}
```

Only include categories that have at least one issue. Omit empty categories.

### Deduplication:
Identify the same real-world customer appearing across systems:
- Same email (case-insensitive) → same customer
- Different emails but same name AND similar street address → same customer
- Different emails and names don't match but same phone number → same customer
- **Important**: Same name at a DIFFERENT address means different people — don't merge those
- When merging, prefer the most recent signup_date and non-empty field values
- Invalid emails (missing @) should still produce a customer record but shouldn't be used for matching
