import os
import tensorflow as tf
import tensorflow.keras as keras
import sys

import bootstrap
from common.data import find_dbs, DnaLabelType, DnaSequenceGenerator
from common.models import dnabert
from common.utils import str_to_bool


def define_arguments(parser):
    # Pretrained model
    parser.add_argument("--pretrained-model-artifact", type=str, default=None)

    # Architecture settings
    parser.add_argument("--embed-dim", type=int, default=128)
    parser.add_argument("--stack", type=int, default=4)
    parser.add_argument("--num-heads", type=int, default=4)
    parser.add_argument("--pre-layernorm", type=str_to_bool, default=True)

    # Training settings
    parser.add_argument("--batches-per-epoch", type=int, default=100)
    parser.add_argument("--val-batches-per-epoch", type=int, default=16)
    parser.add_argument("--data-augment", type=str_to_bool, default=True)
    parser.add_argument("--data-balance", type=str_to_bool, default=False)
    parser.add_argument("--data-workers", type=int, default=1)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--sub-batch-size", type=int, default=1000)
    parser.add_argument("--optimizer", type=str, choices=["adam", "nadam"], default="adam")
    parser.add_argument("--lr", type=float, default=4e-4)


def load_dataset(config, datadir, length, kmer):
    samples = find_dbs(datadir)
    dataset = DnaSequenceGenerator(
        samples=samples,
        sequence_length=length,
        kmer=kmer,
        batch_size=config.batch_size,
        batches_per_epoch=config.batches_per_epoch,
        augment=config.data_augment,
        balance=config.data_balance,
        labels=DnaLabelType.OneMer,
        rng=bootstrap.rng())
    return dataset


def load_datasets(config, length, kmer):
    datadir = bootstrap.use_dataset(config)
    datasets = []
    for folder in ("train", "validation"):
        datasets.append(load_dataset(config, os.path.join(datadir, folder), length, kmer))
    return datasets


def create_model(config):
    # Fetch the pretrained DNABERT model
    pretrain_path = bootstrap.use_model(config.pretrained_model_artifact)

    # Create the model
    base = dnabert.DnaBertPretrainModel.load(pretrain_path).base
    encoder = dnabert.DnaBertEncoderModel(base)
    decoder = dnabert.DnaBertDecoderModel(
        length=base.length,
        embed_dim=config.embed_dim,
        stack=config.stack,
        num_heads=config.num_heads,
        latent_dim=base.embed_dim,
        pre_layernorm=config.pre_layernorm)
    model = dnabert.DnaBertAutoencoderModel(encoder, decoder)

    # Select an optimizer
    if config.optimizer == "adam":
        optimizer = keras.optimizers.Adam(config.lr)
    elif config.optimizer == "nadam":
        optimizer = keras.optimizers.Nadam(config.lr)

    # Compile and return the model
    model.compile(optimizer=optimizer, metrics=[
        keras.metrics.SparseCategoricalAccuracy()
    ])
    model(tf.zeros((1, encoder.base.length - encoder.base.kmer + 1)))
    model.summary()
    return model


def load_model(path):
    print("Loading previous model...")
    return dnabert.DnaBertAutoencoderModel.load(path)


def train(config, model_path=None):
    with bootstrap.strategy().scope():
        # Create the autoencoder model
        if model_path is not None:
            model = load_model(model_path)
        else:
            model = create_model(config)

        # Load the dataset using the base DNABERT model parameters
        length = model.encoder.base.length
        kmer = model.encoder.base.kmer
        train_data, val_data = load_datasets(config, length, kmer)

        # Create any collbacks we may need
        callbacks = bootstrap.callbacks({})

        # Train the model with keyboard-interrupt protection
        bootstrap.run_safely(
            model.fit,
            train_data,
            validation_data=val_data,
            initial_epoch=bootstrap.initial_epoch(),
            subbatch_size=config.sub_batch_size,
            epochs=config.epochs,
            callbacks=callbacks,
            use_multiprocessing=(config.data_workers > 1),
            workers=config.data_workers)

        # Save the model
        bootstrap.save_model(model)

    return model


def main(argv):
    # Job Information
    job_info = {
        "name": "finetune",
        "job_type": bootstrap.JobType.Finetune,
        "project": os.environ["WANDB_PROJECT_DNABERT_AUTOENCODER"],
        "group": "finetune"
    }

    # Initialize the job and load the config
    job_config, config = bootstrap.init(argv, job_info, define_arguments)

    # If this is a resumed run, we need to fetch the latest model run
    model_path = None
    if bootstrap.is_resumed():
        print("Restoring previous model...")
        model_path = bootstrap.restore_dir(config.save_to)

    # Train the model if necessary
    if bootstrap.initial_epoch() < config.epochs:
        train(config, model_path)
    else:
        print("Skipping training...")

    # Upload an artifact of the model if requested
    if job_config.log_artifacts:
        bootstrap.log_model_artifact(bootstrap.group().replace('/', '-'))


if __name__ == "__main__":
    sys.exit(bootstrap.boot(main, (sys.argv,)) or 0)
