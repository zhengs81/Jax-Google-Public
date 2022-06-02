# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from functools import partial
import zlib

from absl.testing import absltest
from absl.testing import parameterized

import numpy as np
from jax import lax
import jax.numpy as jnp

from examples import control

from jax.config import config
config.parse_flags_with_absl()


def one_step_lqr(dim, T):
  Q = jnp.stack(T * (jnp.eye(dim),))
  q = jnp.zeros((T, dim))
  R = jnp.zeros((T, dim, dim))
  r = jnp.zeros((T, dim))
  M = jnp.zeros((T, dim, dim))
  A = jnp.stack(T * (jnp.eye(dim),))
  B = jnp.stack(T * (jnp.eye(dim),))
  return control.LqrSpec(Q, q, R, r, M, A, B)


def control_from_lqr(lqr):
  T, dim, _ = lqr.Q.shape
  dot = jnp.dot

  def cost(t, x, u):
    return (
        dot(dot(lqr.Q[t], x), x) + dot(lqr.q[t], x) +
        dot(dot(lqr.R[t], u), u) + dot(lqr.r[t], u) +
        dot(dot(lqr.M[t], u), x))

  def dynamics(t, x, u):
    return dot(lqr.A[t], x) + dot(lqr.B[t], u)

  return control.ControlSpec(cost, dynamics, T, dim, dim)


def one_step_control(dim, T):

  def cost(t, x, u):
    return jnp.dot(x, x)

  def dynamics(t, x, u):
    return x + u

  return control.ControlSpec(cost, dynamics, T, dim, dim)


