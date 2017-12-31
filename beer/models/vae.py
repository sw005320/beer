
"""Implementation of the Variational Auto-Encoder with arbitrary
prior over the latent space.

"""

import abc
import math

import torch
from torch import nn
from torch.autograd import Variable
from torch import optim
import numpy as np

from .model import Model
from ..priors import NormalGammaPrior


class VAE(nn.Module, Model):
    """Variational Auto-Encoder (VAE)."""

    def __init__(self, encoder, decoder, latent_model, nsamples):
        """Initialize the VAE.

        Args:
            encoder (``MLPModel``): Encoder of the VAE.
            decoder (``MLPModel``): Decoder of the VAE.
            latent_model(``ConjugateExponentialModel``): Bayesian Model
                for the prior over the latent space.
            nsamples (int): Number of samples to approximate the
                expectation of the log-likelihood.

        """
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.latent_model = latent_model
        self.nsamples = nsamples

    def _fit_step(self, mini_batch):
        # Number of samples in the mini-batch
        mini_batch_size = np.prod(mini_batch.shape[:-1])

        # Total number of samples of the training data.
        data_size = self._fit_cache['data_size']

        # Scale of the sufficient statistics.
        scale = float(data_size) / mini_batch_size

        # Convert the data into the suitable pytorch Variable
        X = Variable(torch.from_numpy(mini_batch).float())

        # Clean up the previously accumulated gradient.
        self._fit_cache['optimizer'].zero_grad()

        # Forward the data through the VAE.
        state = self(X, self._fit_cache['sample'])

        # Compute the loss (negative ELBO).
        loss, llh, kld = self.loss(X, state,
                                   kl_weight=self._fit_cache['kl_weight'])
        loss, llh, kld = loss.sum(), llh.sum(), kld.sum()

        # We normalize the loss so we don't have to tune the learning rate
        # depending on the batch size.
        loss /= float(mini_batch_size)

        # Backward propagation of the gradient.
        loss.backward()

        # Update of the parameters of the neural network part of the
        # model.
        self._fit_cache['optimizer'].step()

        # Natural gradient step of the latent model.
        self.latent_model.natural_grad_update(state['acc_stats'],
            scale=scale, lrate=self._fit_cache['latent_model_lrate'])

        # Full elbo (including the KL div. of the latent model).
        latent_model_kl = self.latent_model.kl_div_posterior_prior() / data_size
        elbo = -loss.data.numpy()[0] - latent_model_kl

        return elbo, llh.data.numpy()[0] / mini_batch_size, \
            kld.data.numpy()[0] / mini_batch_size + latent_model_kl

    def fit(self, data, mini_batch_size=-1, max_epochs=1, seed=None, lrate=1e-3,
            latent_model_lrate=1., kl_weight=1.0, sample=True, callback=None):
        self._fit_cache = {
            'optimizer':optim.Adam(self.parameters(), lr=lrate,
                                   weight_decay=1e-6),
            'latent_model_lrate': latent_model_lrate,
            'data_size': np.prod(data.shape[:-1]),
            'kl_weight': kl_weight,
            'sample': sample
        }
        super().fit(data, mini_batch_size, max_epochs, seed, callback)

    def evaluate(self, data, sampling=True):
        'Convenience function mostly for plotting and debugging.'
        torch_data = Variable(torch.from_numpy(data).float())
        state = self(torch_data, sampling=sampling)
        loss, llh, kld = self.loss(torch_data, state)
        return -loss.data.numpy(), llh.data.numpy(), kld.data.numpy(), \
            state['encoder_state'].mean.data.numpy(), \
            state['encoder_state'].std_dev().data.numpy()**2

    def forward(self, X, sampling=True):
        '''Forward data through the VAE model.

        Args:
            x (torch.Variable): Data to process. The first dimension is
                the number of samples and the second dimension is the
                dimension of the latent space.
            sampling (boolearn): If True, sample to approximate the
                expectation of the log-likelihood.

        Returns:
            dict: State of the VAE.

        '''
        # Forward the data through the encoder.
        encoder_state = self.encoder(X)

        # Forward the statistics to the latent model.
        p_np_params, acc_stats = self.latent_model.expected_natural_params(
                encoder_state.mean.data.numpy(),
                (1/encoder_state.prec).data.numpy())

        # Samples of the latent variable using the reparameterization
        # "trick". "z" is a L x N x K tensor where L is the number of
        # samples for the reparameterization "trick", N is the number
        # of frames and K is the dimension of the latent space.
        if sampling:
            nsamples = self.nsamples
            samples = []
            for i in range(self.nsamples):
                samples.append(encoder_state.sample())
            samples = torch.stack(samples).view(self.nsamples * X.size(0), -1)
            decoder_state = self.decoder(samples)
        else:
            nsamples = 1
            decoder_state = self.decoder(encoder_state.mean)

        return {
            'encoder_state': encoder_state,
            'p_np_params': Variable(torch.FloatTensor(p_np_params)),
            'acc_stats': acc_stats,
            'decoder_state': decoder_state,
            'nsamples': nsamples
        }

    def loss(self, X, state, kl_weight=1.0):
        """Loss function of the VAE. This is the negative of the
        variational objective function i.e.:

            loss = - ( E_q [ ln p(X|Z) ] - KL( q(z) || p(z) ) )

        Args:
            X (torch.Variable): Data on which to estimate the loss.
            state (dict): State of the VAE after forwarding the data
                through the network.
            kl_weight (float): Weight of the KL divergence in the loss.
                You probably don't want to touch it unless you know
                what you are doing.

        Returns:
            torch.Variable: Symbolic computation of the loss function.

        """
        nsamples = state['nsamples']
        llh = state['decoder_state'].log_likelihood(X, state['nsamples'])
        llh = llh.view(nsamples, X.size(0), -1).sum(dim=0) / nsamples
        kl = state['encoder_state'].kl_div(state['p_np_params'])
        kl *= kl_weight

        return -(llh - kl[:, None]), llh, kl


