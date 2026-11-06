A PyTorch checkpoint from an older model version (v1) needs to be migrated to a new model architecture (v2). The old checkpoint is at `/app/checkpoint_v1.pt` and contains model weights, optimizer state, and training metadata.

The new model architecture is defined in `/app/new_model.py`. The old model architecture is documented in `/app/old_model.py` for reference.

Your task:
1. Analyze the old checkpoint structure and the new model architecture to identify weight mappings between the two versions
2. Create a migration script that:
   - Loads the old checkpoint
   - Maps old layer names to new layer names
   - Properly handles layers that exist in only one version (removed or new)
   - Initializes new layers with appropriate weight initialization
   - Saves the migrated model weights to `/app/migrated_model.pt`
3. Fine-tune the migrated model on the dataset at `/app/dataset.pt` to achieve at least 75% accuracy on the validation split
4. Save the fine-tuned model to `/app/final_model.pt` in TorchScript format

The dataset contains features (shape: N×32) and labels (shape: N) for a 10-class classification task. Use an 80/20 train/validation split.

Success criteria:
- `/app/migrated_model.pt` must contain weights compatible with the NewModel architecture
- `/app/final_model.pt` must be a valid TorchScript model that achieves ≥75% accuracy on the validation set
- The migrated weights from the old checkpoint must be used (not random initialization) for shared layers
