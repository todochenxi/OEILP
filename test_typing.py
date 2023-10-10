import os
import random
import argparse
import logging
import json
import time

import multiprocessing as mp
import scipy.sparse as ssp
from tqdm import tqdm
import networkx as nx
import torch
import numpy as np
import dgl


def process_files(files, onto_files, type_files, saved_data2id, add_traspose_rels,
                  use_test_subgraph=False, neg_model='sample'):
    '''
    files: Dictionary map of file paths to read the triplets from.
    onto_files: Dictionary map of file paths to read the ontology triplets from.
    type_file: Dictionary map of file paths to read the type information from.
    saved_relation2id: Saved relation2id (mostly passed from a trained model) which can be used to map relations to pre-defined indices and filter out the unknown ones.
    '''
    entity2id = {}
    relation2id = saved_data2id[0]

    triplets = {}

    ent = 0
    # rel = 0

    for file_type, file_path in files.items():

        data = []
        with open(file_path) as f:
            file_data = [line.split() for line in f.read().split('\n')[:-1]]

        for triplet in file_data:
            if triplet[0] not in entity2id:
                entity2id[triplet[0]] = ent
                ent += 1
            if triplet[2] not in entity2id:
                entity2id[triplet[2]] = ent
                ent += 1

            # Save the triplets corresponding to only the known relations
            if triplet[1] in relation2id:
                data.append([entity2id[triplet[0]], entity2id[triplet[2]], relation2id[triplet[1]]])

        triplets[file_type] = np.array(data)

    id2entity = {v: k for k, v in entity2id.items()}
    id2relation = {v: k for k, v in relation2id.items()}

    # Construct the list of adjacency matrix each corresponding to eeach relation. Note that this is constructed only from the train data.
    adj_list = []
    adj_list_all = []
    for i in range(len(relation2id)):
        idx = np.argwhere(triplets['graph'][:, 2] == i)
        adj_list.append(ssp.csc_matrix((np.ones(len(idx), dtype=np.uint8), (
            triplets['graph'][:, 0][idx].squeeze(1), triplets['graph'][:, 1][idx].squeeze(1))),
                                       shape=(len(entity2id), len(entity2id))))
        a = ssp.csc_matrix((np.ones(len(idx), dtype=np.uint8), (
            triplets['graph'][:, 0][idx].squeeze(1), triplets['graph'][:, 1][idx].squeeze(1))),
                           shape=(len(entity2id), len(entity2id)))
        c = a
        if use_test_subgraph:
            idx_t = np.argwhere(triplets['links'][:, 2] == i)
            b = ssp.csc_matrix((np.ones(len(idx_t), dtype=np.uint8), (
                triplets['links'][:, 0][idx_t].squeeze(1), triplets['links'][:, 1][idx_t].squeeze(1))),
                               shape=(len(entity2id), len(entity2id)))
            c = a + b
        adj_list_all.append(c)
    # Add transpose matrices to handle both directions of relations.
    adj_list_aug = adj_list
    if add_traspose_rels:
        adj_list_t = [adj.T for adj in adj_list]
        adj_list_aug = adj_list + adj_list_t

    dgl_adj_list = ssp_multigraph_to_dgl(adj_list_aug)

    adj_list_all_aug = adj_list_all
    if add_traspose_rels:
        adj_list_all_t = [adj.T for adj in adj_list_all]
        adj_list_all_aug = adj_list_all + adj_list_all_t

    dgl_adj_list_all = ssp_multigraph_to_dgl(adj_list_all_aug)

    onto2id = saved_data2id[1]
    meta2id = saved_data2id[2]
    triplets_onto = {}

    for file_type, file_path in onto_files.items():

        data_onto = []
        with open(file_path) as f:
            file_data = [line.split() for line in f.read().split('\n')[:-1]]

        for triplet in file_data:
            # Save the triplets corresponding to only the known relations
            if triplet[1] in meta2id and triplet[0] in onto2id and triplet[2] in onto2id:
                data_onto.append([onto2id[triplet[0]], onto2id[triplet[2]], meta2id[triplet[1]]])

        triplets_onto[file_type] = np.array(data_onto)

    id2onto = {v: k for k, v in onto2id.items()}
    id2meta = {v: k for k, v in meta2id.items()}

    # Construct the list of adjacency matrix each corresponding to each meta relation. Note that this is constructed only from the onto data.
    adj_list_onto = []
    for i in range(len(meta2id)):
        idx = np.argwhere(triplets_onto['onto'][:, 2] == i)
        adj_list_onto.append(ssp.csc_matrix((np.ones(len(idx), dtype=np.uint8), (
            triplets_onto['onto'][:, 0][idx].squeeze(1), triplets_onto['onto'][:, 1][idx].squeeze(1))),
                                            shape=(len(onto2id), len(onto2id))))

    # Establish the mapping relationship between entities and their corresponding concepts. Based on all data.
    entity2onto = {}
    for file_type, file_path in type_files.items():
        with open(file_path) as f:
            file_data = [line.split() for line in f.read().split('\n')[:-1]]
        for triplet in file_data:
            if triplet[0] in entity2id and triplet[2] in onto2id:
                ent_id = entity2id[triplet[0]]
                onto_id = onto2id[triplet[2]]
                if ent_id in entity2onto:
                    entity2onto[ent_id].append(onto_id)
                else:
                    entity2onto[ent_id] = [onto_id]
    m_e2o = np.ones([len(entity2id), len(id2onto)]) * len(id2onto)
    entity2onto_neg = {}
    m_e2o_pos = np.ones([len(entity2id), len(id2onto)]) * len(id2onto)
    m_e2o_neg = np.ones([len(entity2id), len(id2onto)]) * len(id2onto)
    for enti, ont in entity2onto.items():
        ont_list = [j for j in range(len(id2onto))]
        for i in ont:
            if i in ont_list:
                ont_list.remove(i)
        ont = np.array(ont)
        if neg_model == 'sample' and len(ont_list) > 50:
            ont_neg = np.random.choice(ont_list, 50-len(ont))
        else:
            ont_neg = np.array(ont_list)
        m_e2o_pos[enti][: ont.shape[0]] = ont
        m_e2o_neg[enti][: ont_neg.shape[0]] = ont_neg
        entity2onto_neg[enti] = list(ont_neg)
    print("Construct matrix of entity2onto done!")

    return adj_list, dgl_adj_list, adj_list_all, dgl_adj_list_all, triplets, entity2id, relation2id, id2entity, id2relation, adj_list_onto, triplets_onto, onto2id, id2onto, meta2id, id2meta, entity2onto, entity2onto_neg, m_e2o, m_e2o_pos, m_e2o_neg


