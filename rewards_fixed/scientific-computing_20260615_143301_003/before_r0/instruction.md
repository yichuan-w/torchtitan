I've assembled a compound library for virtual screening in `library.tsv` (tab-separated, columns: MOL_ID, SMILES). The entries come from various vendors and databases — some won't even parse, salts are mixed in, charges are all over the place, and the tautomeric forms are whatever the vendor decided to draw.

Before I can dock these, I need a clean, standardized, drug-like subset.

**For each molecule that can be salvaged:**
1. Fix any SMILES that fail to parse — correct the underlying chemistry, don't just skip them. If a molecule is truly unfixable, skip it.
2. Standardize to a neutral, canonical tautomer form — counterions, solvents, and metals must be removed. Charges should be neutralized where chemically possible.
3. Compute Lipinski descriptors: molecular weight (MW), Wildman-Crippen LogP, H-bond donors (HBD), H-bond acceptors (HBA).
4. Check against PAINS (Pan Assay Interference Compounds) substructure filters. Use RDKit's PAINS filter catalog.

**Filtering — exclude any molecule that meets ANY of:**
- Fails 2 or more Lipinski Rule-of-Five criteria (MW > 500, LogP > 5, HBD > 5, HBA > 10)
- Matches any PAINS substructure

**Output:** Write `curated_library.csv` with columns:
`MOL_ID`, `original_SMILES`, `standardized_SMILES`, `MW`, `LogP`, `HBD`, `HBA`, `passes_filter`

Where `passes_filter` is True if the molecule survives both Lipinski and PAINS filters, False otherwise. Only include rows where `passes_filter` is True. Sort by MOL_ID. MW to 1 decimal place, LogP to 2 decimal places.

The input file is tab-separated. You can install whatever Python packages you need.