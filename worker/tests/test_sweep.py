import numpy as np

from cluster.online import Candidate, ClusterConfig, choose_cluster
from cluster.sweep import Item, replay, summarize


def _reference(items, cfg):
    """Plain loop over online.choose_cluster, the code the sweep must mirror."""
    cands: list[Candidate] = []
    labels = []
    for it in items:
        d = choose_cluster(it.vec, it.entities, cands, cfg)
        if d.cluster_id is None:
            cands.append(Candidate(len(cands), it.vec.copy(), 1, set(it.entities)))
            labels.append(len(cands) - 1)
        else:
            cands[d.cluster_id].add(it.vec, it.entities)
            labels.append(d.cluster_id)
    return labels


def _items(seed=0, n=300, topics=12):
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(topics, 32))
    names = [f"e{i}" for i in range(20)]
    out = []
    for i in range(n):
        t = rng.integers(topics)
        v = centers[t] + rng.normal(scale=rng.uniform(0.4, 1.2), size=32)
        ents = set(rng.choice(names, size=3, replace=False)) | {f"topic{t}"}
        out.append(Item(i, v / np.linalg.norm(v), ents, f"src{rng.integers(15)}", f"t{i}"))
    return out


def test_replay_matches_choose_cluster():
    items = _items()
    for cfg in (ClusterConfig(), ClusterConfig(threshold=0.7, gray_margin=0.0),
                ClusterConfig(threshold=0.55, min_entity_overlap=1)):  # fmt: skip
        assert replay(items, cfg) == _reference(items, cfg)


def test_stricter_threshold_never_makes_fewer_clusters_here():
    items = _items(seed=1)
    loose = summarize(items, replay(items, ClusterConfig(threshold=0.5)))
    strict = summarize(items, replay(items, ClusterConfig(threshold=0.8)))
    assert strict["clusters"] > loose["clusters"]
    assert strict["largest"] < loose["largest"]
