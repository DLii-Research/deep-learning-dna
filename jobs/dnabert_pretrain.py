import datetime
import dotenv
import os
import tensorflow as tf
import tensorflow.keras as keras
import tf_utils as tfu
import sys

import bootstrap
from common.callbacks import LearningRateStepScheduler
from common.data import find_shelves, DnaKmerSequenceGenerator
from common.models.dnabert import DnaBertBase, DnaBertPretrainModel


def define_arguments(parser):
    # Architecture Settings
    parser.add_argument("--length", type=int, default=150)
    parser.add_argument("--kmer", type=int, default=3)
    parser.add_argument("--embed-dim", type=int, default=128)
    parser.add_argument("--stack", type=int, default=8)
    parser.add_argument("--num-heads", type=int, default=4)
    parser.add_argument("--pre-layernorm", type=bool, default=True)
    
    # Training settings
    parser.add_argument("--batches-per-epoch", type=int, default=100)
    parser.add_argument("--val-batches-per-epoch", type=int, default=16)
    parser.add_argument("--data-augment", type=bool, default=True)
    parser.add_argument("--data-balance", type=bool, default=False)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--mask-ratio", type=float, default=0.15)
    parser.add_argument("--optimizer", type=str, choices=["adam", "nadam"], default="adam")
    parser.add_argument("--lr", type=float, default=4e-4)
    parser.add_argument("--init-lr", type=float, default=0.0)
    parser.add_argument("--warmup-steps", type=int, default=None)
    
    
def load_dataset(config, datadir):
    samples = find_shelves(datadir, prepend_path=True)
    dataset = DnaKmerSequenceGenerator(
        samples=samples,
        length=config.length,
        kmer=config.kmer,
        batch_size=config.batch_size,
        batches_per_epoch=config.batches_per_epoch,
        augment=config.data_augment,
        balance=config.data_balance)
    return dataset
    
        
def load_datasets(config):
    datadir = bootstrap.dataset(config)
    assert datadir is not None, "No input data supplied."
    datasets = []
    for folder in ("train", "validation"):
        datasets.append(load_dataset(config, os.path.join(datadir, folder)))
    return datasets

    
def create_model(config):
    dnabert = DnaBertBase(
        length=config.length,
        kmer=config.kmer,
        embed_dim=config.embed_dim,
        stack=config.stack,
        num_heads=config.num_heads,
        pre_layernorm=config.pre_layernorm)
    model = DnaBertPretrainModel(
        dnabert=dnabert,
        length=config.length,
        kmer=config.kmer,
        embed_dim=config.embed_dim,
        stack=config.stack,
        num_heads=config.num_heads,
        mask_ratio=config.mask_ratio)
    
    if config.optimizer == "adam":
        optimizer = keras.optimizers.Adam(config.lr)
    elif config.optimizer == "nadam":
        optimizer = keras.optimizers.Nadam(config.lr)
    
    model.compile(optimizer=optimizer, metrics=[
        keras.metrics.SparseCategoricalAccuracy()
    ])
    return model
    
def train_model(config, train_data, val_data, model, callbacks):
    if config.warmup_steps is not None:
        callbacks.append(LearningRateStepScheduler(
            init_lr = config.init_lr,
            max_lr=config.lr,
            warmup_steps=config.warmup_steps,
            end_steps=config.batches_per_epoch*config.epochs
        ))
    model.fit(
        train_data,
        validation_data=val_data,
        epochs=config.epochs,
        callbacks=callbacks)
    
    
def main(argv):
    
    # Job Information
    job_info = {
        "name": bootstrap.name_timestamped("dnabert-pretrain"),
        "job_type": bootstrap.JobType.Pretrain,
        "group": "dnabert/pretrain"
    }
    
    # Initialize the job and load the config
    config = bootstrap.init(argv, job_info, define_arguments)
        
    # Load the dataset
    train_data, val_data = load_datasets(config)
    
    # Create the autoencoder model
    model = create_model(config)
    
    # Create any collbacks we may need
    callbacks = bootstrap.callbacks()
    
    # Train the model
    bootstrap.run_safely(train_model, config, train_data, val_data, model, callbacks)
    
    # Save the model
    bootstrap.save_model(model)
        
    
if __name__ == "__main__":
    sys.exit(main(sys.argv) or 0)
        