def intialize_worker(model, adj_list_all, dgl_adj_list_all, id2entity, m_e2o, m_e2o_pos, m_e2o_neg, params,
                     node_features,
                     kge_entity2id):
    global model_, adj_list_all_, dgl_adj_list_all_, id2entity_, entity2onto_, entity2onto_pos_, entity2onto_neg_, params_, node_features_, kge_entity2id_
    model_, adj_list_all_, dgl_adj_list_all_, id2entity_, entity2onto_, entity2onto_pos_, entity2onto_neg_, params_, node_features_, kge_entity2id_ = model, adj_list_all, dgl_adj_list_all, id2entity, m_e2o, m_e2o_pos, m_e2o_neg, params, node_features, kge_entity2id


def incidence_matrix(adj_list):
    '''
    adj_list: List of sparse adjacency matrices
    '''

    rows, cols, dats = [], [], []
    dim = adj_list[0].shape
    for adj in adj_list:
        adjcoo = adj.tocoo()
        rows += adjcoo.row.tolist()
        cols += adjcoo.col.tolist()
        dats += adjcoo.data.tolist()
    row = np.array(rows)
    col = np.array(cols)
    data = np.array(dats)
    return ssp.csc_matrix((data, (row, col)), shape=dim)


def _bfs_relational(adj, roots, max_nodes_per_hop=None):
    """
    BFS for graphs with multiple edge types. Returns list of level sets.
    Each entry in list corresponds to relation specified by adj_list.
    Modified from dgl.contrib.data.knowledge_graph to node accomodate sampling
    """
    visited = set()
    current_lvl = set(roots)

    next_lvl = set()

    while current_lvl:

        for v in current_lvl:
            visited.add(v)

        next_lvl = _get_neighbors(adj, current_lvl)
        next_lvl -= visited  # set difference

        if max_nodes_per_hop and max_nodes_per_hop < len(next_lvl):
            next_lvl = set(random.sample(next_lvl, max_nodes_per_hop))

        yield next_lvl

        current_lvl = set.union(next_lvl)


