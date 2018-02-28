'Test the Normal model.'


import sys
sys.path.insert(0, './')
import unittest
import numpy as np
import math
import beer
from beer import NormalDiagonalCovariance
import torch


TOLERANCE = 5


class TestNormalDiagonalCovariance(unittest.TestCase):

    def test_create(self):
        mean, prec = torch.ones(4), 2 * torch.eye(4)
        model = beer.NormalDiagonalCovariance.create(mean, prec,
            prior_count=2.5)
        m1, m2 = mean.numpy(), model.mean.numpy()
        self.assertTrue(np.allclose(m1, m2))
        c1, c2 = prec.numpy(), model.cov.numpy()
        self.assertTrue(np.allclose(c1, c2))
        self.assertAlmostEqual(2.5, model.count, places=TOLERANCE)

    def test_create_random(self):
        mean, prec = torch.ones(4), 2 * torch.eye(4)
        model = beer.NormalDiagonalCovariance.create(mean, prec,
            prior_count=2.5, random_init=True)
        m1, m2 = mean.numpy(), model.mean.numpy()
        self.assertFalse(np.allclose(m1, m2))
        c1, c2 = prec.numpy(), model.cov.numpy()
        self.assertTrue(np.allclose(c1, c2))
        self.assertAlmostEqual(2.5, model.count, places=TOLERANCE)

    def test_sufficient_statistics(self):
        X = np.ones((20, 5)) * 3.14
        s1 = np.c_[X**2, X, np.ones_like(X), np.ones_like(X)]
        X = torch.from_numpy(X)
        s2 = beer.NormalDiagonalCovariance.sufficient_statistics(X)
        self.assertTrue(np.allclose(s1, s2.numpy()))

    def test_exp_llh(self):
        mean, prec = torch.ones(2), 2 * torch.eye(2)
        model = beer.NormalDiagonalCovariance.create(mean, prec,
            prior_count=2.5)
        X = torch.ones(20, 2)
        T = model.sufficient_statistics(X)
        nparams = model.posterior.expected_sufficient_statistics
        exp_llh1 = T @ nparams
        exp_llh1 -= .5 * X.size(1) * math.log(2 * math.pi)
        s1 = T.sum(dim=0)
        exp_llh2, s2 = model.exp_llh(X, accumulate=True)
        self.assertTrue(np.allclose(exp_llh1.numpy(), exp_llh2.numpy()))
        self.assertTrue(np.allclose(s1.numpy(), s2.numpy()))


class TestNormalFullCovariance(unittest.TestCase):

    def test_create(self):
        mean, cov = torch.zeros(2), 4 * torch.eye(2)
        model = beer.NormalFullCovariance.create(mean, cov,
            prior_count=2.5)
        m1, m2 = mean.numpy(), model.mean.numpy()
        self.assertTrue(np.allclose(m1, m2))
        c1, c2 = cov.numpy(), model.cov.numpy()
        self.assertTrue(np.allclose(c1, c2))
        self.assertAlmostEqual(2.5, model.count, places=TOLERANCE)

    def test_create_random(self):
        mean, prec = torch.ones(2), 2 * torch.eye(2)
        model = beer.NormalFullCovariance.create(mean, prec,
            prior_count=2.5, random_init=True)
        m1, m2 = mean.numpy(), model.mean.numpy()
        self.assertFalse(np.allclose(m1, m2))
        c1, c2 = prec.numpy(), model.cov.numpy()
        self.assertTrue(np.allclose(c1, c2))
        self.assertAlmostEqual(2.5, model.count, places=TOLERANCE)


if __name__ == '__main__':
    unittest.main()