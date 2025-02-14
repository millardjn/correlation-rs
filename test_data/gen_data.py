import functools
import json
from typing import FrozenSet, List, Dict, Tuple

import numpy as np
import stim
import yaml

HyperEdge = FrozenSet[int]


class TannerGraph:
    """A tanner graph is a graph representation of a detector error model.

    It indicates the relationship between the errors and the detectors.
    In our case, the check nodes(cnode) are the detectors, and the variable
    nodes (vnode) are the error sets that flip the detectors. The different
    errors flip the same detectors are considered as the same vnode.
    """

    def __init__(self, dem: stim.DetectorErrorModel) -> None:
        """Construct the tanner graph from a detector error model.

        Args:
            dem: The detector error model.
        """
        self._dem = dem.flattened()

        self._hyperedges: List[HyperEdge] = []
        self._hyperedge_frames: Dict[HyperEdge, FrozenSet[int]] = {}
        self._hyperedge_probs: Dict[HyperEdge, float] = {}
        self._stim_decompose: Dict[HyperEdge, List[HyperEdge]] = {}
        self._process_dem()
        self._tanner_matrix = self._gen_tanner_matrix()

    @property
    def hyperedges(self) -> List[HyperEdge]:
        """All the hyperedges included in the detector error model."""
        return self._hyperedges

    @property
    def hyperedge_frames(self) -> Dict[HyperEdge, FrozenSet[int]]:
        """The frames of all hyperedges."""
        return self._hyperedge_frames

    @property
    def hyperedge_probs(self) -> Dict[HyperEdge, float]:
        """The probabilities of all hyperedges."""
        return self._hyperedge_probs

    @property
    def stim_decompose(self) -> Dict[HyperEdge, List[HyperEdge]]:
        """The stim suggested decomposition of all hyperedges."""
        return self._stim_decompose

    @property
    def tanner_matrix(self) -> np.ndarray:
        """The tanner matrix repr of the detector error model."""
        return self._tanner_matrix

    @property
    def num_hyperedges(self) -> int:
        """The number of hyperedges."""
        return len(self.hyperedges)

    @property
    def num_dets(self) -> int:
        """The number of detectors."""
        return self._dem.num_detectors

    def _gen_tanner_matrix(self) -> np.ndarray:
        """Generate the tanner matrix from the hyperedges."""
        tanner_matrix = np.zeros((self.num_dets, self.num_hyperedges), dtype=np.bool_)
        for vnode, hyperedge in enumerate(self._hyperedges):
            for cnode in hyperedge:
                tanner_matrix[cnode, vnode] = True
        return tanner_matrix

    def _process_dem(self):
        """Parse the detector error model."""
        for instruction in self._dem:
            if isinstance(instruction, stim.DemInstruction):
                if instruction.type == "error":
                    self._process_error(instruction)
                elif instruction.type == "detector":
                    pass
                else:
                    raise NotImplementedError()
            else:
                raise NotImplementedError()

    def _process_error(self, instruction: stim.DemInstruction):
        dets_track = []
        frames_track = []
        dets_sep_track = []
        frames_sep_track = []
        prob = instruction.args_copy()[0]
        for t in instruction.targets_copy():
            if t.is_relative_detector_id():
                dets_sep_track.append(t.val)
            elif t.is_logical_observable_id():
                frames_sep_track.append(t.val)
            elif t.is_separator():
                dets_track.append(dets_sep_track)
                frames_track.append(frames_sep_track)
                dets_sep_track = []
                frames_sep_track = []
        dets_track.append(dets_sep_track)
        frames_track.append(frames_sep_track)
        dets = frozenset(i for dets in dets_track for i in dets)
        frame = frozenset(functools.reduce(
            lambda x, y: x.symmetric_difference(y),
            [set(frame) for frame in frames_track],
            set()
        ))
        if dets not in self._hyperedges:
            self._hyperedges.append(dets)
            self._hyperedge_probs[dets] = prob
            self._hyperedge_frames[dets] = frame
            self._stim_decompose[dets] = [
                frozenset(dets)
                for dets, frame in zip(dets_track, frames_track)
            ]
        else:
            prob_prev = self._hyperedge_probs[dets]
            new_prob = prob_prev * (1 - prob) + prob * (1 - prob_prev)
            self._hyperedge_probs[dets] = new_prob