def _get_neighbors(adj, nodes):
    """Takes a set of nodes and a graph adjacency matrix and returns a set of neighbors.
    Directly copied from dgl.contrib.data.knowledge_graph"""
    sp_nodes = _sp_row_vec_from_idx_list(list(nodes), adj.shape[1])
    sp_neighbors = sp_nodes.dot(adj)
    neighbors = set(ssp.find(sp_neighbors)[1])  # convert to set of indices
    return neighbors


def _sp_row_vec_from_idx_list(idx_list, dim):
    """Create sparse vector of dimensionality dim from a list of indices."""
    shape = (1, dim)
    data = np.ones(len(idx_list))
    row_ind = np.zeros(len(idx_list))
    col_ind = list(idx_list)
    return ssp.csr_matrix((data, (row_ind, col_ind)), shape=shape)


def get_neighbor_nodes(roots, adj, h=1, max_nodes_per_hop=None):
    bfs_generator = _bfs_relational(adj, roots, max_nodes_per_hop)
    lvls = list()
    for _ in range(h):
        try:
            lvls.append(next(bfs_generator))
        except StopIteration:
            pass
    return set().union(*lvls)


def subgraph_extraction_labeling(ind, rel, A_list, h=1, enclosing_sub_graph=False, max_nodes_per_hop=None,
                                 node_information=None, max_node_label_value=None):
    # extract the h-hop enclosing subgraphs around link 'ind'
    A_incidence = incidence_matrix(A_list)
    A_incidence += A_incidence.T

    # could pack these two into a function
    root1_nei = get_neighbor_nodes(set([ind[0]]), A_incidence, h, max_nodes_per_hop)
    root2_nei = get_neighbor_nodes(set([ind[1]]), A_incidence, h, max_nodes_per_hop)

    subgraph_nei_nodes_int = root1_nei.intersection(root2_nei)
    subgraph_nei_nodes_un = root1_nei.union(root2_nei)

    # Extract subgraph | Roots being in the front is essential for labelling and the model to work properly.
    if enclosing_sub_graph:
        subgraph_nodes = list(ind) + list(subgraph_nei_nodes_int)
    else:
        subgraph_nodes = list(ind) + list(subgraph_nei_nodes_un)

    subgraph = [adj[subgraph_nodes, :][:, subgraph_nodes] for adj in A_list]

    labels, enclosing_subgraph_nodes = node_label_new(incidence_matrix(subgraph), max_distance=h)

    pruned_subgraph_nodes = np.array(subgraph_nodes)[enclosing_subgraph_nodes].tolist()
    pruned_labels = labels[enclosing_subgraph_nodes]

    if max_node_label_value is not None:
        pruned_labels = np.array([np.minimum(label, max_node_label_value).tolist() for label in pruned_labels])

    return pruned_subgraph_nodes, pruned_labels


def remove_nodes(A_incidence, nodes):
    idxs_wo_nodes = list(set(range(A_incidence.shape[1])) - set(nodes))
    return A_incidence[idxs_wo_nodes, :][:, idxs_wo_nodes]


def node_label_new(subgraph, max_distance=1):
    # an implementation of the proposed double-radius node labeling (DRNd   L)
    roots = [0, 1]
    sgs_single_root = [remove_nodes(subgraph, [root]) for root in roots]
    dist_to_roots = [
        np.clip(ssp.csgraph.dijkstra(sg, indices=[0], directed=False, unweighted=True, limit=1e6)[:, 1:], 0, 1e7) for
        r, sg in enumerate(sgs_single_root)]
    dist_to_roots = np.array(list(zip(dist_to_roots[0][0], dist_to_roots[1][0])), dtype=int)

    # dist_to_roots[np.abs(dist_to_roots) > 1e6] = 0
    # dist_to_roots = dist_to_roots + 1
    target_node_labels = np.array([[0, 1], [1, 0]])
    labels = np.concatenate((target_node_labels, dist_to_roots)) if dist_to_roots.size else target_node_labels

    enclosing_subgraph_nodes = np.where(np.max(labels, axis=1) <= max_distance)[0]
    return labels, enclosing_subgraph_nodes


