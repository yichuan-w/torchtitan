import subprocess
import re
import os


def run_chip8(rom_path):
    """Run the chip8 emulator with a ROM and return parsed register state."""
    exe = "/app/chip8"
    if not os.path.exists(exe):
        # Try building it
        result = subprocess.run(
            ["make", "-C", "/app"],
            capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, f"Build failed: {result.stderr}"
        assert os.path.exists(exe), "Binary not created after build"

    result = subprocess.run(
        [exe, rom_path],
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"Emulator crashed on {rom_path}: {result.stderr}"

    # Parse: V0=XX V1=XX ... VF=XX I=XXXX PC=XXXX
    line = result.stdout.strip()
    m = re.match(
        r'V0=([0-9A-F]+) V1=([0-9A-F]+) V2=([0-9A-F]+) V3=([0-9A-F]+) '
        r'V4=([0-9A-F]+) V5=([0-9A-F]+) V6=([0-9A-F]+) V7=([0-9A-F]+) '
        r'V8=([0-9A-F]+) V9=([0-9A-F]+) VA=([0-9A-F]+) VB=([0-9A-F]+) '
        r'VC=([0-9A-F]+) VD=([0-9A-F]+) VE=([0-9A-F]+) VF=([0-9A-F]+) '
        r'I=([0-9A-F]+) PC=([0-9A-F]+)', line
    )
    assert m, f"Cannot parse output: {line}"

    state = {}
    hex_names = ["V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7",
                 "V8", "V9", "VA", "VB", "VC", "VD", "VE", "VF"]
    for i in range(16):
        state[hex_names[i]] = int(m.group(i + 1), 16)
    state["I"] = int(m.group(17), 16)
    state["PC"] = int(m.group(18), 16)
    return state


def test_8XY6_shift_right():
    """
    Test the 8XY6 (shift right) instruction.
    
    Original CHIP-8 behavior (COSMAC VIP):
      8XY6: VX = VY >> 1, VF = LSB of VY before shift
    
    The test ROM sets V0=0xAA, V1=0x03, then executes 8016.
    Correct: V0 = 0x03 >> 1 = 0x01, VF = 1 (LSB of 0x03 = 1)
    The buggy (modern/SCHIP) implementation would produce V0 = 0xAA >> 1 = 0x55, VF = 0.
    """
    _check_rom_integrity("test_sr.ch8",
        "d3819fc54da978102e4cd22c0c4e5116c6ba5bdf9fa9837b3ba379e416cbc153")
    state = run_chip8("/app/test_sr.ch8")

    assert state["V0"] == 0x01, (
        f"8XY6: Expected V0=0x01 (VY>>1 where VY=0x03), "
        f"got V0=0x{state['V0']:02X}. "
        f"The emulator appears to shift VX instead of VY."
    )
    assert state["VF"] == 1, (
        f"8XY6: Expected VF=1 (LSB of VY=0x03), "
        f"got VF={state['VF']}. "
        f"The carry flag is wrong."
    )
    # V1 should be unchanged
    assert state["V1"] == 0x03, (
        f"8XY6: V1 should remain 0x03 (unchanged by shift), "
        f"got V1=0x{state['V1']:02X}."
    )


def test_8XYE_shift_left():
    """
    Test the 8XYE (shift left) instruction.
    
    Original CHIP-8 behavior (COSMAC VIP):
      8XYE: VX = VY << 1, VF = MSB of VY before shift
    
    The test ROM sets V0=0x55, V1=0x81, then executes 801E.
    Correct: V0 = 0x81 << 1 = 0x02, VF = 1 (MSB of 0x81 = 1)
    The buggy (modern/SCHIP) implementation would produce V0 = 0x55 << 1 = 0xAA, VF = 0.
    """
    _check_rom_integrity("test_sl.ch8",
        "6fdc55056f00235b231ef2e1604077cbeed191dda611eae4bd17fd4fc2090e64")
    state = run_chip8("/app/test_sl.ch8")

    assert state["V0"] == 0x02, (
        f"8XYE: Expected V0=0x02 (VY<<1 where VY=0x81), "
        f"got V0=0x{state['V0']:02X}. "
        f"The emulator appears to shift VX instead of VY."
    )
    assert state["VF"] == 1, (
        f"8XYE: Expected VF=1 (MSB of VY=0x81), "
        f"got VF={state['VF']}. "
        f"The carry flag is wrong."
    )
    # V1 should be unchanged
    assert state["V1"] == 0x81, (
        f"8XYE: V1 should remain 0x81 (unchanged by shift), "
        f"got V1=0x{state['V1']:02X}."
    )


def test_FX55_store_increment():
    """
    Test the FX55 (store registers to memory) instruction.
    
    Original CHIP-8 behavior (COSMAC VIP):
      After storing V0..VX at I, I is incremented by X+1.
    
    The test ROM sets I=0x500, V0=0x11, V1=0x22, then executes F155 (store V0..V1).
    Correct: I = 0x500 + 2 = 0x502
    The buggy (modern/SCHIP) implementation leaves I unchanged at 0x500.
    """
    _check_rom_integrity("test_st.ch8",
        "bf38e7b4060ea0b0c085b2b52a79f5192cf4961efacd2415f7a2526e345bb85e")
    state = run_chip8("/app/test_st.ch8")

    assert state["I"] == 0x502, (
        f"FX55: Expected I=0x502 (0x500 + 2 registers stored), "
        f"got I=0x{state['I']:04X}. "
        f"The I register was not incremented after the store."
    )
    # V0 and V1 should still have their original values
    assert state["V0"] == 0x11, (
        f"FX55: Expected V0=0x11 unchanged after store, "
        f"got V0=0x{state['V0']:02X}."
    )
    assert state["V1"] == 0x22, (
        f"FX55: Expected V1=0x22 unchanged after store, "
        f"got V1=0x{state['V1']:02X}."
    )


def test_FX65_load_increment():
    """
    Test the FX65 (load registers from memory) instruction.
    
    Original CHIP-8 behavior (COSMAC VIP):
      After loading V0..VX from I, I is incremented by X+1.
    
    The test ROM writes 0x33, 0x44 to [0x300], [0x301] via FX55,
    resets I to 0x300, clears V0 and V1, then executes F165 (load V0..V1).
    Correct: V0=0x33, V1=0x44, I = 0x300 + 2 = 0x302
    The buggy (modern/SCHIP) implementation leaves I unchanged at 0x300.
    """
    _check_rom_integrity("test_ld.ch8",
        "df5754634d0663c5b72931ff2e6e006212190b6e978191ac44343e496f4c72e0")
    state = run_chip8("/app/test_ld.ch8")

    assert state["V0"] == 0x33, (
        f"FX65: Expected V0=0x33 (loaded from memory), "
        f"got V0=0x{state['V0']:02X}. "
        f"Load from memory returned wrong value."
    )
    assert state["V1"] == 0x44, (
        f"FX65: Expected V1=0x44 (loaded from memory), "
        f"got V1=0x{state['V1']:02X}. "
        f"Load from memory returned wrong value."
    )
    assert state["I"] == 0x302, (
        f"FX65: Expected I=0x302 (0x300 + 2 registers loaded), "
        f"got I=0x{state['I']:04X}. "
        f"The I register was not incremented after the load."
    )


def test_FX33_bcd_conversion():
    """
    Test the FX33 (BCD conversion) instruction.
    
    Original CHIP-8 behavior:
      FX33 stores the BCD representation of VX at I, I+1, I+2.
      memory[I]   = hundreds digit
      memory[I+1] = tens digit
      memory[I+2] = ones digit
    
    The test ROM sets V0=123 (0x7B), I=0x400, executes FX33,
    then loads the digits back into V0, V1, V2 via FX65.
    Correct: V0=1 (hundreds), V1=2 (tens), V2=3 (ones)
    The buggy implementation reverses the digit order.
    """
    _check_rom_integrity("test_bcd.ch8",
        "a89130eec841d3f7ebd1d38fd6eafabdaad9b909c9d59bbd7b949718f901373f")
    state = run_chip8("/app/test_bcd.ch8")

    assert state["V0"] == 1, (
        f"BCD: Expected V0=1 (hundreds digit of 123), "
        f"got V0={state['V0']}. "
        f"BCD digits appear to be in wrong order."
    )
    assert state["V1"] == 2, (
        f"BCD: Expected V1=2 (tens digit of 123), "
        f"got V1={state['V1']}. "
        f"BCD digits appear to be in wrong order."
    )
    assert state["V2"] == 3, (
        f"BCD: Expected V2=3 (ones digit of 123), "
        f"got V2={state['V2']}. "
        f"BCD digits appear to be in wrong order."
    )


def _check_rom_integrity(fname, expected_hash):
    """Verify ROM file hasn't been tampered with."""
    import hashlib
    path = f"/app/{fname}"
    assert os.path.exists(path), f"ROM {fname} not found"
    with open(path, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    assert actual == expected_hash, f"ROM {fname} has been modified — do not modify test ROMs"
