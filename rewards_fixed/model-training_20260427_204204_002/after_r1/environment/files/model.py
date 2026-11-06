import torch
import torch.nn as nn
import torch.nn.functional as F


class SimpleClassifier(nn.Module):
    """
    Simple MLP for classification.
    Expected accuracy: 80%+ with proper training.
    """
    def __init__(self, input_dim=20, hidden_dim=64, num_classes=3):
        super().__init__()
        
        # Simple MLP layers
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.bn1 = nn.BatchNorm1d(hidden_dim, track_running_stats=False)
        self.fc2 = nn.Linear(hidden_dim, 32)
        self.bn2 = nn.BatchNorm1d(32, track_running_stats=False)
        self.fc3 = nn.Linear(32, num_classes)
    
    def forward(self, x):
        # First layer
        x = self.fc1(x)
        x = self.bn1(x)
        x = F.relu(x)
        
        # Second layer
        x = self.fc2(x)
        x = self.bn2(x)
        x = F.relu(x)
        
        # Output layer
        x = self.fc3(x)
        
        return x