def ssp_multigraph_to_dgl(graph, n_feats=None):
    """
    Converting ssp multigraph (i.e. list of adjs) to dgl multigraph.
    """

    g_nx = nx.MultiDiGraph()
    g_nx.add_nodes_from(list(range(graph[0].shape[0])))
    # Add edges
    for rel, adj in enumerate(graph):
        # Convert adjacency matrix to tuples for nx0
        nx_triplets = []
        for src, dst in list(zip(adj.tocoo().row, adj.tocoo().col)):
            nx_triplets.append((src, dst, {'type': rel}))
        g_nx.add_edges_from(nx_triplets)

    # make dgl graph
    g_dgl = dgl.from_networkx(g_nx, edge_attrs=['type'])
    # add node features
    if n_feats is not None:
        g_dgl.ndata['feat'] = torch.tensor(n_feats)

    return g_dgl


def prepare_features(subgraph, n_labels, r_label, max_n_label, n_feats=None):
    # One hot encode the node label feature and concat to n_featsure
    n_nodes = subgraph.number_of_nodes()
    label_feats = np.zeros((n_nodes, max_n_label[0] + 1 + max_n_label[1] + 1))
    label_feats[np.arange(n_nodes), n_labels[:, 0]] = 1
    label_feats[np.arange(n_nodes), max_n_label[0] + 1 + n_labels[:, 1]] = 1
    n_feats = np.concatenate((label_feats, n_feats), axis=1) if n_feats is not None else label_feats
    subgraph.ndata['feat'] = torch.FloatTensor(n_feats)

    head_id = np.argwhere([label[0] == 0 and label[1] == 1 for label in n_labels])
    tail_id = np.argwhere([label[0] == 1 and label[1] == 0 for label in n_labels])
    n_ids = np.zeros(n_nodes)
    n_ids[head_id] = 1  # head
    n_ids[tail_id] = 2  # tail
    subgraph.ndata['id'] = torch.FloatTensor(n_ids)

    subgraph.ndata['r_label'] = torch.LongTensor(np.ones(n_nodes) * r_label)

    return subgraph


def get_subgraphs(link, adj_list, dgl_adj_list, max_node_label_value, id2entity, entity2onto, entity2onto_pos, entity2onto_neg,
                  node_features=None,
                  kge_entity2id=None):
    # dgl_adj_list = ssp_multigraph_to_dgl(adj_list)

    subgraphs = []
    r_labels = []

    head, tail, rel = link[0], link[1], link[2]
    nodes, node_labels = subgraph_extraction_labeling((head, tail), rel, adj_list, h=params_.hop,
                                                        enclosing_sub_graph=params_.enclosing_sub_graph,
                                                        max_node_label_value=max_node_label_value)

    subgraph = dgl_adj_list.subgraph(nodes)
    subgraph.edata['label'] = torch.tensor(rel * np.ones(subgraph.edata['type'].shape), dtype=torch.long)

    edges_btw_roots = subgraph.edge_ids(0, 1, return_uv=True)[2].tolist()
    rel_link = np.nonzero(subgraph.edata['type'][edges_btw_roots] == rel)

    if rel_link.squeeze().nelement() == 0:
        subgraph.add_edges(0, 1)
        subgraph.edata['type'][-1] = torch.tensor(rel).type(torch.LongTensor)
        subgraph.edata['label'][-1] = torch.tensor(rel).type(torch.LongTensor)

    kge_nodes = [kge_entity2id[id2entity[n]] for n in nodes] if kge_entity2id else None
    n_feats = node_features[kge_nodes] if node_features is not None else None
    subgraph = prepare_features(subgraph, node_labels, rel, max_node_label_value, n_feats)

    # Add the onto node
    subgraph.ndata['onto'] = torch.LongTensor(entity2onto[subgraph.ndata['_ID']])
    subgraph.ndata['onto_pos'] = torch.LongTensor(entity2onto_pos[subgraph.ndata['_ID']])
    subgraph.ndata['onto_neg'] = torch.LongTensor(entity2onto_neg[subgraph.ndata['_ID']])

    subgraphs.append(subgraph)
    r_labels.append(rel)

    batched_graph = dgl.batch(subgraphs)
    r_labels = torch.LongTensor(r_labels)

    return (batched_graph, r_labels), len(nodes)


