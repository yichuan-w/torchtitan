import os
import sys

import torch


def _same_parameter(model):
    return (
        model.embedding.weight is model.output_proj.weight
        and model.embedding.weight.data_ptr() == model.output_proj.weight.data_ptr()
    )


def test_model_exists():
    """The requested trained artifact exists at the public path."""
    assert os.path.exists('/app/model.pt'), "Model file /app/model.pt does not exist"


def test_model_trained():
    """Retain the inherited trained-artifact check at its original strictness."""
    checkpoint = torch.load('/app/model.pt', weights_only=False)
    from model import TextClassifier

    model = TextClassifier(
        vocab_size=checkpoint['vocab_size'],
        embed_dim=checkpoint['embed_dim'],
        num_classes=checkpoint['num_classes'],
    )
    model.load_state_dict(checkpoint['model_state_dict'])
    embed_sum = model.embedding.weight.abs().sum().item()
    warm_start = torch.load('/app/warm_start.pt', weights_only=False)
    warm_embedding = warm_start['model_state_dict']['embedding.weight']
    trained_embedding = checkpoint['model_state_dict']['embedding.weight']
    assert embed_sum > 0.1 and not torch.equal(trained_embedding, warm_embedding), (
        f"Model weights appear untrained (embedding sum: {embed_sum})"
    )


def test_saved_model_uses_one_parameter():
    """The saved model reconstructs with one shared Parameter, not an alias."""
    checkpoint = torch.load('/app/model.pt', weights_only=False)
    from model import TextClassifier

    model = TextClassifier(
        vocab_size=checkpoint['vocab_size'],
        embed_dim=checkpoint['embed_dim'],
        num_classes=checkpoint['num_classes'],
    )
    model.load_state_dict(checkpoint['model_state_dict'])
    assert _same_parameter(model), "Saved model does not use one shared Parameter"


def test_identity_survives_initialization_checkpoint_and_training(tmp_path, monkeypatch):
    """Exercise the shipped warm-start and optimizer workflow at each named stage."""
    import train
    from model import TextClassifier

    initialized = TextClassifier(50, 16, 4)
    assert _same_parameter(initialized), "Weights are not one Parameter after initialization"

    models = []
    load_observations = []
    step_observations = []
    original_adam = train.optim.Adam

    def recording_factory(*args, **kwargs):
        model = TextClassifier(*args, **kwargs)
        models.append(model)
        return model

    class RecordingAdam(original_adam):
        def __init__(self, *args, **kwargs):
            load_observations.append(_same_parameter(models[-1]))
            super().__init__(*args, **kwargs)

        def step(self, *args, **kwargs):
            step_observations.append(_same_parameter(models[-1]))
            result = super().step(*args, **kwargs)
            step_observations.append(_same_parameter(models[-1]))
            return result

    monkeypatch.setattr(train, 'TextClassifier', recording_factory)
    monkeypatch.setattr(train.optim, 'Adam', RecordingAdam)
    monkeypatch.setattr(sys, 'argv', [
        'train.py', '--epochs', '1', '--output_path', str(tmp_path / 'model.pt')
    ])
    train.main()

    assert load_observations and all(load_observations), (
        "The same Parameter does not survive checkpoint loading"
    )
    assert step_observations and all(step_observations), (
        "The same Parameter does not survive optimizer training steps"
    )