class MLPEncoderState(metaclass=abc.ABCMeta):
    'Abstract Base Class for the state of a the VAE encoder.'

    @property
    def mean(self):
        'Mean of each distribution.'
        return self._mean

    @property
    def prec(self):
        'Diagonal of the precision matrix for each distribution.'
        return self._prec

    @abc.abstractmethod
    def sample(self):
        'sample data using the reparametization trick.'
        NotImplemented

    @abc.abstractmethod
    def kl_div(self, p_nparams):
        'kl divergence between the posterior and prior distribution.'
        NotImplemented


class MLPDecoderState(metaclass=abc.ABCMeta):
    'Abstract Base Class for the state of a the VAE decoder.'

    @abc.abstractmethod
    def natural_params(self):
        'Natural parameters for each distribution.'
        NotImplemented

    @abc.abstractmethod
    def log_base_measure(self, X):
        'Natural parameters for each distribution.'
        NotImplemented

    @abc.abstractmethod
    def sufficient_statistics(self, X):
        'Sufficient statistics of the given data.'
        NotImplemented

    def log_likelihood(self, X, nsamples=1):
        'Log-likelihood of the data.'
        s_stats = self.sufficient_statistics(X)
        nparams = self.natural_params()
        log_bmeasure = self.log_base_measure(X)
        nparams = nparams.view(nsamples, X.size(0), -1)
        return torch.sum(nparams * s_stats, dim=-1) + log_bmeasure


class MLPStateNormal(MLPEncoderState, MLPDecoderState):

    def __init__(self, mean, prec):
        self._mean = mean
        self._prec = prec

    def exp_T(self):
        idxs = torch.arange(0, self.mean.size(1)).long()
        XX = self.mean[:, :, None] * self.mean[:, None, :]
        XX[:, idxs, idxs] += 1 / self.prec
        return torch.cat([XX.view(self.mean.size(0), -1), self.mean,
                          Variable(torch.ones(self.mean.size(0), 2))], dim=-1)

    def std_dev(self):
        return 1 / torch.sqrt(self.prec)

    def natural_params(self):
        identity = Variable(torch.eye(self.mean.size(1)))
        np1 = -.5 * self.prec[:, None] * identity[None, :, :]
        np1 = np1.view(self.mean.size(0), -1)
        np2 = self.prec * self.mean
        np3 = -.5 * (self.prec * (self.mean ** 2)).sum(-1)[:, None]
        np4 = .5 * torch.log(self.prec).sum(-1)[:, None]
        return torch.cat([np1, np2, np3, np4], dim=-1)

    def sample(self):
        noise = Variable(torch.randn(*self.mean.size()))
        return self.mean + self.std_dev() * noise

    def kl_div(self, p_nparams):
        return ((self.natural_params() - p_nparams) * self.exp_T()).sum(dim=-1)

    def sufficient_statistics(self, X):
        XX = X[:, :, None] * X[:, None, :]
        return torch.cat([XX.view(X.size(0), -1), X,
                          Variable(torch.ones(X.size(0), 2).float())], dim=-1)

    def log_base_measure(self, X):
        return -.5 * X.size(-1) * math.log(2 * math.pi)


class MLPStateNormalGamma(MLPDecoderState):

    def __init__(self, natural_params):
        self._natural_params = natural_params

    def natural_params(self):
        return self._natural_params

    def sufficient_statistics(self, X):
        return torch.cat([X, Variable(torch.ones(X.size(0),
            X.size(1) + 1).float())], dim=-1)

    def log_base_measure(self, X):
        return -.5 * X.size(-1) * math.log(2 * math.pi)

    def as_priors(self):
        '''Convert the current MLPState into a list of prior objects.

        Returns:
            list: The corresponding priors of the ``MLPState``.

        '''
        priors = [NormalGammaPrior(nparams.data.numpy()[:-1])
                  for nparams in self._natural_params]
        return priors


