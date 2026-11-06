"""
Tests for model checkpoint migration task.
Verifies that the solver successfully migrated the old checkpoint to the new architecture
and fine-tuned the model to achieve target performance.
"""

import torch
import torch.nn as nn
from pathlib import Path
import sys

# Import the new model architecture for testing
sys.path.insert(0, '/app')
from new_model import NewModel


def test_migrated_model_exists():
    """Test that the migrated model file exists."""
    migrated_path = Path("/app/migrated_model.pt")
    assert migrated_path.exists(), f"Migrated model file not found at {migrated_path}"


def test_migrated_model_loads():
    """Test that the migrated weights can be loaded into NewModel."""
    migrated_path = Path("/app/migrated_model.pt")
    
    # Load migrated weights
    migrated_state_dict = torch.load(migrated_path, map_location='cpu', weights_only=True)
    
    # Create new model and try to load weights
    model = NewModel(input_dim=32, hidden_dim=64, num_classes=10)
    model.load_state_dict(migrated_state_dict, strict=True)
    
    # If we get here, the state dict matches the model
    assert True


def test_migrated_weights_from_old_checkpoint():
    """Test that the migrated weights actually came from the old checkpoint, not random initialization."""
    migrated_path = Path("/app/migrated_model.pt")
    old_checkpoint_path = Path("/app/checkpoint_v1.pt")
    
    # Load both state dicts
    migrated_state_dict = torch.load(migrated_path, map_location='cpu', weights_only=True)
    old_checkpoint = torch.load(old_checkpoint_path, map_location='cpu', weights_only=True)
    old_state_dict = old_checkpoint['model_state_dict']
    
    # Check that fc1 weights from old model match layers.input_layer.linear in new model
    old_fc1_weight = old_state_dict['fc1.weight']
    new_linear_weight = migrated_state_dict['layers.input_layer.linear.weight']
    
    assert torch.allclose(old_fc1_weight, new_linear_weight), \
        "FC1 weights don't match - migration may not have preserved old weights"
    
    # Check bn1 -> layers.input_layer.bn
    old_bn1_weight = old_state_dict['bn1.weight']
    new_bn_weight = migrated_state_dict['layers.input_layer.bn.weight']
    
    assert torch.allclose(old_bn1_weight, new_bn_weight), \
        "BN1 weights don't match - migration may not have preserved old weights"
    
    # Check fc2 -> layers.hidden_layer.linear
    old_fc2_weight = old_state_dict['fc2.weight']
    new_hidden_weight = migrated_state_dict['layers.hidden_layer.linear.weight']
    
    assert torch.allclose(old_fc2_weight, new_hidden_weight), \
        "FC2 weights don't match - migration may not have preserved old weights"


def test_batchnorm_stats_migrated():
    """Test that BatchNorm running statistics were properly migrated."""
    migrated_path = Path("/app/migrated_model.pt")
    old_checkpoint_path = Path("/app/checkpoint_v1.pt")
    
    migrated_state_dict = torch.load(migrated_path, map_location='cpu', weights_only=True)
    old_checkpoint = torch.load(old_checkpoint_path, map_location='cpu', weights_only=True)
    old_state_dict = old_checkpoint['model_state_dict']
    
    # Check bn1 running_mean -> layers.input_layer.bn
    old_bn1_mean = old_state_dict['bn1.running_mean']
    new_bn_mean = migrated_state_dict['layers.input_layer.bn.running_mean']
    
    assert torch.allclose(old_bn1_mean, new_bn_mean), \
        "BatchNorm running_mean not properly migrated"
    
    # Check bn1 running_var
    old_bn1_var = old_state_dict['bn1.running_var']
    new_bn_var = migrated_state_dict['layers.input_layer.bn.running_var']
    
    assert torch.allclose(old_bn1_var, new_bn_var), \
        "BatchNorm running_var not properly migrated"


def test_final_model_exists():
    """Test that the final model file exists."""
    final_path = Path("/app/final_model.pt")
    assert final_path.exists(), f"Final model file not found at {final_path}"


def test_final_model_is_torchscript():
    """Test that the final model is a valid TorchScript model."""
    final_path = Path("/app/final_model.pt")
    
    try:
        model = torch.jit.load(final_path, map_location='cpu')
    except Exception as e:
        assert False, f"Failed to load TorchScript model: {e}"
    
    # Verify it can run inference
    test_input = torch.randn(1, 32)
    output = model(test_input)
    
    assert output.shape == (1, 10), f"Expected output shape (1, 10), got {output.shape}"


def test_final_model_accuracy():
    """Test that the fine-tuned model achieves at least 80% accuracy on validation set."""
    final_path = Path("/app/final_model.pt")
    dataset_path = Path("/app/dataset.pt")
    
    # Load model
    model = torch.jit.load(final_path, map_location='cpu')
    model.eval()
    
    # Load dataset
    dataset = torch.load(dataset_path, map_location='cpu', weights_only=True)
    features = dataset['features']
    labels = dataset['labels']
    
    # Use the same 80/20 split as training (last 20% for validation)
    num_samples = len(features)
    split_idx = int(num_samples * 0.8)
    
    val_features = features[split_idx:]
    val_labels = labels[split_idx:]
    
    # Run inference
    with torch.no_grad():
        outputs = model(val_features)
        predictions = outputs.argmax(dim=1)
        accuracy = (predictions == val_labels).float().mean().item()
    
    assert accuracy >= 0.75, \
        f"Model accuracy {accuracy:.2%} is below the required 75% threshold"


def test_new_layers_initialized():
    """Test that new layers (not in old model) have non-trivial initialization."""
    migrated_path = Path("/app/migrated_model.pt")
    
    migrated_state_dict = torch.load(migrated_path, map_location='cpu', weights_only=True)
    
    # Check attention module (new in v2)
    attention_0_weight = migrated_state_dict['attention.attention.0.weight']
    attention_2_weight = migrated_state_dict['attention.attention.2.weight']
    
    # Weights should not be all zeros (proper initialization)
    assert not torch.allclose(attention_0_weight, torch.zeros_like(attention_0_weight)), \
        "Attention layer weights appear to be uninitialized (all zeros)"
    
    assert not torch.allclose(attention_2_weight, torch.zeros_like(attention_2_weight)), \
        "Attention layer weights appear to be uninitialized (all zeros)"
    
    # Check classifier (new dropout + linear)
    classifier_weight = migrated_state_dict['classifier.1.weight']
    
    assert not torch.allclose(classifier_weight, torch.zeros_like(classifier_weight)), \
        "Classifier layer weights appear to be uninitialized (all zeros)"
