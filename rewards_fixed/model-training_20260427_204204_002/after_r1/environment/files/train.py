import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from model import SimpleClassifier


def generate_synthetic_data(n_samples=1000, input_dim=20, num_classes=5, seed=42):
    """Generate synthetic classification data with overlapping clusters"""
    torch.manual_seed(seed)
    np.random.seed(seed)
    
    # Create cluster centers for each class
    centers = torch.randn(num_classes, input_dim) * 0.8
    
    # Generate samples around each center
    samples_per_class = n_samples // num_classes
    X_list = []
    y_list = []
    
    for i in range(num_classes):
        X_class = centers[i] + torch.randn(samples_per_class, input_dim) * 1.0
        X_list.append(X_class)
        y_list.append(torch.full((samples_per_class,), i, dtype=torch.long))
    
    X = torch.cat(X_list, dim=0)
    y = torch.cat(y_list, dim=0)
    
    # Shuffle
    perm = torch.randperm(len(y))
    X = X[perm]
    y = y[perm]
    
    # Split train/val
    n_train = int(0.8 * len(y))
    X_train, X_val = X[:n_train], X[n_train:]
    y_train, y_val = y[:n_train], y[n_train:]
    
    return X_train, y_train, X_val, y_val


def compute_accuracy(model, X, y):
    """Compute classification accuracy"""
    model.eval()
    with torch.no_grad():
        outputs = model(X)
        _, predicted = outputs.max(1)
        correct = predicted.eq(y).sum().item()
        return 100.0 * correct / len(y)


class Trainer:
    """Training utility class for neural network training"""
    
    def __init__(self, model, criterion, optimizer, scheduler=None, device='cpu'):
        self.model = model
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.device = device
        self.best_accuracy = 0.0
    
    def train_step(self, X_batch, y_batch):
        """Perform a single training step"""
        # Forward pass
        with torch.set_grad_enabled(True):
            outputs = self.model(X_batch)
            loss = self.criterion(outputs, y_batch)
        
        # Backward pass
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
        
        return loss.item()
    
    def train_epoch(self, X_train, y_train, batch_size):
        """Train for one epoch"""
        self.model.eval()  # Set consistent state
        
        # Shuffle training data
        perm = torch.randperm(len(y_train))
        X_shuffled = X_train[perm]
        y_shuffled = y_train[perm]
        
        running_loss = 0.0
        
        for i in range(0, len(y_train), batch_size):
            X_batch = X_shuffled[i:i+batch_size]
            y_batch = y_shuffled[i:i+batch_size]
            
            loss = self.train_step(X_batch, y_batch)
            running_loss += loss
        
        return running_loss / (len(y_train) // batch_size)
    
    def validate(self, X_val, y_val):
        """Run validation and return accuracy"""
        return compute_accuracy(self.model, X_val, y_val)
    
    def fit(self, X_train, y_train, X_val, y_val, num_epochs, batch_size):
        """Full training loop"""
        print("\nStarting training...")
        
        for epoch in range(num_epochs):
            train_loss = self.train_epoch(X_train, y_train, batch_size)
            val_accuracy = self.validate(X_val, y_val)
            
            if self.scheduler:
                self.scheduler.step()
            
            print(f"Epoch [{epoch+1}/{num_epochs}] Loss: {train_loss:.4f} Acc: {val_accuracy:.2f}%")
            self.best_accuracy = max(self.best_accuracy, val_accuracy)
        
        return self.best_accuracy


def main():
    # Hyperparameters
    num_epochs = 10
    learning_rate = 0.001
    batch_size = 32
    input_dim = 20
    num_classes = 5
    
    # Device setup
    device = torch.device("cpu")
    print(f"Using device: {device}")
    
    # Generate data
    print("Generating synthetic data...")
    X_train, y_train, X_val, y_val = generate_synthetic_data(
        n_samples=1000, input_dim=input_dim, num_classes=num_classes
    )
    print(f"Train samples: {len(y_train)}, Val samples: {len(y_val)}")
    
    # Initialize model
    model = SimpleClassifier(input_dim=input_dim, num_classes=num_classes, hidden_dim=128)
    print(f"Model created with {sum(p.numel() for p in model.parameters()):,} parameters")
    
    # Loss and optimizer
    criterion = nn.CrossEntropyLoss()
    
    # Get weight matrices for optimizer (exclude biases for memory efficiency)
    weight_params = [p for p in model.parameters() if p.ndim == 2]
    optimizer = optim.SGD(weight_params, lr=learning_rate, momentum=0.9)
    
    # Learning rate scheduler
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=3, gamma=0.1)
    
    # Create trainer
    trainer = Trainer(model, criterion, optimizer, scheduler, device)
    
    # Train the model
    best_accuracy = trainer.fit(X_train, y_train, X_val, y_val, num_epochs, batch_size)
    
    print(f"\nBest validation accuracy: {best_accuracy:.2f}%")
    
    # Save the trained model
    torch.save(model.state_dict(), '/app/trained_model.pth')
    print(f"Model saved to /app/trained_model.pth")


if __name__ == "__main__":
    main()
