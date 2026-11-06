"""
Old model architecture (v1) - this is the architecture that was used to create the checkpoint.
The checkpoint was saved with this model structure.
"""
import torch
import torch.nn as nn


class OldModel(nn.Module):
    """
    Original model architecture (v1).
    Used for simple classification task.
    """
    def __init__(self, input_dim=32, hidden_dim=64, num_classes=10):
        super().__init__()
        
        # Simple MLP
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.bn1 = nn.BatchNorm1d(hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim)
        self.bn2 = nn.BatchNorm1d(hidden_dim)
        self.fc3 = nn.Linear(hidden_dim, 32)
        self.output = nn.Linear(32, num_classes)
        
    def forward(self, x):
        x = torch.relu(self.bn1(self.fc1(x)))
        x = torch.relu(self.bn2(self.fc2(x)))
        x = torch.relu(self.fc3(x))
        x = self.output(x)
        return x


def create_old_checkpoint():
    """Create a checkpoint from the old model with some training done."""
    torch.manual_seed(42)
    model = OldModel(input_dim=32, hidden_dim=64, num_classes=10)
    
    # Simulate some training by updating batch norm stats
    model.train()
    for _ in range(50):
        fake_input = torch.randn(16, 32)
        _ = model(fake_input)
    
    model.eval()
    
    # Create optimizer state (Adam)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    
    # Simulate some optimizer steps
    for _ in range(10):
        optimizer.zero_grad()
        fake_input = torch.randn(16, 32)
        fake_target = torch.randint(0, 10, (16,))
        output = model(fake_input)
        loss = nn.CrossEntropyLoss()(output, fake_target)
        loss.backward()
        optimizer.step()
    
    # Save checkpoint with model and optimizer state
    checkpoint = {
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'epoch': 10,
        'version': '1.0',
    }
    
    return checkpoint


if __name__ == '__main__':
    checkpoint = create_old_checkpoint()
    torch.save(checkpoint, 'checkpoint_v1.pt')
    print("Checkpoint saved to checkpoint_v1.pt")
    print("\nModel state dict keys:")
    for key in checkpoint['model_state_dict'].keys():
        shape = checkpoint['model_state_dict'][key].shape
        print(f"  {key}: {shape}")
