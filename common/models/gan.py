import tensorflow as tf
import tensorflow.keras as keras

from common.core.custom_objects import CustomObject
from common.models import CustomModel
from common.utils import accumulate_train_step

# Interfaces ---------------------------------------------------------------------------------------

class IGanGenerator:
    """
    The interface methods for all GAN models.
    """
    def generate_input(self, batch_size):
        raise NotImplementedError("Must implement generate_input(batch_size)")

class IConditionalGanComponent:
    """
    The interface for the conditional GAN components (generator/discriminator)
    """
    @property
    def gan_num_classes(self):
        raise NotImplementedError("Must implement property gan_num_classes")

# GAN Models ---------------------------------------------------------------------------------------

@CustomObject
class Gan(CustomModel):
    """
    A highly-generalized standard GAN model
    """
    def __init__(
        self,
        generator: IGanGenerator,
        discriminator: IGanGenerator,
        **kwargs
    ):
        super().__init__(**kwargs)
        assert isinstance(generator, IGanGenerator), "Generator must implement IGanComponent"
        self.generator = generator
        self.discriminator = discriminator

    def compile(
        self,
        loss, # Sent to the loss object constructor
        generator_optimizer=None,
        discriminator_optimizer=None,
        generator_metrics=[],
        discriminator_metrics=[],
        force_build=True,
        **kwargs
    ):
        super().compile(**kwargs)
        self.loss_obj = loss
        self.g_optimizer = generator_optimizer
        self.d_optimizer = discriminator_optimizer
        self.g_metrics = generator_metrics
        self.d_metrics = discriminator_metrics
        self.g_loss = keras.metrics.Mean("generator_loss")
        self.d_loss = keras.metrics.Mean("discriminator_loss")
        
        if force_build:
            self.force_build()
    
    def force_build(self):
        # Force build the model and allow saving. Thanks Keras!
        self.discriminator(self(self.generator.generate_input(1)))

    def call(self, inputs, training=None):
        # Only invoke the generator for convenient data logging with W&B
        return self.generator(inputs, training=training)

    @property
    def metrics(self):
        return [self.g_loss, *self.g_metrics, self.d_loss, *self.d_metrics]

    # Training Procedure ---------------------------------------------------------------------------

    def modify_data_for_input(self, data):
        """
        Filter or modify the given input data
        """
        return data

    def batch_size_from_data(self, data):
        """
        Get the batch size from the given data
        """
        return tf.shape(data)[0]

    def generate_generator_input(self, batch_size):
        """
        Generate some input for the generator
        """
        return self.generator.generate_input(batch_size)

    def train_step(self, data):
        """
        A robust and expandable version of the standard GAN training procedure
        """
        # Extract the batch size from the data shape
        batch_size = self.batch_size_from_data(data)

        # Filter the given input
        real_input = self.modify_data_for_input(data)

        # Sample the latent space for the generator
        generator_input = self.generate_generator_input(batch_size)

        with tf.GradientTape() as g_tape, tf.GradientTape() as d_tape:
            fake_data = self.generator(generator_input, training=True)

            real_output = self.discriminator(real_input, training=True)
            fake_output = self.discriminator(fake_data, training=True)

            g_loss, d_loss, metrics = self.compute_metrics(
                data, generator_input, real_output, fake_output)

        # Update gradients
        g_grads = g_tape.gradient(g_loss, self.generator.trainable_variables)
        d_grads = d_tape.gradient(d_loss, self.discriminator.trainable_variables)
        self.g_optimizer.apply_gradients(zip(g_grads, self.generator.trainable_variables))
        self.d_optimizer.apply_gradients(zip(d_grads, self.discriminator.trainable_variables))

        return metrics

    def compute_metrics_for_component(self, y_true, y_pred, loss_fn, loss_metric, metrics):
        """
        Compute the loss and update the metrics for a component model.
        """
        loss = tf.reduce_mean(loss_fn(y_true, y_pred))
        loss_metric.update_state(loss)
        for metric in metrics:
            metric.update_state(y_true, y_pred)
        return loss

    def compute_metrics(self, data, gen_input, real_output, fake_output):
        """
        Compute the loss and update the metrics for the generator and discriminator.
        """
        # Generator
        y_true, y_pred = self.generator_metric_args(gen_input, fake_output)
        g_loss = self.compute_metrics_for_component(
            y_true, y_pred, self.generator_loss, self.g_loss, self.g_metrics)

        # Discriminator
        y_true, y_pred = self.discriminator_metric_args(data, real_output, fake_output)
        d_loss = self.compute_metrics_for_component(
            y_true, y_pred, self.discriminator_loss, self.d_loss, self.d_metrics)

        # Fetch the metric results
        metrics = {
            self.g_loss.name: self.g_loss.result(),
            self.d_loss.name: self.d_loss.result()
        }
        metrics.update({m.name: m.result() for m in self.g_metrics})
        metrics.update({m.name: m.result() for m in self.d_metrics})
        return g_loss, d_loss, metrics

    # Metrics --------------------------------------------------------------------------------------

    def generator_metric_args(self, gen_input, fake_output):
        """
        Compute the y_true and y_pred loss/metric arguments for the generator
        """
        y_true = tf.ones_like(fake_output)
        y_pred = fake_output
        return y_true, y_pred

    def discriminator_metric_args(self, data, real_output, fake_output):
        """
        Compute the y_true and y_pred loss/metric arguments for the discriminator
        """
        y_true = tf.concat((tf.ones_like(real_output), tf.zeros_like(fake_output)), axis=0)
        y_pred = tf.concat((real_output, fake_output), axis=0)
        return y_true, y_pred

    def generator_loss(self, y_true, y_pred):
        """
        Compute the loss for the generator
        """
        batch_size = tf.shape(y_true)[0]
        return self.loss_obj(y_true, y_pred) / tf.cast(batch_size, dtype=tf.float32)

    def discriminator_loss(self, y_true, y_pred):
        """
        Compute the loss for the discriminator
        """
        batch_size = tf.shape(y_true)[0]
        return self.loss_obj(y_true, y_pred) / tf.cast(batch_size, dtype=tf.float32)

    def get_config(self):
        config = super().get_config()
        config.update({
            "generator": self.generator,
            "discriminator": self.discriminator
        })
        return config

