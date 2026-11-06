"""
New model architecture (v2) - this is the target architecture.
The solver must migrate the old checkpoint to work with this model.

Key differences from old model:
1. Layer naming convention changed (e.g., fc1 -> layers.input_layer.linear, bn1 -> layers.input_layer.bn)
2. Additional attention mechanism added (new layers)
3. Classifier renamed (fc3 -> layers.hidden_layer, output -> classifier.head)
4. Dropout added between layers
"""
import torch
import torch.nn as nn


class LinearBlock(nn.Module):
    """Linear layer with BatchNorm."""
    def __init__(self, in_features, out_features):
        super().__init__()
        self.linear = nn.Linear(in_features, out_features)
        self.bn = nn.BatchNorm1d(out_features)
        
    def forward(self, x):
        return torch.relu(self.bn(self.linear(x)))


class AttentionBlock(nn.Module):
    """Simple attention mechanism - not present in old model."""
    def __init__(self, features):
        super().__init__()
        self.attention = nn.Sequential(
            nn.Linear(features, features // 4),
            nn.ReLU(),
            nn.Linear(features // 4, features),
            nn.Sigmoid()
        )
    
    def forward(self, x):
        weights = self.attention(x)
        return x * weights


class NewModel(nn.Module):
    """
    New model architecture (v2).
    
    Key changes from v1:
    - Layer naming uses dot notation (e.g., layers.input_layer.linear)
    - Added AttentionBlock module
    - Added Dropout between layers
    - Different class structure
    """
    def __init__(self, input_dim=32, hidden_dim=64, num_classes=10):
        super().__init__()
        
        # Note: fc1/bn1 -> layers.input_layer
        self.layers = nn.ModuleDict({
            'input_layer': LinearBlock(input_dim, hidden_dim),
            'hidden_layer': LinearBlock(hidden_dim, hidden_dim),
            'output_layer': nn.Linear(hidden_dim, 32)
        })
        
        self.attention = AttentionBlock(32)  # NEW: attention mechanism
        
        # Note: output -> classifier.head
        self.classifier = nn.Sequential(
            nn.Dropout(0.3),  # NEW: dropout
            nn.Linear(32, num_classes)
        )
        
    def forward(self, x):
        x = self.layers['input_layer'](x)
        x = self.layers['hidden_layer'](x)
        x = torch.relu(self.layers['output_layer'](x))
        x = self.attention(x)
        x = self.classifier(x)
        return x


if __name__ == '__main__':
    model = NewModel(input_dim=32, hidden_dim=64, num_classes=10)
    print("New model state dict keys:")
    for key in model.state_dict().keys():
        shape = model.state_dict()[key].shape
        print(f"  {key}: {shape}")
