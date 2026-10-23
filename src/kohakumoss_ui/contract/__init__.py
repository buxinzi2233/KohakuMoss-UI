"""The only layer that knows trainer-specific names and file layouts."""

from .root import TRAIN_SCRIPT, TrainerRoot, discover

__all__ = ["TRAIN_SCRIPT", "TrainerRoot", "discover"]