@CustomObject
class ConditionalGan(Gan):
    def __init__(
        self,
        generator: IConditionalGanComponent,
        discriminator: IConditionalGanComponent,
        **kwargs
    ):
        super().__init__(generator, discriminator, **kwargs)
        assert isinstance(generator, IConditionalGanComponent), "Generator must implement ConditionalGanComponent"
        assert isinstance(discriminator, IConditionalGanComponent), "Discriminator must implement ConditionalGanComponent"

    def batch_size_from_data(self, data):
        """
        Compute the batch size from the input data
        """
        return tf.shape(data[0])[0]

    def modify_data_for_input(self, data):
        """
        Filter or modify the given input data
        """
        return data[0]

    def generator_metric_args(self, gen_input, fake_output):
        """
        Compute the y_true and y_pred loss/metric arguments for the conditional generator
        """
        y_true = gen_input[-1]
        y_pred = fake_output
        return y_true, y_pred

    def discriminator_metric_args(self, data, real_output, fake_output):
        """
        Compute the y_true and y_pred loss/metric arguments for the conditional discriminator
        """
        batch_size = tf.shape(fake_output)[0]
        y_true = tf.concat((data[-1], tf.fill((batch_size,), self.discriminator.gan_num_classes)), axis=0)
        y_pred = tf.concat((real_output, fake_output), axis=0)
        return y_true, y_pred


