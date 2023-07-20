import math
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from xopt import Evaluator, VOCS, Xopt
from xopt.generators import UpperConfidenceBoundGenerator
from xopt.generators.bayesian.bayesian_generator import BayesianGenerator
from xopt.generators.bayesian.turbo import (
    OptimizeTurboController,
    SafetyTurboController,
)
from xopt.resources.testing import TEST_VOCS_BASE, TEST_VOCS_DATA


class TestTurbo(TestCase):
    def test_turbo_init(self):
        test_vocs = deepcopy(TEST_VOCS_BASE)
        test_vocs.variables = {"x1": [0, 1]}

        state = OptimizeTurboController(test_vocs)
        assert state.dim == 1
        assert state.failure_tolerance == 2
        assert state.success_tolerance == 2

    @patch.multiple(BayesianGenerator, __abstractmethods__=set())
    def test_get_trust_region(self):
        # test in 1D
        test_vocs = deepcopy(TEST_VOCS_BASE)
        test_vocs.variables = {"x1": [0, 1]}

        gen = BayesianGenerator(vocs=test_vocs)
        gen.add_data(TEST_VOCS_DATA)
        model = gen.train_model()

        turbo_state = OptimizeTurboController(gen.vocs)
        turbo_state.update_state(gen.data)
        tr = turbo_state.get_trust_region(model)
        assert tr[0].numpy() >= test_vocs.bounds[0]
        assert tr[1].numpy() <= test_vocs.bounds[1]

        # test in 2D
        test_vocs = deepcopy(TEST_VOCS_BASE)
        gen = BayesianGenerator(vocs=test_vocs)
        gen.add_data(TEST_VOCS_DATA)
        model = gen.train_model()

        turbo_state = OptimizeTurboController(gen.vocs)
        turbo_state.update_state(gen.data)
        tr = turbo_state.get_trust_region(model)

        assert np.all(tr[0].numpy() >= test_vocs.bounds[0])
        assert np.all(tr[1].numpy() <= test_vocs.bounds[1])

        with pytest.raises(RuntimeError):
            turbo_state = OptimizeTurboController(gen.vocs)
            turbo_state.get_trust_region(model)

    @patch.multiple(BayesianGenerator, __abstractmethods__=set())
    def test_with_constraints(self):
        # test in 1D
        test_vocs = deepcopy(TEST_VOCS_BASE)
        test_vocs.variables = {"x1": [0, 1]}
        test_vocs.constraints = {"c1": ["LESS_THAN", 0.0]}

        # test with valid data
        data = deepcopy(TEST_VOCS_DATA)
        data["c1"] = -10.0
        y_data = np.ones(10)
        y_data[5] = -1
        data["y1"] = y_data
        best_x = data["x1"].iloc[5]

        gen = BayesianGenerator(vocs=test_vocs)
        gen.add_data(data)
        model = gen.train_model()

        turbo_state = OptimizeTurboController(gen.vocs, failure_tolerance=5)
        turbo_state.update_state(gen.data)
        assert turbo_state.center_x == {"x1": best_x}
        assert turbo_state.success_counter == 0
        assert turbo_state.failure_counter == 1

        tr = turbo_state.get_trust_region(model)
        assert tr[0].numpy() >= test_vocs.bounds[0]
        assert tr[1].numpy() <= test_vocs.bounds[1]

        # test a case where the last point is invalid
        new_data = deepcopy(gen.data)
        n_c = new_data["c1"].to_numpy()
        n_c[-1] = 1.0
        new_data["c1"] = n_c
        turbo_state.update_state(new_data)
        assert turbo_state.success_counter == 0
        assert turbo_state.failure_counter == 2

        # test will all invalid data
        data = deepcopy(TEST_VOCS_DATA)
        data["c1"] = 10.0
        y_data = np.ones(10)
        y_data[5] = -1
        data["y1"] = y_data

        gen = BayesianGenerator(vocs=test_vocs)
        gen.add_data(data)

        turbo_state = OptimizeTurboController(gen.vocs)
        with pytest.raises(RuntimeError):
            turbo_state.update_state(gen.data)

        # test best y value violates the constraint
        data = deepcopy(TEST_VOCS_DATA)
        c_data = -10.0*np.ones(10)
        c_data[5] = 5.0
        data["c1"] = c_data
        y_data = np.ones(10)
        y_data[5] = -1
        y_data[6] = -0.8
        data["y1"] = y_data
        best_x = data["x1"].iloc[6]

        gen = BayesianGenerator(vocs=test_vocs)
        gen.add_data(data)

        turbo_state = OptimizeTurboController(gen.vocs, failure_tolerance=5)
        turbo_state.update_state(gen.data)
        assert turbo_state.center_x == {"x1": best_x}

    def test_set_best_point(self):
        test_vocs = deepcopy(TEST_VOCS_BASE)

        turbo_state = OptimizeTurboController(test_vocs)
        turbo_state.update_state(TEST_VOCS_DATA)
        assert (
            turbo_state.best_value == TEST_VOCS_DATA.min()[test_vocs.objective_names[0]]
        )

    def test_in_generator(self):
        vocs = VOCS(
            variables={"x": [0, 2 * math.pi]},
            objectives={"f": "MINIMIZE"},
        )

        def sin_function(input_dict):
            x = input_dict["x"]
            return {"f": -10 * np.exp(-((x - np.pi) ** 2) / 0.01) + 0.5 * np.sin(5 * x)}

        evaluator = Evaluator(function=sin_function)
        generator = UpperConfidenceBoundGenerator(
            vocs=vocs, turbo_controller="optimize"
        )
        X = Xopt(evaluator=evaluator, generator=generator, vocs=vocs)

        X.evaluate_data(pd.DataFrame({"x": [3.0, 1.75, 2.0]}))

        # determine trust region from gathered data
        generator.train_model()
        generator.turbo_controller.update_state(generator.data)
        generator.turbo_controller.get_trust_region(generator.model)

    def test_safety(self):
        test_vocs = VOCS(
            variables={"x": [0, 2 * math.pi]},
            objectives={"f": "MINIMIZE"},
            constraints={"c": ["LESS_THAN", 0]},
        )

        test_data = pd.DataFrame(
            {"x": [0.5, 1.0, 1.5], "f": [1.0, 1.0, 1.0], "c": [-1.0, -1.0, 1.0]}
        )
        sturbo = SafetyTurboController(vocs=test_vocs)
        sturbo.update_state(test_data)

        assert sturbo.center_x == {"x": 0.75}
        assert sturbo.failure_counter == 1
