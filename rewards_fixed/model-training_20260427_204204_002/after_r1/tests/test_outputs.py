import os
import subprocess
import sys


def test_trained_model_exists():
    """Test that the trained model file exists"""
    model_path = "/app/trained_model.pth"
    assert os.path.exists(model_path), f"Trained model file not found at {model_path}"
    
    # Check file is not empty
    file_size = os.path.getsize(model_path)
    assert file_size > 0, "Trained model file is empty"
    print(f"Model file size: {file_size} bytes")


def test_model_accuracy():
    """Test that the trained model achieves at least 80% accuracy"""
    import torch
    import numpy as np
    from pathlib import Path
    
    # Add /app to path so we can import the model
    sys.path.insert(0, '/app')
    from model import SimpleClassifier
    
    # Set device
    device = torch.device("cpu")
    
    # Load the model
    input_dim = 20
    num_classes = 5
    model = SimpleClassifier(input_dim=input_dim, num_classes=num_classes, hidden_dim=128).to(device)
    model_path = Path("/app/trained_model.pth")
    
    assert model_path.exists(), f"Model file not found at {model_path}"
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    
    # Generate validation data with same seed as training
    torch.manual_seed(42)
    np.random.seed(42)
    
    centers = torch.randn(num_classes, input_dim) * 0.8
    samples_per_class = 200  # Total 1000 samples
    
    X_list = []
    y_list = []
    
    for i in range(num_classes):
        X_class = centers[i] + torch.randn(samples_per_class, input_dim) * 1.0
        X_list.append(X_class)
        y_list.append(torch.full((samples_per_class,), i, dtype=torch.long))
    
    X = torch.cat(X_list, dim=0)
    y = torch.cat(y_list, dim=0)
    
    # Use the same split ratio (80/20) but take the val portion
    n_train = int(0.8 * len(y))
    X_val = X[n_train:]
    y_val = y[n_train:]
    
    # Evaluate
    model.eval()
    
    with torch.no_grad():
        outputs = model(X_val)
        _, predicted = outputs.max(1)
        correct = predicted.eq(y_val).sum().item()
        accuracy = 100.0 * correct / len(y_val)
    
    print(f"Validation accuracy: {accuracy:.2f}%")
    
    assert accuracy >= 80.0, f"Model accuracy {accuracy:.2f}% is below the required 80%"
    print(f"SUCCESS: Model achieved {accuracy:.2f}% accuracy (>= 80% required)")


def test_model_can_inference():
    """Test that the model can perform inference on a single sample"""
    import torch
    import sys
    
    sys.path.insert(0, '/app')
    from model import SimpleClassifier
    
    device = torch.device("cpu")
    model = SimpleClassifier(input_dim=20, num_classes=5, hidden_dim=128).to(device)
    model.load_state_dict(torch.load("/app/trained_model.pth", map_location=device, weights_only=True))
    model.eval()
    
    # Create a random input
    dummy_input = torch.randn(1, 20)
    
    with torch.no_grad():
        output = model(dummy_input)
    
    assert output.shape == (1, 5), f"Expected output shape (1, 5), got {output.shape}"
    
    # Check output is valid probability-like (after softmax)
    probs = torch.softmax(output, dim=1)
    assert torch.allclose(probs.sum(), torch.tensor(1.0), atol=1e-5), "Output probabilities don't sum to 1"
    
    print("Model inference test passed")
