import numpy as np
import networkx as nx
from typing import Tuple, Dict


def add_empty_to_lookup(lookup: np.ndarray) -> np.ndarray:
    """
    Add an empty entry to a lookup table.
    """
    return np.concatenate([[0], lookup+1], dtype=lookup.dtype)


def apply_lookup(array: np.ndarray | None, mapping: Dict[int, int] | Tuple[np.ndarray, np.ndarray] | np.array | None,
                 apply_inplace_on: np.ndarray | None = None) \
        -> np.ndarray:
    lookup = mapping
    if mapping is None:
        return array
    if not isinstance(mapping, np.ndarray):
        lookup = np.arange(len(array), dtype=np.int64)
        if isinstance(mapping, dict):
            mapping = tuple(zip(*mapping.items()))
        search = mapping[0]
        replace = mapping[1]
        if not isinstance(replace, np.ndarray):
            replace = [replace] * len(search)
        for s, r in zip(search, replace):
            lookup[lookup == s] = r

    if apply_inplace_on is not None:
        apply_inplace_on[:] = lookup[apply_inplace_on]

    return lookup if array is None else lookup[array]


def apply_node_lookup_on_coordinates(nodes_coord, nodes_lookup: np.ndarray | None,
                                     nodes_weight: np.ndarray | None = None):
    """
    Apply a lookup table on a set of coordinates to merge specific nodes and return their barycenter.

    Args:
        nodes_coord: A tuple (y, x) of two 1D arrays of the same length N containing the nodes coordinates.
        nodes_lookup: A 1D array of length N containing a lookup table of points index. Nodes with the same
                          index in this table will be merged.

    Returns:
        A tuple (y, x) of two 1D arrays of length M (max(junction_lookup) + 1) containing the merged coordinates.

    """
    if nodes_lookup is None:
        return nodes_coord
    if nodes_weight is None or nodes_weight.sum() == 0:
        nodes_weight = np.ones_like(nodes_lookup)
    nodes_weight = nodes_weight + 1e-8

    assert len(nodes_coord) == 2, f"nodes_coord must be a tuple of two 1D arrays. Got {len(nodes_coord)} elements."
    assert len(nodes_coord[0]) == len(nodes_coord[1]) == len(nodes_lookup) == len(nodes_weight), \
        f"nodes_coord, nodes_lookup and nodes_weight must have the same length. Got {len(nodes_coord[0])}, " \
        f"{len(nodes_lookup)} and {len(nodes_weight)} respectively."

    jy, jx = nodes_coord
    nodes_coord = np.zeros((np.max(nodes_lookup) + 1, 2), dtype=np.float64)
    nodes_total_weight = np.zeros((np.max(nodes_lookup) + 1,), dtype=np.float64)
    np.add.at(nodes_coord, nodes_lookup, np.asarray((jy, jx)).T * nodes_weight[:, None])
    np.add.at(nodes_total_weight, nodes_lookup, nodes_weight)
    nodes_coord = nodes_coord / nodes_total_weight[:, None]
    return nodes_coord.T


def branch_by_nodes_to_adjacency_list(branches_by_nodes: np.ndarray, sorted=False) -> np.ndarray:
    """
    Convert a connectivity matrix of branches and nodes to an adjacency list of branch. Each branch is represented
    by a tuple (node1, node2) where node1 and node2 are the two nodes connected by the branch.

    Args:
        branches_by_nodes: A 2D array of shape (nb_branches, nb_nodes) containing the connectivity matrix of branches
                             and nodes. branches_by_nodes[i, j] is True if the branch i is connected to the node j.
        sorted: If True, the adjacency list is sorted by the first node id.

    Returns:
        A 2d array of shape (nb_branches, 2) containing the adjacency list of branches. The first node id is always
        lower than the second node id.
    """
    node1_ids = np.argmax(branches_by_nodes, axis=1)
    node2_ids = branches_by_nodes.shape[1] - np.argmax(branches_by_nodes[:, ::-1], axis=1) - 1

    if sorted:
        sort_id = np.argsort(node1_ids)
        node1_ids = node1_ids[sort_id]
        node2_ids = node2_ids[sort_id]

    return np.stack([node1_ids, node2_ids], axis=1)


