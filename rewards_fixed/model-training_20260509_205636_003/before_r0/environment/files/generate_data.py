"""
Script to generate the checkpoint file and dataset.
This runs during Docker build to create the initial state.
"""
import torch
import torch.nn as nn
import sys

# Add current directory to path
sys.path.insert(0, '/app')

from old_model import OldModel, create_old_checkpoint
from new_model import NewModel


def generate_synthetic_dataset(num_samples=500, input_dim=32, num_classes=10):
    """Generate a synthetic classification dataset with overlapping class regions."""
    torch.manual_seed(12345)
    
    # Generate random features with class-specific patterns
    features = []
    labels = []
    
    for i in range(num_samples):
        label = i % num_classes
        
        # Base features with more noise
        x = torch.randn(input_dim) * 0.8
        
        # Add weaker class-specific signal (harder to learn)
        x[label * 2:(label + 1) * 2] += 1.5
        # Add some overlap by using adjacent dimensions
        x[(label * 2 + 1) % input_dim] += 0.5
        
        features.append(x)
        labels.append(label)
    
    features = torch.stack(features)
    labels = torch.tensor(labels)
    
    # Shuffle
    perm = torch.randperm(num_samples)
    features = features[perm]
    labels = labels[perm]
    
    return {
        'features': features,
        'labels': labels,
        'num_classes': num_classes,
    }


def main():
    print("Generating old model checkpoint...")
    checkpoint = create_old_checkpoint()
    torch.save(checkpoint, '/app/checkpoint_v1.pt')
    
    print("\nOld model state dict keys:")
    for key in checkpoint['model_state_dict'].keys():
        shape = checkpoint['model_state_dict'][key].shape
        print(f"  {key}: {shape}")
    
    print("\nGenerating synthetic dataset...")
    dataset = generate_synthetic_dataset(num_samples=500, input_dim=32, num_classes=10)
    torch.save(dataset, '/app/dataset.pt')
    print(f"Dataset shape: features={dataset['features'].shape}, labels={dataset['labels'].shape}")
    
    # Also save the new model architecture info (for reference)
    new_model = NewModel(input_dim=32, hidden_dim=64, num_classes=10)
    print("\nNew model state dict keys:")
    for key in new_model.state_dict().keys():
        shape = new_model.state_dict()[key].shape
        print(f"  {key}: {shape}")
    
    # Count which keys exist in old vs new
    old_keys = set(checkpoint['model_state_dict'].keys())
    new_keys = set(new_model.state_dict().keys())
    
    print(f"\nKeys only in old model: {old_keys - new_keys}")
    print(f"Keys only in new model: {new_keys - old_keys}")
    
    # Save key mappings hint (but not the solution!)
    with open('/app/migration_notes.txt', 'w') as f:
        f.write("Migration Notes (v1 -> v2)\n")
        f.write("=" * 50 + "\n\n")
        f.write("Architecture Changes:\n")
        f.write("- Layer naming convention changed to dot notation\n")
        f.write("- AttentionBlock module added (new)\n")
        f.write("- Dropout added in classifier\n")
        f.write("\n")
        f.write("Old checkpoint contains:\n")
        f.write("- model_state_dict: model weights\n")
        f.write("- optimizer_state_dict: Adam optimizer state\n")
        f.write("- epoch: training epoch number\n")
        f.write("- version: checkpoint version\n")
    
    print("\nFiles created successfully!")
    print("  - /app/checkpoint_v1.pt")
    print("  - /app/dataset.pt")
    print("  - /app/migration_notes.txt")


if __name__ == '__main__':
    main()
