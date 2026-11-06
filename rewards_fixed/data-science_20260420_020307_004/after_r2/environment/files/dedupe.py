"""
Transaction deduplication pipeline.
Keeps only the most recent transaction per customer.
"""
import polars as pl
from pathlib import Path

def deduplicate_transactions(input_path: str, output_path: str) -> None:
    """
    Deduplicate transactions, keeping the most recent per customer.
    
    Args:
        input_path: Path to input CSV with transactions
        output_path: Path to write deduplicated CSV
    """
    # Read transactions
    lf = pl.scan_csv(input_path)
    
    # Sort by timestamp descending (most recent first), then keep first per customer
    # This should keep the most recent transaction for each customer
    result = (
        lf
        .sort("timestamp", descending=True)
        .unique("customer_id", keep="first")
        .sort("timestamp", descending=True)
        .collect()
    )
    
    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Write result
    result.write_csv(output_path)
    print(f"Deduplicated {len(result)} records to {output_path}")


if __name__ == "__main__":
    deduplicate_transactions(
        "/app/data/transactions.csv",
        "/app/output/deduped.csv"
    )