def compute_is_endpoints(branches_by_nodes):
    """
    Compute a boolean array indicating if each node is an endpoint (i.e. connected to only one branch).
    """
    return np.sum(branches_by_nodes, axis=0) == 1


def delete_nodes(branches_by_nodes, nodes_id):
    """
    Delete a node and its connected branch from the connectivity matrix of branches and nodes.
    """
    # Check parameters and compute the initial branch lookup
    branch_lookup, nodes_mask, nodes_id = _prepare_node_deletion(branches_by_nodes, nodes_id)

    deleted_branches = branches_by_nodes[:, nodes_id].any(axis=1)
    branches_id = np.where(deleted_branches)[0]

    branches_by_nodes = np.delete(branches_by_nodes, branches_id, axis=0)
    branches_by_nodes = np.delete(branches_by_nodes, nodes_id, axis=1)

    branch_lookup = add_empty_to_lookup(branch_lookup)
    branch_lookup[1:][deleted_branches] = 0
    branch_shift_lookup = np.cumsum(np.concatenate([[True], ~deleted_branches])) - 1

    return branches_by_nodes, branch_shift_lookup[branch_lookup], nodes_mask


def fuse_nodes(branches_by_nodes, nodes_id, node_coord: Tuple[np.ndarray, np.ndarray] | None = None):
    """
    Fuse a node from the connectivity matrix of branches and nodes.
    The node is removed and the two branches connected to it are fused into one.
    """
    # Check parameters and compute the initial branch lookup
    branch_lookup, nodes_mask, nodes_id = _prepare_node_deletion(branches_by_nodes, nodes_id)
    nb_branches = len(branch_lookup)

    branches_by_fused_nodes = branches_by_nodes[:, nodes_id]
    invalid_fused_node = np.sum(branches_by_fused_nodes, axis=0) > 2
    if np.any(invalid_fused_node):
        print("Warning: some nodes are connected to more than 2 branches and won't be fused.")
        branches_by_fused_nodes = branches_by_fused_nodes[:, ~invalid_fused_node]
    branch1_ids = np.argmax(branches_by_fused_nodes, axis=0)
    branch2_ids = nb_branches - np.argmax(branches_by_fused_nodes[::-1], axis=0) - 1

    sort_id = np.argsort(branch1_ids)[::-1]
    branch1_ids = branch1_ids[sort_id]
    branch2_ids = branch2_ids[sort_id]
    if node_coord is not None:
        node_coord = tuple(c[nodes_id[sort_id]] for c in node_coord)
    branches_to_delete = []

    # Sequential merge is required when a branch appear both in branch1_ids and branch2_ids
    #  (because 2 adjacent nodes are fused)
    for b1, b2 in zip(branch1_ids, branch2_ids):
        b2_id = branch_lookup[b2]
        branches_by_nodes[b1] |= branches_by_nodes[b2_id]
        branches_to_delete.append(b2_id)

        branch_lookup = apply_lookup(branch_lookup, {b2_id: b1})

    branches_by_nodes = np.delete(branches_by_nodes, branches_to_delete, axis=0)
    branches_by_nodes = np.delete(branches_by_nodes, nodes_id, axis=1)

    branch_shift_lookup = np.cumsum(np.isin(np.arange(len(branch_lookup)), branches_to_delete, invert=True))-1
    branch_lookup = add_empty_to_lookup(branch_shift_lookup[branch_lookup])

    if node_coord is not None:
        nodes_labels = node_coord[0], node_coord[1], branch_lookup[branch1_ids+1]
        return branches_by_nodes, branch_lookup, nodes_mask, nodes_labels
    else:
        return branches_by_nodes, branch_lookup, nodes_mask


