I'm a computational chemist and I've been collecting compounds for a virtual screening campaign. The library is in /app/library/ - it's a mix of SMILES (.smi) and SDF (.sdf) files from different collaborators and vendors. The manifest.txt tells you which files to process.

The problem is the library is a mess. Some compounds are stored as salts (hydrochlorides, sodium salts), some have weird tautomeric forms, and there are definitely duplicates across the different batches. I need to clean this up before I can run any docking.

Here's what I need:

**Standardization**: For each molecule, strip any salts/counterions, neutralize charged groups where chemically reasonable, and generate a consistent tautomeric form. The standardized molecules should be the parent compound, not the salt form.

**Property calculation**: Compute these six properties for each standardized molecule:
- Molecular Weight (MW, in daltons)
- LogP (octanol-water partition coefficient)
- Number of Hydrogen Bond Donors (HBD)
- Number of Hydrogen Bond Acceptors (HBA)
- Topological Polar Surface Area (TPSA, in Å²)
- Number of Rotatable Bonds

**Lipinski filtering**: Keep only molecules that pass all four Lipinski rules:
- MW ≤ 500
- LogP ≤ 5
- HBD ≤ 5
- HBA ≤ 10

**Deduplication**: Remove duplicates. Two molecules are duplicates if they represent the same chemical structure after standardization.

**Output**: Write the cleaned library to `/app/cleaned_library.sdf`. Each molecule entry should have the computed properties stored as SD tags with these exact field names: `MW`, `LogP`, `HBD`, `HBA`, `TPSA`, `RotBonds`. The molecules should have reasonable 3D coordinates (generate conformers if the input doesn't have them).

If a molecule in the input can't be parsed, skip it and move on.