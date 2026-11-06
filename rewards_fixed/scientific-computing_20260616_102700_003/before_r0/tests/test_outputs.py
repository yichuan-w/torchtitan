"""Tests for the cleaned chemical library output."""

import os
import subprocess
import sys
from pathlib import Path


def _ensure_rdkit():
    """Ensure RDKit is available, installing if needed."""
    try:
        import rdkit  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "rdkit", "--quiet"])


def test_output_file_exists():
    """The cleaned library SDF file must exist."""
    output_path = Path("/app/cleaned_library.sdf")
    assert output_path.exists(), f"Output file not found at {output_path}"


def test_output_is_valid_sdf():
    """The output file must be a valid SDF with at least one molecule."""
    _ensure_rdkit()
    from rdkit import Chem

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    mols = [m for m in suppl if m is not None]
    assert len(mols) > 0, "Output SDF must contain at least one molecule"
    assert len(mols) >= 5, f"Expected at least 5 molecules after filtering, got {len(mols)}"


def test_all_molecules_pass_lipinski():
    """Every molecule in the output must satisfy Lipinski's Rule of 5."""
    _ensure_rdkit()
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    violations = []
    for i, mol in enumerate(suppl):
        if mol is None:
            continue
        mw = Descriptors.MolWt(mol)
        logp = Crippen.MolLogP(mol)
        hbd = Descriptors.NumHDonors(mol)
        hba = Descriptors.NumHAcceptors(mol)

        issues = []
        if mw > 500:
            issues.append(f"MW={mw:.1f}>500")
        if logp > 5:
            issues.append(f"LogP={logp:.2f}>5")
        if hbd > 5:
            issues.append(f"HBD={hbd}>5")
        if hba > 10:
            issues.append(f"HBA={hba}>10")

        if issues:
            smile = Chem.MolToSmiles(mol)
            violations.append(f"Molecule {i} ({smile}): {', '.join(issues)}")

    assert len(violations) == 0, (
        f"Found {len(violations)} molecules violating Lipinski rules:\n"
        + "\n".join(violations)
    )


def test_property_tags_present():
    """Every molecule must have all six required SD tags."""
    _ensure_rdkit()
    from rdkit import Chem

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    required_tags = {"MW", "LogP", "HBD", "HBA", "TPSA", "RotBonds"}

    missing = []
    for i, mol in enumerate(suppl):
        if mol is None:
            continue
        props = set(mol.GetPropNames())
        missing_tags = required_tags - props
        if missing_tags:
            smile = Chem.MolToSmiles(mol)
            missing.append(f"Molecule {i} ({smile}): missing {missing_tags}")

    assert len(missing) == 0, (
        f"Found {len(missing)} molecules with missing SD tags:\n"
        + "\n".join(missing)
    )


def test_property_values_are_reasonable():
    """Property values must be within chemically reasonable ranges and match computed values."""
    _ensure_rdkit()
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    issues = []

    for i, mol in enumerate(suppl):
        if mol is None:
            continue

        # Read stored properties
        stored_mw = float(mol.GetProp("MW"))
        stored_logp = float(mol.GetProp("LogP"))
        stored_hbd = int(mol.GetProp("HBD"))
        stored_hba = int(mol.GetProp("HBA"))
        stored_tpsa = float(mol.GetProp("TPSA"))
        stored_rotbonds = int(mol.GetProp("RotBonds"))

        # Compute expected values
        expected_mw = Descriptors.MolWt(mol)
        expected_logp = Crippen.MolLogP(mol)
        expected_hbd = Descriptors.NumHDonors(mol)
        expected_hba = Descriptors.NumHAcceptors(mol)
        expected_tpsa = Descriptors.TPSA(mol)
        expected_rotbonds = Descriptors.NumRotatableBonds(mol)

        # Check tolerance
        smile = Chem.MolToSmiles(mol)
        if abs(stored_mw - expected_mw) > 0.5:
            issues.append(
                f"Molecule {i} ({smile}): MW stored={stored_mw:.1f}, "
                f"computed={expected_mw:.1f}"
            )
        if abs(stored_logp - expected_logp) > 0.1:
            issues.append(
                f"Molecule {i} ({smile}): LogP stored={stored_logp:.2f}, "
                f"computed={expected_logp:.2f}"
            )
        if stored_hbd != expected_hbd:
            issues.append(
                f"Molecule {i} ({smile}): HBD stored={stored_hbd}, "
                f"computed={expected_hbd}"
            )
        if stored_hba != expected_hba:
            issues.append(
                f"Molecule {i} ({smile}): HBA stored={stored_hba}, "
                f"computed={expected_hba}"
            )
        if abs(stored_tpsa - expected_tpsa) > 0.5:
            issues.append(
                f"Molecule {i} ({smile}): TPSA stored={stored_tpsa:.1f}, "
                f"computed={expected_tpsa:.1f}"
            )
        if stored_rotbonds != expected_rotbonds:
            issues.append(
                f"Molecule {i} ({smile}): RotBonds stored={stored_rotbonds}, "
                f"computed={expected_rotbonds}"
            )

    assert len(issues) == 0, (
        f"Found {len(issues)} property mismatches:\n" + "\n".join(issues)
    )