def _prepare_node_deletion(branches_by_nodes, nodes_id):
    assert nodes_id.ndim == 1, "nodes_id must be a 1D array"
    if nodes_id.dtype == bool:
        assert len(nodes_id) == branches_by_nodes.shape[
            1], "nodes_id must be a boolean array of the same length as the number of nodes," \
                f" got len(nodes_id)={len(nodes_id)} instead of {branches_by_nodes.shape[1]}."
        nodes_mask = ~nodes_id
        nodes_id = np.where(nodes_id)[0]
    else:
        nodes_mask = None
    assert nodes_id.dtype == np.int64, "nodes_id must be a boolean or integer array"

    if nodes_mask is None:
        nodes_mask = np.isin(np.arange(branches_by_nodes.shape[1], dtype=np.int64), nodes_id, invert=True)

    nb_branches = branches_by_nodes.shape[0]
    return np.arange(nb_branches, dtype=np.int64), nodes_mask, nodes_id


def invert_lookup(lookup):
    """
    Invert a lookup array. The lookup array must be a 1D array of integers. The output array is a 1D array of length
    max(lookup) + 1. The output array[i] contains the list of indices of lookup where lookup[index] == i.
    """
    splits = np.split(np.argsort(np.argsort(lookup)), np.cumsum(np.unique(lookup, return_counts=True)[1]))[:-1]
    return np.array([np.array(s[0], dtype=np.int64) for s in splits])


def merge_equivalent_branches(branches_by_node, max_nodes_distance=None, nodes_coordinates=None):
    if max_nodes_distance is None:
        branches_by_node, branches_lookup = np.unique(branches_by_node, return_inverse=True, axis=0)
    else:
        # Select small branches
        assert nodes_coordinates is not None, "nodes_coordinates must be provided when max_nodes_distance is not None"
        branches_n1, branches_n2 = branch_by_nodes_to_adjacency_list(branches_by_node).T
        nodes_coordinates = np.stack(nodes_coordinates, axis=1)
        branches_node_dist = np.linalg.norm(nodes_coordinates[branches_n1] - nodes_coordinates[branches_n2], axis=1)

        small_branches = branches_node_dist <= max_nodes_distance
        small_branches_lookup = np.where(small_branches)[0]
        if len(small_branches_lookup) == 0:
            return branches_by_node, None

        small_branches_by_node = branches_by_node[small_branches]

        # Identify equivalent small branches
        small_branches_by_node, unique_idx, unique_count = np.unique(small_branches_by_node, axis=0,
                                                                     return_inverse=True, return_counts=True)
        branches_to_remove = []
        for duplicate_id in np.where(unique_count > 1)[0]:
            duplicated_branches = small_branches_lookup[np.where(unique_idx == duplicate_id)[0]]
            branches_to_remove.append(duplicated_branches[1:])

        if len(branches_to_remove) == 0:
            return branches_by_node, None

        branches_lookup = np.arange(len(branches_by_node), dtype=np.int64)

        # Delete duplicated branches
        branches_to_remove = np.concatenate(branches_to_remove)
        branches_by_node = np.delete(branches_by_node, branches_to_remove, axis=0)

        branches_lookup_shift = np.cumsum(np.isin(branches_lookup, branches_to_remove, invert=True)) - 1
        branches_lookup = branches_lookup_shift[branches_lookup]

    return branches_by_node, add_empty_to_lookup(branches_lookup)


