#!/usr/bin/env python3
"""
Prepare training data for the text classifier.

This script creates a synthetic sentiment analysis dataset for testing.
"""

import os
import random

import torch


def generate_synthetic_data(vocab_size=50, max_seq_len=10, num_train=300, num_val=100, num_classes=4):
    """Generate synthetic sentiment analysis data."""
    random.seed(42)
    torch.manual_seed(42)
    
    # Generate random sequences with deterministic labels
    def generate_samples(num_samples, offset=0):
        input_ids_list = []
        attention_mask_list = []
        labels_list = []
        
        for i in range(num_samples):
            seq_len = random.randint(3, max_seq_len)
            
            # Generate input IDs (avoid padding token 0)
            input_ids = [random.randint(1, vocab_size - 1) for _ in range(seq_len)]
            attention_mask = [1] * seq_len
            
            # Pad to max_seq_len
            padding_length = max_seq_len - seq_len
            input_ids = input_ids + [0] * padding_length
            attention_mask = attention_mask + [0] * padding_length
            
            input_ids_list.append(input_ids)
            attention_mask_list.append(attention_mask)
            
            # Create a deterministic label that correlates with input features
            # Use sum of first 3 tokens mod num_classes for learnability
            # This creates a learnable pattern from the input
            label = sum(input_ids[:3]) % num_classes
            labels_list.append(label)
        
        return (
            torch.tensor(input_ids_list, dtype=torch.long),
            torch.tensor(attention_mask_list, dtype=torch.long),
            torch.tensor(labels_list, dtype=torch.long),
        )
    
    train_input_ids, train_attention_mask, train_labels = generate_samples(num_train)
    val_input_ids, val_attention_mask, val_labels = generate_samples(num_val, offset=num_train)
    
    print(f'Generated {num_train} training samples')
    print(f'Generated {num_val} validation samples')
    print(f'Vocabulary size: {vocab_size}')
    print(f'Number of classes: {num_classes}')
    
    return {
        'train_input_ids': train_input_ids,
        'train_attention_mask': train_attention_mask,
        'train_labels': train_labels,
        'val_input_ids': val_input_ids,
        'val_attention_mask': val_attention_mask,
        'val_labels': val_labels,
        'vocab_size': vocab_size,
        'num_classes': num_classes,
    }


def main():
    data = generate_synthetic_data()
    
    os.makedirs('/app', exist_ok=True)
    torch.save(data, '/app/data.pt')
    print('Data saved to /app/data.pt')


if __name__ == '__main__':
    main()
