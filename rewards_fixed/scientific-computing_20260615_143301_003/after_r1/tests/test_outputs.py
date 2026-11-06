import csv
import os
import subprocess

# The tests use the system Python which has rdkit installed via apt
# We'll run a helper script to avoid import issues


def _run_python(code):
    """Run Python code in a subprocess that has rdkit available.
    Use /usr/bin/python3 (system Python) which has rdkit installed via apt."""
    result = subprocess.run(
        ["/usr/bin/python3", "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"Python subprocess failed: {result.stderr}"
    return result.stdout.strip()


def test_output_file_exists():
    """Verify the curated_library.csv file exists and is readable."""
    path = "/app/curated_library.csv"
    assert os.path.exists(path), f"Output file '{path}' does not exist."
    assert os.path.getsize(path) > 0, f"Output file '{path}' is empty."


def test_csv_has_correct_header():
    """Verify the CSV has the required columns."""
    path = "/app/curated_library.csv"
    with open(path, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
    expected = ["MOL_ID", "original_SMILES", "standardized_SMILES",
                "MW", "LogP", "HBD", "HBA", "passes_filter"]
    assert header == expected, (
        f"Header mismatch.\nExpected: {expected}\nGot: {header}"
    )


def test_all_standardized_smiles_valid():
    """Verify every standardized SMILES can be parsed by RDKit without errors."""
    path = "/app/curated_library.csv"
    code = f"""
import csv
from rdkit import Chem

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        smi = row['standardized_SMILES'].strip()
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            print(f"FAIL:{{row['MOL_ID']}}:{{smi}}")
            exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Some standardized SMILES failed to parse: {stdout}"


def test_no_salts_or_metals_in_output():
    """Verify standardized SMILES contain no salts (no '.' separator) and no metals."""
    path = "/app/curated_library.csv"
    code = f"""
import csv
from rdkit import Chem

metals = {{'Na', 'K', 'Li', 'Mg', 'Ca', 'Zn', 'Fe', 'Cu', 'Mn', 'Co', 'Ni', 'Al', 'Pt', 'Pd', 'Ag', 'Au'}}
with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        smi = row['standardized_SMILES'].strip()
        if '.' in smi:
            print(f"FAIL:{{row['MOL_ID']}}: salt dot found in {{smi}}")
            exit(1)
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        for atom in mol.GetAtoms():
            sym = atom.GetSymbol()
            if sym in metals:
                print(f"FAIL:{{row['MOL_ID']}}: metal atom {{sym}} found")
                exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Salt or metal contamination: {stdout}"


def test_tautomer_standardization():
    """Verify that M019 and M020 (acetylacetone keto/enol tautomers) standardize to the same SMILES."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

smiles = {{}}
with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['MOL_ID'] in ('M019', 'M020'):
            smiles[row['MOL_ID']] = row['standardized_SMILES'].strip()

if 'M019' not in smiles:
    print("FAIL: M019 not found in output")
    exit(1)
if 'M020' not in smiles:
    print("FAIL: M020 not found in output")
    exit(1)
if smiles['M019'] != smiles['M020']:
    print(f"FAIL: M019={{smiles['M019']}} != M020={{smiles['M020']}}")
    exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Tautomer standardization failed: {stdout}"


def test_mol_ids_sorted():
    """Verify the output is sorted by MOL_ID."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    ids = [row['MOL_ID'] for row in reader]

if ids != sorted(ids):
    print(f"FAIL: not sorted. Got: {{ids}}")
    exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Sort order wrong: {stdout}"


def test_all_original_smiles_preserved():
    """Verify original_SMILES matches the input library exactly."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

# Read original library
orig = {{}}
with open('/app/library.tsv', 'r') as f:
    reader = csv.DictReader(f, delimiter='\\t')
    for row in reader:
        orig[row['MOL_ID']] = row['SMILES'].strip()

# Read output
with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        mid = row['MOL_ID']
        if mid not in orig:
            print(f"FAIL: {{mid}} not in original library")
            exit(1)
        if row['original_SMILES'].strip() != orig[mid]:
            print(f"FAIL: {{mid}} original SMILES mismatch: expected '{{orig[mid]}}', got '{{row['original_SMILES'].strip()}}'")
            exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Original SMILES mismatch: {stdout}"


def test_descriptor_values_for_known_compound():
    """Verify descriptor values for paracetamol (M030) are within expected ranges."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['MOL_ID'] == 'M030':
            mw = float(row['MW'])
            logp = float(row['LogP'])
            hbd = int(row['HBD'])
            hba = int(row['HBA'])
            # Paracetamol: MW ~151.2, LogP ~1.35, HBD=2, HBA=2
            if not (149 <= mw <= 154):
                print(f"FAIL: M030 MW={{mw}}, expected ~151")
                exit(1)
            if not (1.0 <= logp <= 1.7):
                print(f"FAIL: M030 LogP={{logp}}, expected ~1.35")
                exit(1)
            if hbd != 2:
                print(f"FAIL: M030 HBD={{hbd}}, expected 2")
                exit(1)
            if hba != 2:
                print(f"FAIL: M030 HBA={{hba}}, expected 2")
                exit(1)
            print("OK")
            exit(0)
    print("FAIL: M030 not found in output")
    exit(1)
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Descriptor check failed for M030: {stdout}"


def test_descriptor_values_for_ibuprofen():
    """Verify descriptor values for ibuprofen (M001) are within expected ranges."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['MOL_ID'] == 'M001':
            mw = float(row['MW'])
            logp = float(row['LogP'])
            hbd = int(row['HBD'])
            hba = int(row['HBA'])
            # Ibuprofen: MW ~206.3, LogP ~3.07, HBD=1, HBA=1 (carbonyl O only, not OH)
            if not (204 <= mw <= 209):
                print(f"FAIL: M001 MW={{mw}}, expected ~206")
                exit(1)
            if not (2.5 <= logp <= 3.6):
                print(f"FAIL: M001 LogP={{logp}}, expected ~3.07")
                exit(1)
            if hbd != 1:
                print(f"FAIL: M001 HBD={{hbd}}, expected 1")
                exit(1)
            if hba != 1:
                print(f"FAIL: M001 HBA={{hba}}, expected 1")
                exit(1)
            print("OK")
            exit(0)
    print("FAIL: M001 not found in output")
    exit(1)
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Descriptor check failed for M001: {stdout}"


def test_lipinski_filter_correct():
    """Verify that molecules failing >=2 Lipinski rules are excluded from output."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        mw = float(row['MW'])
        logp = float(row['LogP'])
        hbd = int(row['HBD'])
        hba = int(row['HBA'])
        violations = 0
        if mw > 500:
            violations += 1
        if logp > 5:
            violations += 1
        if hbd > 5:
            violations += 1
        if hba > 10:
            violations += 1
        if violations >= 2:
            print(f"FAIL: {{row['MOL_ID']}} has {{violations}} Lipinski violations but is in output")
            exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Lipinski filter error: {stdout}"


def test_pains_filter_correct():
    """Verify output contains no PAINS-active molecules."""
    path = "/app/curated_library.csv"
    code = f"""
import csv
from rdkit import Chem
from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams

params = FilterCatalogParams()
params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
catalog = FilterCatalog(params)

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        smi = row['standardized_SMILES'].strip()
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        # Check the standardized molecule itself
        entry = catalog.GetFirstMatch(mol)
        if entry is not None:
            print(f"FAIL: {{row['MOL_ID']}} standardized SMILES matches PAINS: {{entry.GetDescription()}}")
            exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"PAINS contamination in output: {stdout}"


def test_known_pains_excluded():
    """Verify known PAINS compounds (M026, M031, M032, M039) are excluded."""
    path = "/app/curated_library.csv"
    pains_ids = {'M026', 'M031', 'M032', 'M039'}
    found = set()
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            found.add(row['MOL_ID'])
    overlap = pains_ids & found
    assert not overlap, (
        f"PAINS compounds found in output that should be excluded: {overlap}"
    )


def test_known_lipinski_fails_excluded():
    """Verify molecules with >=2 Lipinski violations (M023, M024, M025, M037, M040) are excluded."""
    path = "/app/curated_library.csv"
    exclude_ids = {'M023', 'M024', 'M025', 'M037', 'M040'}
    found = set()
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            found.add(row['MOL_ID'])
    overlap = exclude_ids & found
    assert not overlap, (
        f"Lipinski-failing compounds found in output: {overlap}"
    )


def test_pass_filter_column_consistent():
    """Verify that passes_filter is True for all entries in the output (since filtered)."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        passes = row['passes_filter'].strip()
        if passes != 'True':
            print(f"FAIL: {{row['MOL_ID']}} has passes_filter={{passes}}, expected True")
            exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"passes_filter column error: {stdout}"


def test_at_least_20_output_rows():
    """Verify that after filtering, the output has at least 20 molecules."""
    path = "/app/curated_library.csv"
    count = 0
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for _ in reader:
            count += 1
    assert count >= 20, (
        f"Expected at least 20 output molecules, got {count}. "
        "Check that filtering is not too aggressive."
    )


def test_fixed_broken_smiles_present():
    """Verify that the originally broken SMILES (M011, M012, M013) appear in the output."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

expected = {{'M011', 'M012', 'M013'}}
found = set()
with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        found.add(row['MOL_ID'])

missing = expected - found
if missing:
    print(f"FAIL: Missing molecules: {{missing}}")
    exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Broken SMILES not fixed/present: {stdout}"


def test_no_duplicate_mol_ids():
    """Verify no duplicate MOL_IDs in the output."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

ids = []
with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        ids.append(row['MOL_ID'])

if len(ids) != len(set(ids)):
    from collections import Counter
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    print(f"FAIL: Duplicate MOL_IDs: {{dupes}}")
    exit(1)
print("OK")
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Duplicate MOL_IDs: {stdout}"


def test_zinc_salt_handled():
    """Verify M033 (zinc acetate) was properly handled — metal removed, counterions separated."""
    path = "/app/curated_library.csv"
    code = f"""
import csv

with open({repr(path)}, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row['MOL_ID'] == 'M033':
            smi = row['standardized_SMILES'].strip()
            # Should be acetic acid after metal removal and neutralization
            if 'Zn' in smi:
                print(f"FAIL: M033 still contains Zn: {{smi}}")
                exit(1)
            if '.' in smi:
                print(f"FAIL: M033 still contains salt separator: {{smi}}")
                exit(1)
            print("OK")
            exit(0)
    print("FAIL: M033 not found in output")
    exit(1)
"""
    stdout = _run_python(code)
    assert "OK" in stdout, f"Zinc salt handling failed: {stdout}"