def merge_nodes_by_distance(branches_by_nodes: np.ndarray, nodes_coord: tuple[np.ndarray, np.ndarray],
                            distance: float | list[Tuple[np.ndarray, float, bool]]):
    """

    """
    if isinstance(distance, float):
        distance = [(None, distance)]

    branch_lookup = None

    for i, (mask, dist, remove_branch) in enumerate(distance):
        if dist <= 0:
            continue
        masked_coord = np.stack(nodes_coord, axis=1)
        masked_coord = masked_coord[mask] if mask is not None else masked_coord
        proximity_matrix = np.linalg.norm(masked_coord[:, None] - masked_coord[None, :], axis=2) <= dist
        proximity_matrix &= ~np.eye(proximity_matrix.shape[0], dtype=bool)

        if proximity_matrix.sum() == 0:
            continue

        lookup = np.arange(len(nodes_coord[0]), dtype=np.int64)
        lookup = lookup[mask] if mask is not None else lookup
        nodes_clusters = [tuple(lookup[_] for _ in cluster)
                          for cluster in solve_clusters(np.where(proximity_matrix)) if len(cluster) > 1]
        endpoints_nodes = compute_is_endpoints(branches_by_nodes)

        branches_by_nodes, branch_lookup2, node_lookup = merge_nodes_clusters(branches_by_nodes, nodes_clusters,
                                                                              remove_branches_labels=remove_branch)

        nodes_coord = apply_node_lookup_on_coordinates(nodes_coord, node_lookup, nodes_weight=endpoints_nodes)
        branch_lookup = apply_lookup(branch_lookup, branch_lookup2)
        inverted_lookup = invert_lookup(node_lookup)

        for j, (m, d, r) in enumerate(distance[i+1:]):
            if m is not None:
                distance[j+i+1] = (m[inverted_lookup], d, r)

    return branches_by_nodes, branch_lookup, nodes_coord


def merge_nodes_clusters(branches_by_junctions, nodes_clusters, remove_branches_labels=True):
    """
    Merge junctions in the connectivity matrix of branches and junctions.
    """
    node_lookup = np.arange(branches_by_junctions.shape[1], dtype=np.int64)
    branches_to_remove = np.zeros(branches_by_junctions.shape[0], dtype=bool)
    branches_lookup = np.arange(branches_by_junctions.shape[0]+1, dtype=np.int64)
    node_to_remove = np.zeros(branches_by_junctions.shape[1], dtype=bool)

    # TODO: what happen in the case of common nodes between clusters?
    for cluster in nodes_clusters:
        cluster = np.asarray(tuple(cluster), dtype=np.int64)
        cluster.sort()
        cluster_branches = np.where(np.sum(branches_by_junctions[:, cluster].astype(bool), axis=1) >= 2)[0]
        cluster_branches.sort()
        branches_to_remove[cluster_branches] = True
        incoming_cluster_branches = np.where(np.sum(branches_by_junctions[:, cluster].astype(bool), axis=1) == 1)[0]
        if len(incoming_cluster_branches):
            branches_lookup = apply_lookup(branches_lookup,
                                           (cluster_branches + 1, branches_lookup[incoming_cluster_branches[0] + 1]))
        else:
            branches_lookup[cluster_branches+1] = 0
        node_lookup[cluster[1:]] = cluster[0]
        node_to_remove[cluster[1:]] = True

    if branches_to_remove.any():
        branches_by_junctions = branches_by_junctions[~branches_to_remove, :]
        if remove_branches_labels:
            branches_lookup[np.where(branches_to_remove)[0]+1] = 0
        branches_lookup_shift = np.cumsum(np.concatenate(([True], ~branches_to_remove))) - 1
        branches_lookup = branches_lookup_shift[branches_lookup]
    else:
        branches_lookup = None
    nb_branches = branches_by_junctions.shape[0]

    node_lookup_shift = np.cumsum(~node_to_remove) - 1
    nb_nodes = node_lookup_shift[-1] + 1
    node_lookup = node_lookup_shift[node_lookup]

    nodes_by_branches = np.zeros_like(branches_by_junctions, shape=(nb_nodes, nb_branches))
    np.add.at(nodes_by_branches, node_lookup, branches_by_junctions.T)

    return nodes_by_branches.T, branches_lookup, node_lookup


def node_rank(branches_by_nodes):
    """
    Compute the rank of each node in the connectivity matrix of branches and nodes.
    """
    return np.sum(branches_by_nodes, axis=0)