@CustomObject
class VeeGan(Gan):
    def __init__(self, generator, discriminator, reconstructor, **kwargs):
        super().__init__(generator, discriminator)
        self.reconstructor = reconstructor

    def compile(
        self,
        loss, # Sent to the loss object constructor
        generator_optimizer=None,
        reconstructor_optimizer=None,
        discriminator_optimizer=None,
        generator_metrics=[],
        discriminator_metrics=[],
        force_build=True,
        **kwargs
    ):
        self.r_optimizer = reconstructor_optimizer
        super().compile(
            loss, # Sent to the loss object constructor
            generator_optimizer,
            discriminator_optimizer,
            generator_metrics,
            discriminator_metrics,
            force_build,
            **kwargs)

    def force_build(self):
        # Force build the model and allow saving. Thanks Keras!
        inp = self.generator.generate_input(1)
        data = self(inp)
        self.discriminator((inp[0], data))
        self.reconstructor(data)
        
    def encode_data(self, data):
        return data
        
    def construct_reconstructor_input(self, fake_input, fake_data):
        """
        Construct the input to the reconstructor model
        """
        return fake_data
    
    def reconstructor_likelihood(self, y_pred):
        """
        Compute the likelihood of the reconstructor output
        """
        return -tf.reduce_mean(tf.reduce_sum(y_pred, axis=1))
    
    def subbatch_train_step(self, data):
        """
        A robust and expandable version of the standard GAN training procedure
        """
        # Extract the batch size from the data shape
        batch_size = self.batch_size_from_data(data)
        
        encoded_data = self.encode_data(data)

        # Filter the given input
        real_data = self.modify_data_for_input(encoded_data)

        # Sample the latent space for the generator
        fake_input = self.generate_generator_input(batch_size)

        with tf.GradientTape() as g_tape, tf.GradientTape() as r_tape, tf.GradientTape() as d_tape:
            fake_data = self.generator(fake_input, training=True)
            real_input = tf.stop_gradient(self.reconstructor(encoded_data, training=True))

            real_output = self.discriminator((real_input, real_data), training=True)
            fake_output = self.discriminator((fake_input[0], fake_data), training=True)
            
            recon_input = self.construct_reconstructor_input(fake_input, fake_data)
            recon_output = self.reconstructor(recon_input, training=True).log_prob(fake_input[0])

            g_loss, r_loss, d_loss = self.compute_metrics(
                data, fake_input, real_input, fake_output, real_output, recon_output)

        # Update gradients
        g_grads = g_tape.gradient(g_loss, self.generator.trainable_variables)
        r_grads = r_tape.gradient(r_loss, self.reconstructor.trainable_variables)
        d_grads = d_tape.gradient(d_loss, self.discriminator.trainable_variables)
        
        return [], [g_grads, r_grads, d_grads]
        
    def train_step(self, data):
        """
        A robust and expandable version of the standard GAN training procedure
        """
        if self.subbatching:
            _, (g_grads, r_grads, d_grads) = accumulate_train_step(
                self.subbatch_train_step, data, self.subbatch_size,
                (self.generator, self.reconstructor, self.discriminator))
        else:
            _, (g_grads, r_grads, d_grads) = self.subbatch_train_step(data)
        self.g_optimizer.apply_gradients(zip(g_grads, self.generator.trainable_variables))
        self.r_optimizer.apply_gradients(zip(r_grads, self.reconstructor.trainable_variables))
        self.d_optimizer.apply_gradients(zip(d_grads, self.discriminator.trainable_variables))
        
        # Fetch the metric results
        metrics = {
            self.g_loss.name: self.g_loss.result(),
            self.d_loss.name: self.d_loss.result()
        }
        metrics.update({m.name: m.result() for m in self.g_metrics})
        metrics.update({m.name: m.result() for m in self.d_metrics})
        
        return metrics
    
    def compute_metrics(self, data, fake_input, real_input, fake_output, real_output, recon_output):
        """
        Compute the loss and update the metrics for the generator and discriminator.
        """
        # Reconstructor
        r_loss = self.reconstructor_likelihood(recon_output)
        
        # Generator
        y_true, y_pred = self.generator_metric_args(fake_input, fake_output)
        g_loss = r_loss + self.compute_metrics_for_component(
            y_true, y_pred, self.generator_loss, self.g_loss, self.g_metrics)

        # Discriminator
        y_true, y_pred = self.discriminator_metric_args(data, real_output, fake_output)
        d_loss = self.compute_metrics_for_component(
            y_true, y_pred, self.discriminator_loss, self.d_loss, self.d_metrics)
        return g_loss, r_loss, d_loss
    
    def get_config(self):
        config = super().get_config()
        config.update({
            "reconstructor": self.reconstructor
        })
        return config
    

@CustomObject
class ConditionalVeeGan(VeeGan):
    
    def force_build(self):
        # Force build the model and allow saving. Thanks Keras!
        inp = self.generator.generate_input(1)
        data = self(inp)
        self.discriminator((inp[0], data))
        self.reconstructor((data, inp[-1]))
    
    def batch_size_from_data(self, data):
        """
        Compute the batch size from the input data
        """
        return tf.shape(data[0])[0]

    def modify_data_for_input(self, data):
        """
        Filter or modify the given input data
        """
        return data[0]
    
    def construct_reconstructor_input(self, fake_input, fake_data):
        """
        Construct the input to the reconstructor model
        """
        return (fake_data, fake_input[-1])

    def generator_metric_args(self, fake_input, fake_output):
        """
        Compute the y_true and y_pred loss/metric arguments for the conditional generator
        """
        y_true = fake_input[-1]
        y_pred = fake_output
        return y_true, y_pred

    def discriminator_metric_args(self, data, real_output, fake_output):
        """
        Compute the y_true and y_pred loss/metric arguments for the conditional discriminator
        """
        batch_size = tf.shape(fake_output)[0]
        y_true = tf.concat((data[-1], tf.fill((batch_size,), self.discriminator.gan_num_classes)), axis=0)
        y_pred = tf.concat((real_output, fake_output), axis=0)
        return y_true, y_pred