#                             -*- Mode: Python -*-
# MutationFinder.py
#

import string
import sys
import logging as log
import time
import datetime
import numpy as np

from . import Graph as ug
from . import PathQuant as upq
from .. utils import common as uc

class MutationFinder:
    def __init__(self, target_name, target_seq, jf, graphical, max_stack=500,
                 max_break=10):

        log.debug("Number of fusions to test: %d", len(target_seq))

        # Load the reference sequence and preparing ref k-mers
        self.first_kmer = "BigBang"
        self.last_kmer = "BigCrunch"
        
        self.start_kmers = set([seq[0:(jf.k)] for seq in target_seq])
        self.end_kmers = set([seq[-(jf.k):] for seq in target_seq])

        self.target_kmers = [[self.first_kmer] +\
                             uc.get_target_kmers(tar, jf.k, name) +\
                             [self.last_kmer]
                             for tar, name in zip(target_seq, target_name)]
        self.target_set = set([kmer for tar in self.target_kmers for kmer in tar])
        
        log.debug("Ref. set contains %d kmers.", len(self.target_set))
     
        self.target_name = target_name
        self.target_seq = target_seq
        self.jf = jf
        self.node_data = {}
        self.done = set()
        
        self.paths = []  # in case there aren't any
        
        self.max_stack = max_stack
        self.max_break = max_break
        
        # Register all k-mers from the reference
        for s in self.target_set:
            if not (s == self.first_kmer or s == self.last_kmer):
                self.node_data[s] = self.jf.query(s)
        #self.node_data[self.first_kmer] = np.nan
        #self.node_data[self.last_kmer] = np.nan

        # kmer walking from each k-mer of ref_seq
        self.done.update(self.target_set)
        
        for kmer in self.target_set:
            if kmer in self.end_kmers or kmer == self.first_kmer or kmer == self.last_kmer:
                continue
            self.__extend([kmer], 0)

        self.graph_analysis(graphical)


    def __extend(self, stack, breaks):
        """ Recursive depth first search """
        if len(stack) > self.max_stack:
            return
        cur_kmer = stack[-1]
        childs = self.jf.get_child(cur_kmer, forward=True)

        if len(childs) > 1:
            breaks += 1
            if breaks > self.max_break:
                return

        for child in childs:
            if child in self.done:
                self.done.update(stack)
                for p in stack:
                    self.node_data[p] = self.jf.query(p)
            else:
                self.__extend(stack + [child], breaks)


    def graph_analysis(self, graphical=False):

        self.paths = []
        kmer = self.node_data.keys()  # unique k-mers
        kmer.extend([self.first_kmer, self.last_kmer])
        
        self.first_kmer_index = kmer.index(self.first_kmer)  # These will be useful after they are
        self.last_kmer_index = kmer.index(self.last_kmer)    # deleted from the kmer list
        
        num_k = len(kmer)
        graph = ug.Graph(num_k)
        
        # The reference path, with node numbers
        target_index = []
        # Look up indexes of chopped up k-mers from individual sequences in the nodes graph
        for i in range(len(self.target_kmers)):
            target_index.append(map(lambda k: kmer.index(k), self.target_kmers[i]))
        
        log.debug("k-mer graph contains %d nodes.", num_k - 2)

        # Finds pairwise kmer continuation to build graph from nodes (all unique k-mers in all sequences)
        for i in range(num_k):
            for j in range(num_k):
                if i == j:
                    continue
                if kmer[i][1:] == kmer[j][:-1]:
                    weight = 1
                    graph[i, j] = weight

        # Attribute a weight of 0.001 to continuous k-mers in the same sequence
        for l in range(len(target_index)):  # for each sequence
            for k in range(len(target_index[l])-1):  # k-mers in sequence - 1
                i = target_index[l][k]
                j = target_index[l][k+1]
                graph[i, j] = 0.001
                # NOTE: A weight difference fold of 1000x might start causing problems for
                #       deletions that are > 31,000 bp long

        log.debug("BigBang=%d, BigCrunch=%d" % (self.first_kmer_index, self.last_kmer_index))
        for s in self.start_kmers:
            log.debug("Start kmer=%d (%s)" % (kmer.index(s), s))
        for e in self.end_kmers:
            log.debug("End kmer=%d (%s)" % (kmer.index(e), e))
        
        graph.init_paths(self.first_kmer_index, self.last_kmer_index)
        
        short_paths = graph.all_shortest()
        short_paths_full = [[p[1:-1] for p in short_paths]]  # keep as backup if we'll need the raw paths
        
        for i in range(len(self.target_kmers)):
            self.target_kmers[i] = self.target_kmers[i][1:-1]
        for i in range(len(target_index)):
            target_index[i] = target_index[i][1:-1]
        kmer = kmer[:-2]
        
        # Decompose path to get all alternatives from one start to one end 
        new_paths = []
        for p in short_paths:
            # Remove BigBang and BigCrunch from path
            if p[0] == self.first_kmer_index:
                p = p[1:]
            if p[-1] == self.last_kmer_index:
                p = p[:-1]
            nps = []
            ends = 0
            for k in p:
                if kmer[k] in self.start_kmers:
                    # Continue already started paths
                    for np in nps:
                        np.append(k)
                    # Start new path
                    nps.append([k])
                elif kmer[k] in self.end_kmers:
                    ends += 1
                    # Elongate and accept found paths
                    for np in nps:
                        np.append(k)
                        new_paths.append(tuple(np))
                    #np = []  # end if end kmer present
                    # NOTE: we can have more than 1 end k-mer if we're walking from the right
                elif nps:
                    for np in nps:
                        np.append(k)

        short_paths = new_paths
         
        # Organize paths and index them with their corresponding target
        # TODO: find out what to do with unassigned paths (alternative splicing function?)
        # TODO: find a way to assign path to one target when two or more share the same start and end
        short_paths_target = []
        for tar_id, target in enumerate(target_index):
            s_paths = [target]  # Keep a reference
            for path in short_paths:
                if target[0] == path[0] and target[-1] == path[-1]:
                    s_paths.append(path)
            short_paths_target.append(s_paths)
        short_paths = short_paths_target
        
        
        def get_seq(path, kmer, skip_prefix=True):
            path = list(path)
            if not path:
                # Deals with an empty sequence
                return ""

            if skip_prefix:
                seq = kmer[path[0]][-1]
            else:
                seq = kmer[path[0]]

            for i in path[1:]:
                seq += kmer[i][-1]
            return seq

        
        def get_name(a, b, offset=0):
            k = self.jf.k
            diff = graph.diff_path_without_overlap(a, b, k)
            deletion = diff[3]
            ins = diff[4]

            if (len(a)-len(deletion)+len(ins)) != len(b):
                sys.stderr.write(
                    "ERROR: %s %d != %d" % (
                        "mutation identification could be incorrect",
                        len(a) - len(deletion) + len(ins),
                        len(b)
                    )
                )

                # Fixes cases where we look at two copies of the same sequence
                deletion = diff[3]
                raise Exception()

            # Trim end sequence when in both del and ins:
            del_seq = get_seq(deletion, kmer, True)
            ins_seq = get_seq(ins, kmer, True)

            trim = 1
            while (len(del_seq[-trim:]) > 0 and
                    del_seq[-trim:] == ins_seq[-trim:]):
                trim += 1
            trim -= 1
            if trim != 0:
                del_seq = del_seq[:-trim]
                ins_seq = ins_seq[:-trim]

            if diff[0] == diff[1] and not diff[4]:
                return "Reference\t"
            else:
                variant = "Indel"
                # SNP have equal length specific sequences
                if diff[1] == diff[2]:
                    variant = "Substitution"

                # ITD have zero kmers in ref after full trimming.
                # However, this does not distinguish cases where there is
                # garbage between repeats.
                elif diff[0] == diff[5]:
                    variant = "ITD"
                elif len(del_seq) == 0 and len(ins_seq) != 0:
                    variant = "Insertion"
                elif len(del_seq) != 0 and len(ins_seq) == 0:
                    variant = "Deletion"

                return "{}\t{}:{}:{}".format(
                    variant,
                    diff[0] + k + offset,
                    (string.lower(del_seq) + "/" + ins_seq),
                    diff[1] + 1 + offset)


        def get_counts(path, kmer):
            counts = []
            for i in path:
                counts += [self.node_data[kmer[i]]]
            return counts


        # Quantify all paths independently
        individual = True
        if individual:
            for target_id in range(len(short_paths)):
                for path in short_paths[target_id]:
                    quant = upq.PathQuant(all_path=[path, target_index[target_id]],
                                          counts=self.node_data.values())
                 
                    quant.compute_coef()
                    quant.refine_coef()
                    quant.get_ratio()
                    
                    # Reference
                    if len(self.target_seq) == 1 and list(path) == target_index[target_id]:
                        quant.adjust_for_reference()
                    
                    paths_quant = quant.get_paths(
                        db_f=self.jf.filename,
                        target_name=self.target_name[target_id],
                        name_f=lambda path: get_name(target_index[target_id], path),
                        seq_f=lambda path: get_seq(path, kmer, skip_prefix=False),
                        target_path=target_index[target_id], info="vs_ref",
                        get_min_f=lambda path: min(get_counts(path, kmer)))
                    
                    self.paths += paths_quant
                
                if graphical:
                    import matplotlib.pyplot as plt
                
                    plt.figure(figsize=(10, 6))
                    for path in short_paths:
                        plt.plot(get_counts(path, kmer),
                                 label=get_name(target_index[target_id], path).replace("\t", " "))
                    plt.legend()
                    plt.show()

        # Quantify by cutting the sequence around mutations,
        # considering overlapping mutations as a cluster
        #
        cluster = True 
        if cluster:
            for target_id in range(len(short_paths)):
                variant_set = set(range(0, len(short_paths[target_id])))
                variant_diffs = []
                for variant in short_paths[target_id]:
                    diff = graph.diff_path_without_overlap(
                        target_index[target_id], variant, self.jf.k)
                    variant_diffs += [diff]

                def get_intersect(start, stop):
                    for var in variant_set:
                        if (variant_diffs[var][1] >= start and
                                variant_diffs[var][0] <= stop):
                            return var
                    return -1

                variant_groups = []
                while len(variant_set) > 0:
                    seed = variant_set.pop()
                    grp = [seed]
                    start = variant_diffs[seed][0]
                    stop = variant_diffs[seed][1]
                    variant = get_intersect(start, stop)
                    while variant != -1:
                        variant_set.remove(variant)
                        grp += [variant]
                        start = min(start, variant_diffs[variant][0])
                        stop = max(stop, variant_diffs[variant][1])
                        variant = get_intersect(start, stop)
                    variant_groups += [(start, stop, grp)]

                num_cluster = 0
                for var_gr in variant_groups:
                    if (len(var_gr[2]) == 1 and
                          list(short_paths[target_id][var_gr[2][0]]) == target_index[target_id]):
                        continue
                    num_cluster += 1

                    start = var_gr[0]
                    stop = var_gr[1]
                    var_size = max([abs(x[2]-x[1]+1) for x in [variant_diffs[v] for v in var_gr[2]]])
                    offset = max(0, start - var_size)
                    target_path = target_index[target_id][offset:stop]
                    clipped_paths = [target_path]
                    for var in var_gr[2]:
                        start_off = offset
                        stop_off = variant_diffs[var][2] + (stop - variant_diffs[var][1])
                        clipped_paths += [short_paths[target_id][var][start_off:stop_off]]

                    quant = upq.PathQuant(all_path=clipped_paths,
                                      counts=self.node_data.values())

                    quant.compute_coef()
                    quant.refine_coef()

                    quant.get_ratio()

                    paths_quant = (quant.get_paths(
                            db_f=self.jf.filename,
                            target_name=self.target_name[target_id],
                            name_f=lambda path: get_name(target_path, path, offset),
                            seq_f=lambda path: get_seq(path, kmer, skip_prefix=False),
                            target_path=target_path,
                            info="cluster %d n=%d" % (num_cluster, len(var_gr[2])),
                            get_min_f=lambda path: min(get_counts(path, kmer)),
                            start_off=start_off))

                    paths_quant
                    self.paths += paths_quant

                    if graphical:
                        import matplotlib.pyplot as plt

                        plt.figure(figsize=(10, 6))
                        for i in range(self.get_paths):
                            for path, ratio in zip(clipped_paths, quant.get_ratio()):
                                if path == target_path:
                                    plt.plot(get_counts(path, kmer),
                                        label="Reference")
                                else:
                                    plt.plot(get_counts(path, kmer),
                                        label=get_name(target_path, path, offset).split("\t")[0])
                        plt.legend()
                        plt.show()

    def get_paths(self):
        return self.paths

    @staticmethod
    def output_header():
        upq.PathQuant.output_header()
