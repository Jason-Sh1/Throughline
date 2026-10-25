import numpy as np
import pytest

from cluster.online import Candidate, ClusterConfig, choose_cluster
from cluster.recluster import NOISE, plan_recluster, run_hdbscan

CFG = ClusterConfig(threshold=0.7, tie_margin=0.03, gray_margin=0.1, min_entity_overlap=2)


def vec_at(cos: float, base: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Unit vector with cosine `cos` to `base` (base and other orthonormal)."""
    return cos * base + np.sqrt(1 - cos**2) * other


E = np.eye(8, dtype=np.float32)


def cand(cid, centroid, entities=()):
    return Candidate(cid, centroid.astype(np.float32), 1, set(entities))


# --- online -------------------------------------------------------------------


def test_no_candidates_starts_new_cluster():
    assert choose_cluster(E[0], set(), [], CFG).cluster_id is None


def test_joins_most_similar_above_threshold():
    a, b = cand(1, E[0]), cand(2, vec_at(0.75, E[0], E[1]))
    d = choose_cluster(vec_at(0.9, E[0], E[2]), set(), [b, a], CFG)
    assert d.cluster_id == 1 and d.reason == "similarity" and d.similarity == pytest.approx(0.9)


def test_below_threshold_without_entities_is_new():
    d = choose_cluster(vec_at(0.65, E[0], E[1]), {"x"}, [cand(1, E[0], {"x"})], CFG)
    assert d.cluster_id is None and d.reason == "new"


def test_gray_zone_joins_on_entity_overlap():
    c = cand(1, E[0], {"jerome powell", "federal reserve", "inflation"})
    d = choose_cluster(vec_at(0.65, E[0], E[1]), {"jerome powell", "federal reserve"}, [c], CFG)
    assert d.cluster_id == 1 and d.reason == "entity_overlap"
    # one shared name isn't enough
    d = choose_cluster(vec_at(0.65, E[0], E[1]), {"jerome powell"}, [c], CFG)
    assert d.cluster_id is None
    # too far below threshold even with overlap
    d = choose_cluster(vec_at(0.5, E[0], E[1]), {"jerome powell", "federal reserve"}, [c], CFG)
    assert d.cluster_id is None


def test_tiebreak_prefers_entity_overlap_among_close_candidates():
    # article is 0.80 to cluster 1 and 0.78 to cluster 2 (within tie_margin);
    # cluster 2 shares names, so it wins.
    art = E[0]
    c1 = cand(1, vec_at(0.80, E[0], E[1]), {"gaza"})
    c2 = cand(2, vec_at(0.78, E[0], E[2]), {"gaza", "hamas", "qatar"})
    d = choose_cluster(art, {"gaza", "hamas", "qatar"}, [c1, c2], CFG)
    assert d.cluster_id == 2 and d.reason == "tiebreak"


def test_no_tiebreak_outside_margin():
    c1 = cand(1, vec_at(0.90, E[0], E[1]), set())
    c2 = cand(2, vec_at(0.80, E[0], E[2]), {"gaza", "hamas"})
    assert choose_cluster(E[0], {"gaza", "hamas"}, [c1, c2], CFG).cluster_id == 1


def test_candidate_centroid_running_mean():
    c = cand(1, E[0])
    c.add(E[1], {"a"})
    np.testing.assert_allclose(c.centroid, (E[0] + E[1]) / np.sqrt(2), atol=1e-6)
    assert c.n == 2 and c.entities == {"a"}


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("CLUSTER_THRESHOLD", "0.8")
    monkeypatch.setenv("CLUSTER_WINDOW_HOURS", "48")
    cfg = ClusterConfig.from_env()
    assert cfg.threshold == 0.8 and cfg.window_hours == 48


# --- re-cluster plan ------------------------------------------------------------


def test_plan_no_change_when_labels_match():
    plan = plan_recluster({1: 10, 2: 10, 3: 20, 4: 20}, {1: 0, 2: 0, 3: 1, 4: 1})
    assert plan.moves == {} and plan.merges == {} and plan.new_groups == {}


def test_plan_merges_two_clusters_into_larger_one():
    current = {1: 10, 2: 10, 3: 10, 4: 20, 5: 20}
    plan = plan_recluster(current, {a: 0 for a in current})
    assert plan.merges == {20: 10}
    assert plan.moves == {4: 10, 5: 10}


def test_plan_merge_tie_keeps_oldest_id():
    plan = plan_recluster({1: 30, 2: 20}, {1: 0, 2: 0})
    assert plan.merges == {30: 20} and plan.moves == {1: 20}


def test_plan_splits_cluster_into_new_group():
    current = {1: 10, 2: 10, 3: 10, 4: 10, 5: 10}
    plan = plan_recluster(current, {1: 0, 2: 0, 3: 0, 4: 1, 5: 1})
    assert plan.merges == {}
    assert plan.new_groups == {"new:1": [4, 5]}
    assert plan.moves == {4: "new:1", 5: "new:1"}


def test_plan_noise_stays_unless_cluster_merged():
    current = {1: 10, 2: 10, 3: 20, 4: 20, 5: 20}
    labels = {1: 0, 2: 0, 3: 0, 4: NOISE, 5: 0}
    plan = plan_recluster(current, labels)
    # 20 has 2 members in label 0 vs 10's 2 -> tie on overlap, oldest id (10) survives
    assert plan.merges == {20: 10}
    assert plan.moves[4] == 10  # noise follows its merged cluster
    plan = plan_recluster({1: 10, 2: 10, 3: 10}, {1: 0, 2: 0, 3: NOISE})
    assert plan.moves == {}


def test_plan_unclustered_articles_join_or_form_groups():
    plan = plan_recluster({1: 10, 2: None, 3: None, 4: None}, {1: 0, 2: 0, 3: 1, 4: 1})
    assert plan.moves == {2: 10, 3: "new:1", 4: "new:1"}


# --- HDBSCAN --------------------------------------------------------------------


def test_hdbscan_finds_separated_groups():
    rng = np.random.default_rng(0)
    groups = []
    for axis in (0, 1, 2):
        pts = E[axis] + rng.normal(0, 0.03, size=(6, 8))
        groups.append(pts)
    x = np.vstack(groups).astype(np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    labels = run_hdbscan(x, min_cluster_size=2)
    # each group of 6 shares one label, and the three labels differ
    per_group = [set(labels[i * 6 : (i + 1) * 6]) for i in range(3)]
    assert all(len(g) == 1 and NOISE not in g for g in per_group)
    assert len(set.union(*per_group)) == 3


def test_hdbscan_too_few_points_is_noise():
    assert list(run_hdbscan(np.array([[1.0, 0.0]], dtype=np.float32))) == [NOISE]