class ControlExampleTest(parameterized.TestCase):

  def setUp(self):
    self.rng = np.random.default_rng(zlib.adler32(self.__class__.__name__.encode()))

  def testTrajectoryCyclicIntegerCounter(self):
    num_states = 3

    def dynamics(t, x, u):
      return (x + u) % num_states

    T = 10

    U = jnp.ones((T, 1))
    X = control.trajectory(dynamics, U, jnp.zeros(1))
    expected = jnp.arange(T + 1) % num_states
    expected = jnp.reshape(expected, (T + 1, 1))
    np.testing.assert_allclose(X, expected)

    U = 2 * jnp.ones((T, 1))
    X = control.trajectory(dynamics, U, jnp.zeros(1))
    expected = jnp.cumsum(2 * jnp.ones(T)) % num_states
    expected = jnp.concatenate((jnp.zeros(1), expected))
    expected = jnp.reshape(expected, (T + 1, 1))
    np.testing.assert_allclose(X, expected)

  def testTrajectoryTimeVarying(self):
    T = 6

    def clip(x, lo, hi):
      return jnp.minimum(hi, jnp.maximum(lo, x))

    def dynamics(t, x, u):
      # computes `(x + u) if t > T else 0`
      return (x + u) * clip(t - T, 0, 1)

    U = jnp.ones((2 * T, 1))
    X = control.trajectory(dynamics, U, jnp.zeros(1))
    expected = jnp.concatenate((jnp.zeros(T + 1), jnp.arange(T)))
    expected = jnp.reshape(expected, (2 * T + 1, 1))
    np.testing.assert_allclose(X, expected)


  def testTrajectoryCyclicIndicator(self):
    num_states = 3

    def position(x):
      '''finds the index of a standard basis vector, e.g. [0, 1, 0] -> 1'''
      x = jnp.cumsum(x)
      x = 1 - x
      return jnp.sum(x, dtype=jnp.int32)

    def dynamics(t, x, u):
      '''moves  the next standard basis vector'''
      idx = (position(x) + u[0]) % num_states
      return lax.dynamic_slice_in_dim(jnp.eye(num_states), idx, 1)[0]

    T = 8

    U = jnp.ones((T, 1), dtype=jnp.int32)
    X = control.trajectory(dynamics, U, jnp.eye(num_states, dtype=jnp.int32)[0])
    expected = jnp.vstack((jnp.eye(num_states),) * 3)
    np.testing.assert_allclose(X, expected)


  def testLqrSolve(self):
    dim, T = 2, 10
    p = one_step_lqr(dim, T)
    K, k = control.lqr_solve(p)
    K_ = -jnp.stack(T * (jnp.eye(dim),))
    np.testing.assert_allclose(K, K_, atol=1e-6, rtol=1e-6)
    np.testing.assert_allclose(k, jnp.zeros((T, dim)))


  def testLqrPredict(self):
    dim, T = 2, 10
    p = one_step_lqr(dim, T)
    x0 = self.rng.normal(size=dim)
    X, U = control.lqr_predict(p, x0)
    np.testing.assert_allclose(X[0], x0)
    np.testing.assert_allclose(U[0], -x0,
                        atol=1e-6, rtol=1e-6)
    np.testing.assert_allclose(X[1:], jnp.zeros((T, 2)),
                        atol=1e-6, rtol=1e-6)
    np.testing.assert_allclose(U[1:], jnp.zeros((T - 1, 2)),
                        atol=1e-6, rtol=1e-6)


  def testIlqrWithLqrProblem(self):
    dim, T, num_iters = 2, 10, 3
    lqr = one_step_lqr(dim, T)
    p = control_from_lqr(lqr)
    x0 = self.rng.normal(size=dim)
    X, U = control.ilqr(num_iters, p, x0, jnp.zeros((T, dim)))
    np.testing.assert_allclose(X[0], x0)
    np.testing.assert_allclose(U[0], -x0)
    np.testing.assert_allclose(X[1:], jnp.zeros((T, 2)), atol=1E-15)
    np.testing.assert_allclose(U[1:], jnp.zeros((T - 1, 2)), atol=1E-15)


  def testIlqrWithLqrProblemSpecifiedGenerally(self):
    dim, T, num_iters = 2, 10, 3
    p = one_step_control(dim, T)
    x0 = self.rng.normal(size=dim)
    X, U = control.ilqr(num_iters, p, x0, jnp.zeros((T, dim)))
    np.testing.assert_allclose(X[0], x0)
    np.testing.assert_allclose(U[0], -x0)
    np.testing.assert_allclose(X[1:], jnp.zeros((T, 2)), atol=1E-15)
    np.testing.assert_allclose(U[1:], jnp.zeros((T - 1, 2)), atol=1E-15)


  def testIlqrWithNonlinearProblem(self):
    def cost(t, x, u):
      return (x[0] ** 2. + 1e-3 * u[0] ** 2.) / (t + 1.)

    def dynamics(t, x, u):
      return (x ** 2. - u ** 2.) / (t + 1.)

    T, num_iters, d = 10, 7, 1
    p = control.ControlSpec(cost, dynamics, T, d, d)

    x0 = jnp.array([0.2])
    X, U = control.ilqr(num_iters, p, x0, 1e-5 * jnp.ones((T, d)))
    assert_close = partial(np.testing.assert_allclose, atol=1e-2)
    assert_close(X[0], x0)
    assert_close(U[0] ** 2., x0 ** 2.)
    assert_close(X[1:], jnp.zeros((T, d)))
    assert_close(U[1:], jnp.zeros((T - 1, d)))


  def testMpcWithLqrProblem(self):
    dim, T, num_iters = 2, 10, 3
    lqr = one_step_lqr(dim, T)
    p = control_from_lqr(lqr)
    x0 = self.rng.normal(size=dim)
    solver = partial(control.ilqr, num_iters)
    X, U = control.mpc_predict(solver, p, x0, jnp.zeros((T, dim)))
    np.testing.assert_allclose(X[0], x0)
    np.testing.assert_allclose(U[0], -x0)
    np.testing.assert_allclose(X[1:], jnp.zeros((T, 2)))
    np.testing.assert_allclose(U[1:], jnp.zeros((T - 1, 2)))


  def testMpcWithLqrProblemSpecifiedGenerally(self):
    dim, T, num_iters = 2, 10, 3
    p = one_step_control(dim, T)
    x0 = self.rng.normal(size=dim)
    solver = partial(control.ilqr, num_iters)
    X, U = control.mpc_predict(solver, p, x0, jnp.zeros((T, dim)))
    np.testing.assert_allclose(X[0], x0)
    np.testing.assert_allclose(U[0], -x0)
    np.testing.assert_allclose(X[1:], jnp.zeros((T, 2)))
    np.testing.assert_allclose(U[1:], jnp.zeros((T - 1, 2)))


  def testMpcWithNonlinearProblem(self):
    def cost(t, x, u):
      return (x[0] ** 2. + 1e-3 * u[0] ** 2.) / (t + 1.)

    def dynamics(t, x, u):
      return (x ** 2. - u ** 2.) / (t + 1.)

    T, num_iters, d = 10, 7, 1
    p = control.ControlSpec(cost, dynamics, T, d, d)

    x0 = jnp.array([0.2])
    solver = partial(control.ilqr, num_iters)
    X, U = control.mpc_predict(solver, p, x0, 1e-5 * jnp.ones((T, d)))
    assert_close = partial(np.testing.assert_allclose, atol=1e-2)
    assert_close(X[0], x0)
    assert_close(U[0] ** 2., x0 ** 2.)
    assert_close(X[1:], jnp.zeros((T, d)))
    assert_close(U[1:], jnp.zeros((T - 1, d)))


if __name__ == '__main__':
  absltest.main()