def get_rank(links):
    data, nodes = get_subgraphs(links, adj_list_all_, dgl_adj_list_all_, model_.gnn.max_label_value, id2entity_,
                         entity2onto_, entity2onto_pos_, entity2onto_neg_,
                         node_features_, kge_entity2id_)
    pos_head_scores, pos_tail_scores, neg_head_scores, neg_tail_scores = model_(data, cal_type=True, separate=True)

    if len(pos_head_scores) != 0:
        head_scores = torch.cat((pos_head_scores,neg_head_scores),dim=0)
        head_scores = torch.softmax(-head_scores,dim=0).squeeze(1).detach().numpy()
        head_rank_origin = []
        for idx in range(len(pos_head_scores)):
            head_rank_origin.append(int(np.argwhere(np.argsort(head_scores)[::-1] == idx) + 1))
        if params_.filter:
            head_rank = []
            for onto in head_rank_origin:
                move = 0
                for other in head_rank_origin:
                    if other < onto:
                        move += 1
                head_rank.append(onto - move)
        else:
            head_rank = head_rank_origin
    else:
        head_scores = np.array([])
        head_rank = []

    if len(pos_tail_scores) != 0:
        tail_scores = torch.cat((pos_tail_scores, neg_tail_scores), dim=0)
        tail_scores = torch.softmax(-tail_scores, dim=0).squeeze(1).detach().numpy()
        tail_rank_origin = []
        for idx in range(len(pos_tail_scores)):
            tail_rank_origin.append(int(np.argwhere(np.argsort(tail_scores)[::-1] == idx) + 1))
        if params_.filter:
            tail_rank = []
            for onto in tail_rank_origin:
                move = 0
                for other in tail_rank_origin:
                    if other < onto:
                        move += 1
                tail_rank.append(onto - move)
        else:
            tail_rank = tail_rank_origin
    else:
        tail_scores = np.array([])
        tail_rank = []

    return head_scores, head_rank, tail_scores, tail_rank, nodes


def save_to_file(triplets, id2entity, id2relation, file_name, entity2onto, entity2onto_neg,
                 id2onto):
    with open(os.path.join('./data', params.dataset, f'{file_name}_typing_head.txt'), "w") as f:
        for triplet in triplets:
            s, o, r = triplet
            if entity2onto.get(s):
                f.write('\t'.join([id2entity[s], id2relation[r], id2entity[o]]) + '\n')
                for t in entity2onto[s]:
                    f.write('\t'.join(['head pos:', id2onto[t]]) + '\n')
                for t in entity2onto_neg[s]:
                    f.write('\t'.join(['head neg:', id2onto[t]]) + '\n')

    with open(os.path.join('./data', params.dataset, f'{file_name}_typing_tail.txt'), "w") as f:
        for triplet in triplets:
            s, o, r = triplet
            if entity2onto.get(o):
                f.write('\t'.join([id2entity[s], id2relation[r], id2entity[o]]) + '\n')
                for t in entity2onto[o]:
                    f.write('\t'.join(['tail pos:', id2onto[t]]) + '\n')
                for t in entity2onto_neg[o]:
                    f.write('\t'.join(['tail neg:', id2onto[t]]) + '\n')

    with open(os.path.join('./data', params.dataset, f'{file_name}_typing.txt'), "w") as f:
        for triplet in triplets:
            s, o, r = triplet
            if entity2onto.get(s) or entity2onto.get(o):
                f.write('\t'.join([id2entity[s], id2relation[r], id2entity[o]]) + '\n')
            if entity2onto.get(s):
                for t in entity2onto[s]:
                    f.write('\t'.join(['head pos:', id2onto[t]]) + '\n')
                for t in entity2onto_neg[s]:
                    f.write('\t'.join(['head neg:', id2onto[t]]) + '\n')
            if entity2onto.get(o):
                for t in entity2onto[o]:
                    f.write('\t'.join(['tail pos:', id2onto[t]]) + '\n')
                for t in entity2onto_neg[o]:
                    f.write('\t'.join(['tail neg:', id2onto[t]]) + '\n')


