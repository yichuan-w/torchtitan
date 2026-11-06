import torch
import torch.nn as nn


class TextClassifier(nn.Module):
    """
    A simple text classifier with weight tying.
    
    The embedding layer and the output projection layer share weights.
    This is a common technique in language models to reduce parameters.
    """
    
    def __init__(self, vocab_size, embed_dim, num_classes, padding_idx=0):
        super().__init__()
        
        self.vocab_size = vocab_size
        self.embed_dim = embed_dim
        self.num_classes = num_classes
        
        # Embedding layer: shape (vocab_size, embed_dim)
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=padding_idx)
        
        # Output projection: for weight tying with embedding, 
        # we use Linear(embed_dim, vocab_size, bias=False)
        # Weight shape: (vocab_size, embed_dim) - same as embedding!
        self.output_proj = nn.Linear(embed_dim, vocab_size, bias=False)
        
        # Final classifier
        self.classifier = nn.Linear(vocab_size, num_classes)
        
        # Apply weight tying
        self._tie_weights()
        
        self._init_weights()
    
    def _tie_weights(self):
        """Tie output projection weights to embedding weights."""
        self.output_proj.weight.data = self.embedding.weight.data.clone()
    
    def _init_weights(self):
        """Initialize classifier weights."""
        nn.init.xavier_uniform_(self.classifier.weight)
        nn.init.zeros_(self.classifier.bias)
    
    def forward(self, input_ids, attention_mask=None):
        """Forward pass."""
        # Embedding lookup
        embeds = self.embedding(input_ids)  # [batch, seq, embed_dim]
        
        # Mean pooling with mask
        if attention_mask is not None:
            mask = attention_mask.unsqueeze(-1).float()
            embeds = (embeds * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
        else:
            embeds = embeds.mean(dim=1)
        
        # Project to vocabulary space using tied weights
        vocab_logits = self.output_proj(embeds)  # [batch, vocab_size]
        
        # Final classification
        return self.classifier(vocab_logits)
    
    def get_num_parameters(self):
        return sum(p.numel() for p in self.parameters())
