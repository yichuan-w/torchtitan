"""Setup script for datatools package.

This script dynamically discovers CLI entry points from a commands directory.
Each subdirectory in commands/ becomes a namespace, and each .py file becomes a command.
"""

from pathlib import Path
from setuptools import setup, find_packages
import os


def discover_entry_points():
    """
    Discover CLI entry points by scanning the commands directory structure.
    
    Directory structure:
        commands/
          __init__.py (contains main_* functions or command classes)
          utils/
            __init__.py
            convert.py (contains main_csv2json, main_json2csv)
            validate.py (contains main_validate)
            stats.py (contains main_stats)
    """
    entry_points = {"console_scripts": []}
    
    commands_dir = Path(__file__).parent / "src" / "datatools" / "commands"
    
    if not commands_dir.exists():
        # Fallback: scan for main_* functions in commands.py
        commands_file = Path(__file__).parent / "src" / "datatools" / "commands.py"
        if commands_file.exists():
            content = commands_file.read_text()
            import re
            pattern = r'def (main_[a-zA-Z0-9_]+)\('
            matches = re.findall(pattern, content)
            for func_name in matches:
                command_name = func_name[5:].replace('_', '-')
                entry_point = f"{command_name} = datatools.commands:{func_name}"
                entry_points["console_scripts"].append(entry_point)
        return entry_points
    
    # Scan for commands in directory structure
    for py_file in commands_dir.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
            
        # Calculate module path
        rel_path = py_file.relative_to(commands_dir.parent)
        module = ".".join(rel_path.with_suffix("").parts)
        
        # Read file and find main_* functions
        content = py_file.read_text()
        import re
        pattern = r'def (main_[a-zA-Z0-9_]+)\('
        matches = re.findall(pattern, content)
        
        for func_name in matches:
            command_name = func_name[5:].replace('_', '-')
            entry_point = f"{command_name} = {module}:{func_name}"
            entry_points["console_scripts"].append(entry_point)
    
    # Add main datatools command
    entry_points["console_scripts"].append("datatools = datatools.commands:main_datatools")
    
    return entry_points


def get_version():
    """Read version from _version.py file."""
    version_file = Path(__file__).parent / "src" / "datatools" / "_version.py"
    if version_file.exists():
        content = version_file.read_text()
        import re
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
        if match:
            return match.group(1)
    return "0.2.0"


if __name__ == "__main__":
    entry_points = discover_entry_points()
    print(f"Discovered entry points: {entry_points}")
    
    setup(
        name="datatools",
        version=get_version(),
        packages=find_packages(where="src"),
        package_dir={"": "src"},
        python_requires=">=3.8",
        entry_points=entry_points,
    )