def save_score_to_file(triplets, all_head_scores, all_tail_scores, id2entity, id2relation, file_name, entity2onto,
                       entity2onto_neg,
                       id2onto):
    with open(os.path.join('./data', params.dataset, f'{file_name}_typing_head_predictions.txt'), "w") as f:
        count = 0
        for triplet in triplets:
            s, o, r = triplet
            if entity2onto.get(s):
                f.write('\t'.join([id2entity[s], id2entity[o], id2relation[r]]) + '\n')
                for t in entity2onto[s]:
                    f.write('\t'.join(['head pos:', id2onto[t], str(all_head_scores[count])]) + '\n')
                    count += 1
                for t in entity2onto_neg[s]:
                    f.write('\t'.join(['head neg:', id2onto[t], str(all_head_scores[count])]) + '\n')
                    count += 1

    with open(os.path.join('./data', params.dataset, f'{file_name}_typing_tail_predictions.txt'), "w") as f:
        count = 0
        for triplet in triplets:
            s, o, r = triplet
            if entity2onto.get(o):
                f.write('\t'.join([id2entity[s], id2entity[o], id2relation[r]]) + '\n')
                for t in entity2onto[o]:
                    f.write('\t'.join(['tail pos:', id2onto[t], str(all_tail_scores[count])]) + '\n')
                    count += 1
                for t in entity2onto_neg[o]:
                    f.write('\t'.join(['tail neg:', id2onto[t], str(all_tail_scores[count])]) + '\n')
                    count += 1

    with open(os.path.join('./data', params.dataset, f'{file_name}_typing_all_predictions.txt'), "w") as f:
        count_head = 0
        count_tail = 0
        for triplet in triplets:
            s, o, r = triplet
            if entity2onto.get(s) or entity2onto.get(o):
                f.write('\t'.join([id2entity[s], id2entity[o], id2relation[r]]) + '\n')
            if entity2onto.get(s):
                for t in entity2onto[s]:
                    f.write('\t'.join(['head pos:', id2onto[t], str(all_head_scores[count_head])]) + '\n')
                    count_head += 1
                for t in entity2onto_neg[s]:
                    f.write('\t'.join(['head neg:', id2onto[t], str(all_head_scores[count_head])]) + '\n')
                    count_head += 1
            if entity2onto.get(o):
                for t in entity2onto[o]:
                    f.write('\t'.join(['tail pos:', id2onto[t], str(all_tail_scores[count_tail])]) + '\n')
                    count_tail += 1
                for t in entity2onto_neg[o]:
                    f.write('\t'.join(['tail neg:', id2onto[t], str(all_tail_scores[count_tail])]) + '\n')
                    count_tail += 1


def get_kge_embeddings(dataset, kge_model):
    path = './experiments/kge_baselines/{}_{}'.format(kge_model, dataset)
    node_features = np.load(os.path.join(path, 'entity_embedding.npy'))
    with open(os.path.join(path, 'id2entity.json')) as json_file:
        kge_id2entity = json.load(json_file)
        kge_entity2id = {v: int(k) for k, v in kge_id2entity.items()}

    return node_features, kge_entity2id


