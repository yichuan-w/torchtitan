#!/usr/bin/env python3
"""
Training script for text classifier.
"""

import argparse
import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from model import TextClassifier


def load_data(data_path):
    """Load preprocessed data from disk."""
    data = torch.load(data_path, weights_only=False)
    return (
        data['train_input_ids'],
        data['train_attention_mask'],
        data['train_labels'],
        data['val_input_ids'],
        data['val_attention_mask'],
        data['val_labels'],
        data['vocab_size'],
        data['num_classes'],
    )


def train_epoch(model, dataloader, optimizer, criterion):
    """Train for one epoch."""
    model.train()
    total_loss = 0
    correct = 0
    total = 0
    
    for batch_input_ids, batch_attention_mask, batch_labels in dataloader:
        optimizer.zero_grad()
        logits = model(batch_input_ids, batch_attention_mask)
        loss = criterion(logits, batch_labels)
        loss.backward()
        optimizer.step()
        
        total_loss += loss.item() * batch_input_ids.size(0)
        _, predicted = logits.max(1)
        correct += predicted.eq(batch_labels).sum().item()
        total += batch_labels.size(0)
    
    return total_loss / total, correct / total


def evaluate(model, input_ids, attention_mask, labels):
    """Evaluate the model."""
    model.eval()
    with torch.no_grad():
        logits = model(input_ids, attention_mask)
        _, predicted = logits.max(1)
        correct = predicted.eq(labels).sum().item()
        total = labels.size(0)
    return correct / total


def check_weight_tying(model):
    """Check if weight tying is working correctly."""
    embed_weight = model.embedding.weight
    output_weight = model.output_proj.weight
    same_storage = embed_weight.data_ptr() == output_weight.data_ptr()
    return same_storage


def main():
    parser = argparse.ArgumentParser(description='Train text classifier')
    parser.add_argument('--data_path', type=str, default='/app/data.pt')
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--embed_dim', type=int, default=16)
    parser.add_argument('--output_path', type=str, default='/app/model.pt')
    args = parser.parse_args()
    
    # Load data
    print('Loading data...')
    (train_input_ids, train_attention_mask, train_labels,
     val_input_ids, val_attention_mask, val_labels,
     vocab_size, num_classes) = load_data(args.data_path)
    
    print(f'Vocabulary size: {vocab_size}')
    print(f'Number of classes: {num_classes}')
    print(f'Training samples: {len(train_labels)}')
    print(f'Validation samples: {len(val_labels)}')
    
    # Create dataloader
    train_dataset = TensorDataset(train_input_ids, train_attention_mask, train_labels)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    
    # Create model
    model = TextClassifier(vocab_size, args.embed_dim, num_classes)

    # Warm-start using assign mode to avoid an extra copy of checkpoint tensors.
    warm_start = torch.load('/app/warm_start.pt', weights_only=False)
    model.load_state_dict(warm_start['model_state_dict'], assign=True)
    
    # Check initial weight tying
    print(f'Initial weight tying - same storage: {check_weight_tying(model)}')
    
    # Count parameters
    print(f'Total parameters: {model.get_num_parameters():,}')
    
    # Train
    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    criterion = nn.CrossEntropyLoss()
    
    best_val_acc = 0
    print('\nStarting training...')
    
    for epoch in range(args.epochs):
        train_loss, train_acc = train_epoch(model, train_loader, optimizer, criterion)
        val_acc = evaluate(model, val_input_ids, val_attention_mask, val_labels)
        
        print(f'Epoch {epoch+1}/{args.epochs} - Train Loss: {train_loss:.4f}, Train Acc: {train_acc:.4f}, Val Acc: {val_acc:.4f}')
        
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save({
                'model_state_dict': model.state_dict(),
                'vocab_size': vocab_size,
                'num_classes': num_classes,
                'embed_dim': args.embed_dim,
            }, args.output_path)
            print(f'  Saved best model with val_acc: {val_acc:.4f}')
    
    print(f'\nFinal weight tying - same storage: {check_weight_tying(model)}')
    print(f'Best validation accuracy: {best_val_acc:.4f}')
    print(f'Model saved to: {args.output_path}')
    
    return best_val_acc


if __name__ == '__main__':
    main()
