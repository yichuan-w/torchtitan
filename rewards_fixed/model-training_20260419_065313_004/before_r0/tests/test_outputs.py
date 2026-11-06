import os
import torch


def test_model_exists():
    """Test that the trained model file exists."""
    assert os.path.exists('/app/model.pt'), "Model file /app/model.pt does not exist"


def test_model_trained():
    """Test that the model has been trained (weights are not at initialization)."""
    # Load the saved model
    checkpoint = torch.load('/app/model.pt', weights_only=False)
    
    # Reconstruct the model
    from model import TextClassifier
    
    model = TextClassifier(
        vocab_size=checkpoint['vocab_size'],
        embed_dim=checkpoint['embed_dim'],
        num_classes=checkpoint['num_classes'],
    )
    model.load_state_dict(checkpoint['model_state_dict'])
    
    # Check that embedding weights are not zeros (would indicate no training)
    embed_sum = model.embedding.weight.abs().sum().item()
    assert embed_sum > 0.1, f"Model weights appear untrained (embedding sum: {embed_sum})"


def test_weight_tying():
    """Test that weight tying is properly implemented."""
    # Load the saved model
    checkpoint = torch.load('/app/model.pt', weights_only=False)
    
    # Reconstruct the model
    from model import TextClassifier
    
    model = TextClassifier(
        vocab_size=checkpoint['vocab_size'],
        embed_dim=checkpoint['embed_dim'],
        num_classes=checkpoint['num_classes'],
    )
    model.load_state_dict(checkpoint['model_state_dict'])
    
    # Check that embedding and output projection weights share storage
    embed_weight = model.embedding.weight
    output_weight = model.output_proj.weight
    
    # They should point to the same underlying data
    same_storage = embed_weight.data_ptr() == output_weight.data_ptr()
    
    assert same_storage, (
        "Weight tying is not properly implemented: embedding and output_proj "
        "weights do not share the same storage. They should reference the same "
        "tensor, not be copies of each other."
    )


def test_model_file_size():
    """Test that the model file is reasonably sized."""
    file_size = os.path.getsize('/app/model.pt')
    # Model should be less than 10MB
    max_size = 10 * 1024 * 1024
    assert file_size < max_size, f"Model file size {file_size} bytes exceeds {max_size} bytes"