def test_no_duplicate_molecules():
    """Output must not contain duplicate molecules (by canonical SMILES)."""
    _ensure_rdkit()
    from rdkit import Chem

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    seen = {}
    duplicates = []

    for i, mol in enumerate(suppl):
        if mol is None:
            continue
        smile = Chem.MolToSmiles(mol, canonical=True)
        if smile in seen:
            duplicates.append(
                f"Molecule {i} ({smile}) is a duplicate of molecule {seen[smile]}"
            )
        else:
            seen[smile] = i

    assert len(duplicates) == 0, (
        f"Found {len(duplicates)} duplicate molecules:\n" + "\n".join(duplicates)
    )


def test_molecules_are_standardized():
    """Molecules must be standardized: no salts, proper valence, reasonable structures."""
    _ensure_rdkit()
    from rdkit import Chem

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    issues = []

    for i, mol in enumerate(suppl):
        if mol is None:
            continue

        smile = Chem.MolToSmiles(mol)

        # Check for salt indicators: period in SMILES typically means salt/disconnected
        if "." in smile:
            issues.append(
                f"Molecule {i} ({smile}): contains '.' indicating salt/mixture"
            )

        # Check that the molecule has valid valence for all atoms
        try:
            Chem.SanitizeMol(mol)
        except Exception as e:
            issues.append(f"Molecule {i} ({smile}): sanitization failed: {e}")

        # Check that there are no atoms with absurd valence
        for atom in mol.GetAtoms():
            # Check explicit valence is reasonable
            explicit = atom.GetExplicitValence()
            implicit = atom.GetImplicitValence()
            total = explicit + implicit
            atomic_num = atom.GetAtomicNum()
            # Skip check for atoms that can have variable valence
            if atomic_num in {15, 16}:  # P, S can have high valence
                if total > 7:
                    issues.append(
                        f"Molecule {i} ({smile}): atom {atomic_num} "
                        f"has valence {total}"
                    )
            elif atomic_num > 0 and total > 5:
                if atomic_num not in {6}:  # Carbon can have 4
                    if total > 5:
                        issues.append(
                            f"Molecule {i} ({smile}): atom {atomic_num} "
                            f"has valence {total}"
                        )

    assert len(issues) == 0, (
        f"Found {len(issues)} standardization issues:\n" + "\n".join(issues)
    )


def test_at_least_some_input_molecules_were_filtered():
    """Verify that the filtering actually removed some molecules from the input."""
    _ensure_rdkit()
    from rdkit import Chem

    # Count input molecules
    library_dir = Path("/app/library")
    input_count = 0
    for f in library_dir.iterdir():
        if f.name == "manifest.txt":
            continue
        if f.suffix == ".smi":
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        input_count += 1
        elif f.suffix == ".sdf":
            suppl = Chem.SDMolSupplier(str(f))
            input_count += sum(1 for m in suppl if m is not None)

    # Count output molecules
    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    output_count = sum(1 for m in suppl if m is not None)

    assert output_count < input_count, (
        f"Filtering should have removed some molecules. "
        f"Input count: {input_count}, output count: {output_count}"
    )


def test_most_molecules_have_3d_coordinates():
    """At least 80% of output molecules should have reasonable 3D coordinates."""
    _ensure_rdkit()
    from rdkit import Chem

    output_path = "/app/cleaned_library.sdf"
    suppl = Chem.SDMolSupplier(output_path)
    total = 0
    good = 0

    for mol in suppl:
        if mol is None:
            continue
        total += 1
        conf = mol.GetConformer()
        if conf.Is3D():
            positions = [conf.GetAtomPosition(j) for j in range(mol.GetNumAtoms())]
            all_zero = all(p.x == 0 and p.y == 0 and p.z == 0 for p in positions)
            if not all_zero:
                good += 1

    assert total > 0, "No molecules in output"
    ratio = good / total
    assert ratio >= 0.8, (
        f"Only {good}/{total} ({ratio:.0%}) molecules have 3D coordinates, "
        f"need at least 80%"
    )