def simple_graph_matching(node1_yx: tuple[np.ndarray, np.ndarray], node2_yx: tuple[np.ndarray, np.ndarray],
                          max_matching_distance: float | None = None, return_distance: bool = False):
    """
    Match nodes from two graphs based on the euclidian distance between nodes.

    Each node from the first graph is matched to the closest node from the second graph, if their distance is below
      max_matching_distance. When multiple nodes from both graph are near each other, minimize the sum of the distance
      between matched nodes.

    This implementation use the Hungarian algorithm to maximize the sum of the inverse of the distance between
        matched nodes.


    Args:
        node1_yx: tuple (y, x), where y and x are vectors of the same length and encode the coordinates of the nodes
                    of the first graph.
        node2_yx: same format as node1_yx but for the second graph.
        max_matching_distance: maximum distance between two nodes to be considered as a match.
        return_distance: if True, return the distance between each matched nodes.
    Returns:
        A tuple (node1_matched, node2_matched), where node1_matched and node2_matched are vectors of the same length.
          Every (node1_matched[i], node2_matched[i]) encode a match, node1_matched being the index of a node from the
          first graph and node2_matched the index of the corresponding node from the second graph.

        If return_distance is True, returns ((node1_matched, node2_matched), nodes_distance), where nodes_distance
          is a vector of the same length as node1_matched and node2_matched, and encode the distance between each
          matched nodes.
    """
    from pygmtools.linear_solvers import hungarian
    yx1 = np.stack(node1_yx, axis=1)
    yx2 = np.stack(node2_yx, axis=1)
    n1 = len(yx1)
    n2 = len(yx2)

    # Compute the euclidian distance between each node
    euclidian_distance = np.linalg.norm(yx1[:, None] - yx2[None, :], axis=2)

    # Compute the weight as the distance inverse (the hungarian method maximise the sum of the weight)
    weight = 1 / (1e-8 + euclidian_distance)

    # Set the cost of unmatch nodes to half the inverse of the maximum distance,
    #  so that nodes separated by more than max_distance are better left unmatched.
    min_weight = 0.5 / (1e-8 + max_matching_distance) if max_matching_distance is not None else 0

    # Compute the hungarian matching
    matched_nodes = hungarian(weight[None, ...], [n1], [n2], np.repeat([[min_weight]], n1, axis=1),
                              np.repeat([[min_weight]], n2, axis=1))[0]
    matched_nodes = np.where(matched_nodes)

    if return_distance:
        return matched_nodes, euclidian_distance[matched_nodes]
    return matched_nodes


def solve_clusters(pairwise_connection: list[Tuple[int, int]] | tuple[np.ndarray, np.ndarray]) -> list[set]:
    """
    Generate a list of clusters from a list of pairwise connections.
    """

    if isinstance(pairwise_connection, tuple):
        assert len(pairwise_connection) == 2, "pairwise_connection must be a tuple of two arrays"
        assert pairwise_connection[0].shape == pairwise_connection[1].shape, "pairwise_connection must be a tuple of two arrays of the same shape"
        pairwise_connection = zip(*pairwise_connection)

    clusters = []
    for p1, p2 in pairwise_connection:
        for i, c in enumerate(clusters):
            if p1 in c:
                for j, c2 in enumerate(clusters[i+1:]):
                    if p2 in c2:
                        c.update(c2)
                        del clusters[j]
                        break
                else:
                    c.add(p2)
                break
            elif p2 in c:
                for j, c1 in enumerate(clusters[i+1:]):
                    if p1 in c1:
                        c.update(c1)
                        del clusters[j]
                        break
                else:
                    c.add(p1)
                break
        else:
            clusters.append({p1, p2})
    return clusters


def perimeter_from_vertices(coord: np.ndarray):
    coord = np.asarray(coord)
    next_coord = np.roll(coord, 1, axis=0)
    return np.sum(np.linalg.norm(coord - next_coord, axis=1))