class MLPModel(nn.Module):
    '''Base class for the encoder / decoder neural network of
    the VAE. The output of this network are the parameters of a
    conjugate exponential model. The proper way to use this class
    is to wrap with an object that "knows" how to make sense of the
    outptuts (see ``MLPEncoderState``, ``MLPDecoderIso``, ...).

    Note:
        This class only define the neural network structure and does
        not care wether it is used as encoder/decoder and how the
        parameters of the model is used.

    '''

    @staticmethod
    def _init_residulal_layer(linear_transform):
        W = linear_transform.weight.data.numpy()
        dim = max(*W.shape)
        q, _ = np.linalg.qr(np.random.randn(dim, dim))
        W = q[:W.shape[0], :W.shape[1]]
        linear_transform.weight = nn.Parameter(torch.from_numpy(W).float())

    def __init__(self, structure, outputs):
        '''Initialize the ``MLPModel``.

        Args:
            structure (``torch.Sequential``): Sequence linear/
                non-linear operations.
            outputs (list): List of tuple describing the output model.

        '''
        super().__init__()
        self.structure = structure

        # Get the input/ouput dimension of the structure.
        for transform in structure:
            if isinstance(transform, nn.Linear):
                in_dim = transform.in_features
                break
        for transform in reversed(structure):
            if isinstance(transform, nn.Linear):
                out_dim = transform.out_features
                break

        # Create the specific output layer.
        self.output_layer = nn.ModuleList()
        self.residual_connections = nn.ModuleList()
        self.residual_mapping = {}
        for i, output in enumerate(outputs):
            target_dim, residual = output
            self.output_layer.append(nn.Linear(out_dim, target_dim))
            if residual:
                ltransform = nn.Linear(in_dim, target_dim)
                MLPModel._init_residulal_layer(ltransform)
                self.residual_connections.append(ltransform)
                self.residual_mapping[i] = len(self.residual_connections) - 1

    def forward(self, X):
        h = self.structure(X)
        outputs = [transform(h) for transform in self.output_layer]
        for idx1, idx2 in self.residual_mapping.items():
            outputs[idx1] += self.residual_connections[idx2](X)
        return outputs

class MLPNormalDiag(MLPModel):
    '''Neural-Network ending with a double linear projection
    providing the mean and the logarithm of the diagonal of the
    covariance matrix.

    '''

    def __init__(self, structure, dim, residual=False):
        '''Initialize a ``MLPNormalDiag`` object.

        Args:
            structure (``torch.Sequential``): Sequence linear/
                non-linear operations.
            dim (int): Desired dimension of the modeled random
                variable.
            residual (boolean): Add a residual connection to the mean.

        '''
        super().__init__(structure, [(dim, residual), (dim, False)])

    def forward(self, X):
        mean, logprec = super().forward(X)
        return MLPStateNormal(mean, torch.exp(logprec))


class MLPNormalIso(MLPModel):
    '''Neural-Network ending with a double linear projection
    providing the mean and the isotropic covariance matrix.

    '''

    def __init__(self, structure, dim, residual=False):
        '''Initialize a ``MLPNormalDiag`` object.

        Args:
            structure (``torch.Sequential``): Sequence linear/
                non-linear operations.
            dim (int): Desired dimension of the modeled random
                variable.
            residual (boolean): Add a residual connection to the mean.

        '''
        super().__init__(structure, [(dim, residual), (1, False)])

    def forward(self, X):
        mean, logprec = super().forward(X)
        return MLPStateNormal(mean,
            torch.exp(logprec) * Variable(torch.ones(mean.size(1)).float()))


class MLPNormalGamma(MLPModel):
    '''Neural-Network ending with 4 linear and non-linear projection
    corresponding to the natural parameters of the Normal-Gamma
    density. This MLP cannot be used as a decoder.

    '''

    def __init__(self, structure, dim, prior_count=1.):
        '''Initialize a ``MLPNormalGamma`` MLP.

        Args:
            structure (``torch.Sequential``): Sequence linear/
                non-linear operations.
            dim (int): Desired dimension of the modeled random
                variable.
            prior_count (float): Number of pseudo-observations.

        '''
        self.prior_count = prior_count
        super().__init__(structure, [(dim // 2, False)] * 2)

    def forward(self, X):
        outputs = super().forward(X)

        mean = outputs[0]
        prec = torch.log(1 + torch.exp(outputs[1]))
        a = self.prior_count * Variable(torch.ones(*mean.size()))
        b = self.prior_count / prec

        np1 = self.prior_count * (mean ** 2) + 2 * b
        np2 = self.prior_count * mean
        np3 = self.prior_count * Variable(torch.ones(*mean.size()))
        np4 = 2 * a - 1

        # Commpute the log-normalizer.
        lognorm = torch.lgamma(.5 * (np4 + 1))
        lognorm += -.5 * torch.log(np3)
        lognorm += -.5 * (np4 + 1) * torch.log(.5 * (np1 - ((np2**2)/ np3)))
        lognorm = lognorm.sum(dim=-1)

        retval = MLPStateNormalGamma(torch.cat([np1, np2, np3, np4]
            + [-lognorm[:, None]], dim=-1))
        return retval
