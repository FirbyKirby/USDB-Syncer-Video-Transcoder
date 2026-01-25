"""Test script for Phase 6: Settings GUI verification settings."""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    TranscoderConfig,
    VerificationConfig,
    load_config,
    save_config,
    get_config_path,
)


def test_config_defaults():
    """Test that verification config has correct defaults."""
    print("Testing config defaults...")
    cfg = TranscoderConfig()
    
    assert cfg.verification.enabled == True, "Verification should be enabled by default"
    assert cfg.verification.tolerance_preset == "balanced", "Default preset should be 'balanced'"
    assert cfg.verification.custom_i_tolerance is None, "Custom I tolerance should be None by default"
    assert cfg.verification.custom_tp_tolerance is None, "Custom TP tolerance should be None by default"
    assert cfg.verification.custom_lra_tolerance is None, "Custom LRA tolerance should be None by default"
    
    print("✓ Config defaults test passed")


def test_tolerance_presets():
    """Test that tolerance presets return correct values."""
    print("\nTesting tolerance presets...")
    cfg = TranscoderConfig()
    
    # Test balanced preset (default)
    tolerances = cfg.verification.get_active_tolerances()
    assert tolerances.i_tolerance == 1.5, "Balanced I tolerance should be 1.5"
    assert tolerances.tp_tolerance == 0.5, "Balanced TP tolerance should be 0.5"
    assert tolerances.lra_tolerance == 3.0, "Balanced LRA tolerance should be 3.0"
    
    # Test strict preset
    cfg.verification.tolerance_preset = "strict"
    tolerances = cfg.verification.get_active_tolerances()
    assert tolerances.i_tolerance == 1.0, "Strict I tolerance should be 1.0"
    assert tolerances.tp_tolerance == 0.3, "Strict TP tolerance should be 0.3"
    assert tolerances.lra_tolerance == 2.0, "Strict LRA tolerance should be 2.0"
    
    # Test relaxed preset
    cfg.verification.tolerance_preset = "relaxed"
    tolerances = cfg.verification.get_active_tolerances()
    assert tolerances.i_tolerance == 2.0, "Relaxed I tolerance should be 2.0"
    assert tolerances.tp_tolerance == 0.8, "Relaxed TP tolerance should be 0.8"
    assert tolerances.lra_tolerance == 4.0, "Relaxed LRA tolerance should be 4.0"
    
    print("✓ Tolerance presets test passed")


def test_custom_tolerances():
    """Test that custom tolerances override presets."""
    print("\nTesting custom tolerances...")
    cfg = TranscoderConfig()
    
    # Set custom tolerances
    cfg.verification.custom_i_tolerance = 0.8
    cfg.verification.custom_tp_tolerance = 0.4
    cfg.verification.custom_lra_tolerance = 1.5
    
    tolerances = cfg.verification.get_active_tolerances()
    assert tolerances.i_tolerance == 0.8, "Custom I tolerance should be used"
    assert tolerances.tp_tolerance == 0.4, "Custom TP tolerance should be used"
    assert tolerances.lra_tolerance == 1.5, "Custom LRA tolerance should be used"
    
    print("✓ Custom tolerances test passed")


def test_config_save_load():
    """Test that verification config saves and loads correctly."""
    print("\nTesting config save/load...")
    
    # Create a config with custom settings
    cfg = TranscoderConfig()
    cfg.verification.enabled = False
    cfg.verification.tolerance_preset = "strict"
    cfg.verification.custom_i_tolerance = 0.9
    cfg.verification.custom_tp_tolerance = 0.35
    cfg.verification.custom_lra_tolerance = 2.5
    
    # Save config
    save_config(cfg)
    print(f"  Saved config to: {get_config_path()}")
    
    # Load config
    loaded_cfg = load_config()
    
    # Verify all settings were loaded correctly
    assert loaded_cfg.verification.enabled == False, "Enabled setting should be loaded"
    assert loaded_cfg.verification.tolerance_preset == "strict", "Preset should be loaded"
    assert loaded_cfg.verification.custom_i_tolerance == 0.9, "Custom I tolerance should be loaded"
    assert loaded_cfg.verification.custom_tp_tolerance == 0.35, "Custom TP tolerance should be loaded"
    assert loaded_cfg.verification.custom_lra_tolerance == 2.5, "Custom LRA tolerance should be loaded"
    
    print("✓ Config save/load test passed")
    
    # Reset to defaults for next tests
    cfg = TranscoderConfig()
    save_config(cfg)
    print("  Reset config to defaults")


def test_partial_custom_tolerances():
    """Test that partial custom tolerances fall back to presets."""
    print("\nTesting partial custom tolerances...")
    cfg = TranscoderConfig()
    
    # Set only I tolerance custom, others should use preset
    cfg.verification.tolerance_preset = "balanced"
    cfg.verification.custom_i_tolerance = 1.2
    cfg.verification.custom_tp_tolerance = None
    cfg.verification.custom_lra_tolerance = None
    
    # Should use preset because not all custom values are set
    tolerances = cfg.verification.get_active_tolerances()
    assert tolerances.i_tolerance == 1.5, "Should use preset when custom is incomplete"
    assert tolerances.tp_tolerance == 0.5, "Should use preset when custom is incomplete"
    assert tolerances.lra_tolerance == 3.0, "Should use preset when custom is incomplete"
    
    print("✓ Partial custom tolerances test passed")


def run_all_tests():
    """Run all configuration tests."""
    print("=" * 60)
    print("Phase 6 Settings Configuration Tests")
    print("=" * 60)
    
    try:
        test_config_defaults()
        test_tolerance_presets()
        test_custom_tolerances()
        test_partial_custom_tolerances()
        test_config_save_load()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return True
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
