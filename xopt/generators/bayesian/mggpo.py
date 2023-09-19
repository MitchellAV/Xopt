import pandas as pd
import torch
from botorch.acquisition.multi_objective import qNoisyExpectedHypervolumeImprovement
from pydantic import Field

from xopt.generators.bayesian.objectives import create_mobo_objective
from xopt.generators.ga.cnsga import CNSGAGenerator
from .bayesian_generator import MultiObjectiveBayesianGenerator


class MGGPOGenerator(MultiObjectiveBayesianGenerator):
    name = "mggpo"
    population_size: int = Field(64, description="population size for ga")
    supports_batch_generation = True

    ga_generator: CNSGAGenerator = Field(
        None, description="CNSGA generator used to " "generate candidates"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # create GA generator
        self.ga_generator = CNSGAGenerator(
            vocs=self.vocs,
            population_size=self.population_size,
        )

    def propose_candidates(self, model, n_candidates=1):
        ga_candidates = self.ga_generator.generate(n_candidates * 10)
        ga_candidates = pd.DataFrame(ga_candidates)[self.vocs.variable_names].to_numpy()
        ga_candidates = torch.unique(
            torch.tensor(ga_candidates, **self._tkwargs), dim=0
        ).reshape(-1, 1, self.vocs.n_variables)

        if ga_candidates.shape[0] < n_candidates:
            raise RuntimeError("not enough unique solutions generated by the GA!")

        acq_funct = self.get_acquisition(self.model)
        acq_funct_vals = acq_funct(ga_candidates)
        best_idxs = torch.argsort(acq_funct_vals, descending=True)[:n_candidates]

        candidates = ga_candidates[best_idxs]
        return candidates.reshape(n_candidates, self.vocs.n_variables)

    def add_data(self, new_data: pd.DataFrame):
        super().add_data(new_data)
        self.ga_generator.add_data(self.data)

    def get_acquisition(self, model):
        """
        Returns a function that can be used to evaluate the acquisition function
        """
        if model is None:
            raise ValueError("model cannot be None")

        # get base acquisition function
        acq = self._get_acquisition(model)
        return acq

    def _get_objective(self):
        return create_mobo_objective(self.vocs, self._tkwargs)

    def _get_acquisition(self, model):
        # get reference point from data
        inputs = self.get_input_data(self.data)
        sampler = self._get_sampler(model)

        acq = qNoisyExpectedHypervolumeImprovement(
            model,
            X_baseline=inputs,
            prune_baseline=True,
            constraints=self._get_constraint_callables(),
            ref_point=self.torch_reference_point,
            sampler=sampler,
            objective=self._get_objective(),
            cache_root=False,
        )

        return acq