def main(params):
    model = torch.load(params.model_path, map_location='cpu')

    _, _, adj_list_all, dgl_adj_list_all, triplets, _, _, id2entity, id2relation, _, _, _, id2onto, _, _, entity2onto, entity2onto_neg, m_e2o, m_e2o_pos, m_e2o_neg = process_files(
        params.file_paths,
        params.file_paths_onto,
        params.file_paths_type,
        model.data2id,
        params.add_traspose_rels, params.use_test_subgraph, params.mode)

    node_features, kge_entity2id = get_kge_embeddings(params.dataset,
                                                      params.kge_model) if params.use_kge_embeddings else (None, None)

    triplets = [np.array(link) for link in triplets['links']]
    save_to_file(triplets, id2entity, id2relation, 'links', entity2onto, entity2onto_neg, id2onto)

    ranks = []
    all_head_scores = []
    all_tail_scores = []
    all_nodes = []

    with mp.Pool(processes=None, initializer=intialize_worker,
                 initargs=(
                         model, adj_list_all, dgl_adj_list_all, id2entity, m_e2o, m_e2o_pos, m_e2o_neg, params,
                         node_features,
                         kge_entity2id)) as p:
        for head_scores, head_rank, tail_scores, tail_rank, nodes in tqdm(p.imap(get_rank, triplets),
                                                                   total=len(triplets)):
            for rank in head_rank:
                ranks.append(rank)
            for rank in tail_rank:
                ranks.append(rank)

            all_head_scores += head_scores.tolist()
            all_tail_scores += tail_scores.tolist()

    save_score_to_file(triplets, all_head_scores, all_tail_scores, id2entity, id2relation, 'links', entity2onto,
                       entity2onto_neg, id2onto)

    isHit1List = [x for x in ranks if x <= 1]
    isHit5List = [x for x in ranks if x <= 5]
    isHit10List = [x for x in ranks if x <= 10]
    if len(ranks) != 0:
        hits_1 = len(isHit1List) / len(ranks)
        hits_5 = len(isHit5List) / len(ranks)
        hits_10 = len(isHit10List) / len(ranks)
        mrr = np.mean(1 / np.array(ranks))
    else:
        hits_1 = -1
        hits_5 = -1
        hits_10 = -1
        mrr = -1

    logger.info(f'MRR | Hits@1 | Hits@5 | Hits@10 : {mrr} | {hits_1} | {hits_5} | {hits_10}')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description='Testing script for hits@10')

    # Experiment setup params
    parser.add_argument("--experiment_name", "-e", type=str, default="fb_v2_margin_loss",
                        help="Experiment name. Log file with this name will be created")
    parser.add_argument("--dataset", "-d", type=str, default="FB237_v2",
                        help="Path to dataset")
    parser.add_argument("--mode", "-m", type=str, default="sample", choices=["sample", "all"],
                        help="Negative sampling mode")
    parser.add_argument("--use_kge_embeddings", "-kge", type=bool, default=False,
                        help='whether to use pretrained KGE embeddings')
    parser.add_argument("--kge_model", type=str, default="TransE",
                        help="Which KGE model to load entity embeddings from")
    parser.add_argument('--enclosing_sub_graph', '-en', type=bool, default=True,
                        help='whether to only consider enclosing subgraph')
    parser.add_argument("--hop", type=int, default=3,
                        help="How many hops to go while eextracting subgraphs?")
    parser.add_argument('--add_traspose_rels', '-tr', type=bool, default=False,
                        help='Whether to append adj matrix list with symmetric relations?')
    parser.add_argument('--use_test_subgraph', '-uts', type=bool, default=True,
                        help="Whether to use test triples to build subgraph")
    parser.add_argument('--filter', '-f', type=bool, default=False,
                        help="Whether to filter out the correct results")

    params = parser.parse_args()

    params.file_paths = {
        'graph': os.path.join('./data', params.dataset, 'train.txt'),
        'links': os.path.join('./data', params.dataset, 'test.txt')
    }

    params.file_paths_type = {'type': os.path.join('./data', params.dataset, 'type.txt'),
                                'type_test': os.path.join('./data', params.dataset, 'type_test.txt')}
    params.file_paths_onto = {'onto': os.path.join('./data', params.dataset, 'onto.txt')}

    params.model_path = os.path.join('experiments', params.experiment_name, 'best_graph_classifier.pth')

    file_handler = logging.FileHandler(
        os.path.join('experiments', params.experiment_name, f'log_rank_test_{time.time()}.txt'))
    logger = logging.getLogger()
    logger.addHandler(file_handler)

    logger.info('============ Initialized logger ============')
    logger.info('\n'.join('%s: %s' % (k, str(v)) for k, v
                          in sorted(dict(vars(params)).items())))
    logger.info('============================================')

    main(params)