def correlation_from_detector_error_model(
        dem: stim.DetectorErrorModel
) -> Tuple[np.ndarray, np.ndarray]:
    """Extract the correlation matrix from the detector error model.

    Args:
        dem: The detector error model to be converted.

    Returns:
        edges: The correlation probability matrix of two detectors.
            The shape is (num_dets, num_dets).
        boundary: The correlation probability matrix of a single detector with virtual boundary.
            The shape is (num_dets, ).
    """
    tanner_graph = TannerGraph(dem)
    hyperedge_probs = tanner_graph.hyperedge_probs
    num_dets = dem.num_detectors

    correlation_edges = np.zeros((num_dets, num_dets))
    correlation_bdy = np.zeros(num_dets)
    for i in range(num_dets):
        correlation_bdy[i] = hyperedge_probs.get(frozenset([i]), 0.0)
        for j in range(i):
            pij = hyperedge_probs.get(frozenset([i, j]), 0.0)
            correlation_edges[i, j] = correlation_edges[j, i] = pij
    return correlation_bdy, correlation_edges


def surface_code():
    code = "surface_code:rotated_memory_z"
    distance = 3
    rounds = 2
    shots = 500000
    circuit = stim.Circuit.generated(
        code,
        distance=distance,
        rounds=rounds,
        after_clifford_depolarization=0.01,
        after_reset_flip_probability=0.01,
        before_measure_flip_probability=0.01,
        before_round_data_depolarization=0.01,
    )
    sampler = circuit.compile_detector_sampler()
    dets = sampler.sample(shots=shots, bit_packed=False)
    stim.write_shot_data_file(
        data=dets,
        path="surface_code/detectors.b8",
        format='b8',
        num_detectors=circuit.num_detectors,
    )
    stim.write_shot_data_file(
        data=dets,
        path="surface_code/detectors.01",
        format='01',
        num_detectors=circuit.num_detectors,
    )
    metadata = {
        "code": code,
        "distance": distance,
        "rounds": rounds,
        "num_shots": shots,
        "num_detectors": circuit.num_detectors,
    }
    with open("surface_code/metadata.yaml", "w") as f:
        yaml.dump(metadata, f)

    # save hyperedges
    dem = circuit.detector_error_model()
    graph = TannerGraph(dem)
    save_obj = {
        "hyperedges": [list(h) for h in graph.hyperedge_probs.keys()],
        "probability": list(graph.hyperedge_probs.values()),
    }
    with open("surface_code/hyperedges.json", "w") as f:
        json.dump(save_obj, f, indent=2)


def rep_code():
    code = "repetition_code:memory"
    distance = 5
    rounds = 5
    shots = 1000
    circuit = stim.Circuit.generated(
        code,
        distance=distance,
        rounds=rounds,
        after_clifford_depolarization=0.01,
        after_reset_flip_probability=0.01,
        before_measure_flip_probability=0.01,
        before_round_data_depolarization=0.01,
    )
    sampler = circuit.compile_detector_sampler()
    dets = sampler.sample(shots=shots, bit_packed=False)
    stim.write_shot_data_file(
        data=dets,
        path="rep_code/detectors.b8",
        format='b8',
        num_detectors=circuit.num_detectors,
    )
    stim.write_shot_data_file(
        data=dets,
        path="rep_code/detectors.01",
        format='01',
        num_detectors=circuit.num_detectors,
    )
    metadata = {
        "code": code,
        "distance": distance,
        "rounds": rounds,
        "num_shots": shots,
        "num_detectors": circuit.num_detectors,
    }
    with open("rep_code/metadata.yaml", "w") as f:
        yaml.dump(metadata, f)


if __name__ == '__main__':
    # rep_code()
    surface_code()
