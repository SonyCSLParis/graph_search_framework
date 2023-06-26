# -*- coding: utf-8 -*-
"""
Main class for the informed graph traversal
"""
import os
import json
import random
import multiprocessing as mp
from datetime import datetime
from collections import defaultdict
from tqdm import tqdm

import yaml
from ray.util.multiprocessing import Pool

import pandas as pd
from pandas.core.frame import DataFrame
from settings import FOLDER_PATH
from src.ranker import Ranker
from src.metrics import Metrics
from src.ordering import Ordering
from src.reasoner import Reasoner
from src.expansion import NodeExpansion
from src.selecting_node import NodeSelection
from src.hdt_interface import HDTInterface
from src.triply_interface import TriplInterface
from src.sparql_interface import SPARQLInterface
from doc.check_config_framework import CONFIG_TYPE_ERROR_MESSAGES \
    as config_error_messages


class GraphSearchFramework:
    """
    Main class to run the search from a config
    """
    def __init__(self, config: dict,
                 mode: str = "search_type_node_metrics",
                 node_selection: str = "all",
                 walk: str = "informed",
                 keep_only_last: bool = True,
                 reasoning: bool = False):
        """
        - `config`: config for the search,
        examples in `configs-example` folder
        - `mode`: type of search to run
            If == metrics_driven": config should contain `gold_standard`, `referents`
            and `type_metrics`
            Else: not implemented now
        - `node_selection`: expand all nodes corresponding to the best path,
            or only one random among them
            Values: `all` or `random``
        - `walk`: type of walk when exploring the graph
            * `informed`: regular one, with ranker for paths
            * `random`: no ranker or best path, select nodes randomly for next iteration
        - `keep_only_last`: boolean, keep only files from the latest iteration 
        (useful because very disk space consuming)

        Additional `max_uri`
        When the number of nodes visited gets higher than `max_uri`, the search stops
        -------

        WIP

        Differents usages:
        1 - searching types of nodes with metrics
        2 - searching types of nodes without metrics
        3 - Searching for one node
        4 - Simple exploration

        A - Random walk
        B - Informed walk (all 1-4 above)

        rdf_type: 1, 2

        gold_standard: 1
        referents: 1
        type_metrics: 1

        predicate_filter: optional, default []
        name_exp: optional, default taken from start node


        for below on filtering, check in dataset_config
        ordering/domain_range: optional, default 0
        filtering_what: optional, default 0
        filtering_where: optional, default 0
        filtering_when: optional, default 0
        filtering_who: optional, default 0
        start_date: only if filtering_when
        end_date: only if filtering_when

        start: all
        iterations: all
        type_ranking: B
        type_interface: all
        dataset_type: all
        dataset_path: all
        uri_limit: A
        """
        if not isinstance(keep_only_last, bool):
            raise ValueError("Param `keep_only_last` should be boolean")
        self.keep_only_last = keep_only_last

        possible_modes = ["search_type_node_metrics", "search_type_node_no_metrics",
                          "search_specific_node", "simple_search"]
        if mode not in possible_modes:
            raise ValueError(f"`mode` should be one of the followings: {possible_modes}")
        self.mode = mode

        self.possible_type_interface = ["triply", "hdt", "sparql_endpoint"]
        self.possible_type_ranking = [
            "pred_freq", "inverse_pred_freq", "entropy_pred_freq",
            "pred_object_freq", "inverse_pred_object_freq", "entropy_pred_object_freq"]
        self.config_error_messages = config_error_messages

        self._check_config(config=config, walk=walk)
        self.iterations = config["iterations"]
        self.type_interface = config["type_interface"]

        self.dataset_type = config["dataset_type"]
        with open(
            os.path.join(FOLDER_PATH, "dataset-config", f"{config['dataset_type']}.yaml"),
            encoding='utf-8') as file:
            self.dataset_config = yaml.load(file, Loader=yaml.FullLoader)

        self.config = config
        self.rdf_type = config["rdf_type"] if "rdf_type" in config else []
        self.predicate_filter = config["predicate_filter"] if "predicate_filter" in config else []
        self.start = config["start"]

        # TEMPORAL FILTER
        if ("start_date" and "end_date") in config:
            self.dates = [config["start_date"], config["end_date"]]
        else:
            self.dates = None

        if "exclude_category" in config:
            filter_kb = config["exclude_category"]
        else:
            filter_kb = 1
        if self.type_interface == "triply":
            self.interface = TriplInterface()
        elif self.type_interface == "sparql_endpoint":
            self.interface = SPARQLInterface(dataset_config=self.dataset_config, dates=self.dates,
                                             default_pred=self.get_pred_interface(),
                                             filter_kb=filter_kb,
                                             sparql_endpoint=config["sparql_endpoint"])
        else:  # type_interface == "hdt"
            nested = config["nested_dataset"] if "nested_dataset" in config else 1
            pred = self.get_pred_interface()
            self.interface = HDTInterface(filter_kb=filter_kb, folder_hdt=config["dataset_path"],
                                          dataset_config=self.dataset_config, nested_dataset=nested,
                                          default_pred=pred)

        self.subgraph = pd.DataFrame(columns=[
            "subject", "predicate", "object", "type_df", "iteration"])
        self.subgraph_info = {}

        self.pending_nodes_ingoing = pd.DataFrame(columns=["subject", "predicate", "object"])
        self.pending_nodes_outgoing = pd.DataFrame(columns=["subject", "predicate", "object"])
        self.info = pd.DataFrame(columns=["path", "iteration", "tot"] + \
            [x for elt in self.rdf_type for x in [f"{elt[0]}"] ])

        self.nb_cpu = mp.cpu_count()
        self.paths = []

        # RANKER
        possible_walks = ["random", "informed"]
        if walk not in possible_walks:
            raise ValueError(f"`walk` should be one of the followings: {possible_walks}")
        self.walk = walk
        self.type_ranking = config["type_ranking"] if "type_ranking" in config else "random"
        if self.walk == "informed":
            self.ranker = Ranker(type_ranking=self.type_ranking)
            self.uri_limit = None
        else:
            self.ranker = None
            self.uri_limit = config["uri_limit"]
        # ======
        # NODE SELECTION (only if walk == "informed")
        possible_node_selection = ["random", "all"]
        if walk == "informed" and node_selection not in possible_node_selection:
            raise ValueError(
                "`node_selection` should be one of the followings:", possible_node_selection)
        self.node_selection_type = node_selection if walk == "informed" else None
        self.node_selection = NodeSelection(mode=node_selection) if walk == "informed" else None
        # ======


        self.nodes_expanded = []
        self.occurence = defaultdict(int)
        self.to_expand = None
        self.score_expansion = None
        self.nodes_expanded_per_iter = pd.DataFrame(columns=["iteration", "node_expanded"])
        self.expanded = pd.DataFrame(columns=[
            "iteration", "path_expanded", "nb_expanded", "node_expanded", "score"])
        self.discarded = pd.DataFrame(columns=["iteration", "node_discarded"])

        # METRICS
        # Metrics part, only if mode == "metrics_driven"
        # Will compute metrics at each iteration (standard are: precision, recall, f1)
        if self.mode == "search_type_node_metrics":
            config_metrics = {
                "referents": config["referents"], "type_metrics": config["type_metrics"],
                "gold_standard": config['gold_standard']
            }
            self.metrics = Metrics(config_metrics=config_metrics)
            self.metrics_data = {}

        # self.plotter = Plotter()


        ordering_domain_range = config["ordering"]["domain_range"] if \
            "ordering" in config and "domain_range" in config["ordering"] else 0
        self.ordering = Ordering(interface=self.interface,
                                 domain_range=ordering_domain_range,
                                 focus_for_search=[x[1] for x in self.rdf_type])

        if "filtering" in config and "what" in config["filtering"] and \
            config["filtering"]["what"]:
            self.predicate_filter += [self.dataset_config["rdf_type"]]


        if reasoning:
            self.reasoner = Reasoner()
        else:
            self.reasoner = None
        self.node_expander = NodeExpansion(rdf_type=self.rdf_type,
                                           interface=self.interface,
                                           reasoner=self.reasoner,
                                           args_filtering=self.get_config_filtering(
                                            config=config, dataset_config=self.dataset_config))

        self.path_node_to_start = defaultdict(list)
        self.path_found = False
        self.it_found = None

        self.folder_name_suffix = \
            self.get_exp_name(config=config)
        self.save_folder = self._add_save_info()

        # max_uri
        self.max_uri = config["max_uri"] if "max_uri" in config else float("inf")

        self.last_iteration = None

    def get_pred_interface(self) -> list[(str, str)]:
        """ Specific predicates for retrieving info with interface """
        res = []
        for pred in [x for x in ["point_in_time", "start_dates", "end_dates"] \
            if x in self.dataset_config]:
            res += self.dataset_config[pred]
        if "rdf_type" in self.dataset_config:
            res += [self.dataset_config["rdf_type"]]
        return res

    @staticmethod
    def get_config_filtering(config: dict, dataset_config: dict) -> dict:
        """ Create config for Filtering module in NodeExpansion """
        filtering_when = config["filtering"]["when"] if \
            "filtering" in config and "when" in config["filtering"] else 0
        filtering_where = config["filtering"]["where"] if \
            "filtering" in config and "where" in config["filtering"] else 0
        filtering_who = config["filtering"]["who"] if \
            "filtering" in config and "who" in config["filtering"] else 0

        return {
            "when": filtering_when,
            "where": filtering_where,
            "who": filtering_who,
            "point_in_time": dataset_config.get("point_in_time"),
            "start_dates": dataset_config.get("start_dates"),
            "end_dates": dataset_config.get("end_dates"),
            "places": dataset_config.get("places"),
            "people": dataset_config.get("person"),
            "dataset_type": dataset_config.get("config_type"),
        }

    def _check_config(self, config: dict, walk: str):
        """

        gold_standard: 1
        referents: 1
        type_metrics: 1

        """
        # MANDATORY FOR ALL MODES
        # `config`
        if not isinstance(config, dict):
            raise TypeError("`config` param type should be dict`")

        if "start" not in config:
            raise ValueError(self.config_error_messages['start'])
        if not isinstance(config["start"], str):
            raise TypeError(self.config_error_messages['start'])

        if "iterations" not in config:
            raise ValueError(self.config_error_messages['iterations'])
        if not isinstance(config["iterations"], int):
            raise TypeError(self.config_error_messages['iterations'])

        if walk == "informed" and "type_ranking" not in config:
            raise ValueError(self.config_error_messages['type_ranking'])
        if walk == "informed" and config["type_ranking"] not in self.possible_type_ranking:
            raise TypeError(self.config_error_messages['type_ranking'])

        if walk == "random" and "uri_limit" not in config:
            raise ValueError(self.config_error_messages["uri_limit"])
        if walk == "random" and not \
            (isinstance(config["uri_limit"], int) or config["uri_limit"] == "all"):
            raise TypeError(self.config_error_messages["uri_limit"])

        if "type_interface" not in config:
            raise ValueError(self.config_error_messages['type_interface'])
        if config["type_interface"] not in self.possible_type_interface:
            raise TypeError(self.config_error_messages['type_interface'])

        if "dataset_type" not in config:
            raise ValueError(self.config_error_messages['dataset_type'])
        possible_dataset_type = [x.replace(".yaml", "") for x in os.listdir(os.path.join(FOLDER_PATH, "dataset-config"))]
        if config["dataset_type"] not in possible_dataset_type:
            raise TypeError(self.config_error_messages['dataset_type'])

        if config["type_interface"] == "hdt":
            if "dataset_path" not in config:
                raise ValueError(self.config_error_messages['dataset_path'])
            if not isinstance(config["dataset_path"], str):
                raise TypeError(self.config_error_messages['dataset_path'])

        if config["type_interface"] == "sparql_endpoint":
            if "sparql_endpoint" not in config:
                raise ValueError(self.config_error_messages['sparql_endpoint'])
            if not isinstance(config["sparql_endpoint"], str):
                raise TypeError(self.config_error_messages['sparql_endpoint'])

        # OPTIONAL FOR ALL
        # `predicate_filter`
        if "predicate_filter" in config:
            if not isinstance(config["predicate_filter"], list) or \
                any(not isinstance(elt, str) for elt in config["predicate_filter"]):
                raise TypeError(self.config_error_messages['predicate_filter'])

        # `ordering` (`domain_range`), `filtering` (`what`, `where`, `when`, `who`)
        for k_p, v_p in [
            ("ordering", "domain_range"), ("filtering", "what"),
            ("filtering", "when"), ("filtering", "where")
        ]:

            if k_p in config and \
                isinstance(config[k_p], dict) and v_p in config[k_p]:
                if config[k_p][v_p] not in [0, 1]:
                    raise TypeError(self.config_error_messages[k_p][v_p])

        # `start_date`, `end_date` (for filtering params, checked just above)
        if "filtering" in config and config["filtering"].get("when"):
            for date in ["start_date", "end_date"]:
                if date not in config:
                    raise TypeError(self.config_error_messages[date])
                try:
                    datetime(int(config[date][:4]), int(config[date][5:7]), int(config[date][8:10]))
                except Exception as type_error:
                    raise TypeError(self.config_error_messages[date]) from type_error

        # `name_exp`
        if "name_exp" in config:
            if not isinstance(config["name_exp"], str):
                raise TypeError(self.config_error_messages['name_exp'])

        if "max_uri" in config:
            if not isinstance(config["max_uri"], int):
                raise TypeError(self.config_error_messages['max_uri'])


        # MANDATORY FOR MODE 1: search type + metrics
        # `rdf_type` (for search type and if ordering domain range)
        if (self.mode in ['targe_type_node_metrics', 'search_type_node_no_metrics']) or \
            ("ordering" in config and config["ordering"].get("domain_range")):
            if "rdf_type" not in config:
                raise ValueError(self.config_error_messages['rdf_type'])
            if not isinstance(config["rdf_type"], list) or \
                any(not isinstance(elt, tuple) for elt in config["rdf_type"]) or \
                any(not isinstance(k, str) \
                or not isinstance(v, str) for k, v in config['rdf_type']):
                raise TypeError(self.config_error_messages['rdf_type'])


        # MANDATORY FOR MODE 2: search type + no metrics

        # MANDATORY FOR MODE 3: search specific node

        # MANDATORY FOR MODE 4: simple exploration

        # MANDATORY ON CONDITIONS

    def get_exp_name(self, config: dict) -> str:
        """ Get experiment name, depending on parameters """
        exp = config["name_exp"] if "name_exp" in config else config["start"].split("/")[-1].lower()
        elts = [self.walk, config['dataset_type'], exp,
                str(config["iterations"]), self.type_ranking]
        domain_range = "domain_range" if \
            config.get('ordering') and \
                config.get('ordering').get('domain_range') \
                else ""
        elts.append(domain_range)
        if config.get('filtering'):
            what = "what" if \
                config.get('filtering').get('what') else ""
            where = "where" if \
                config.get('filtering').get('where') else ""
            when = "when" if \
                config.get('filtering').get('when') else ""
            who = "who" if \
                config.get('filtering').get('who') else ""
            elts += [what, where, when, who]

        if self.dataset_type == "dbpedia":  # wikilink for DBpedia only
            wikilink = "wikilink" if "http://dbpedia.org/ontology/wikiPageWikiLink" \
                in config["predicate_filter"] else ""
            elts.append(wikilink)
        cat = "with_category" if config.get("exclude_category") == 0 else "without_category"
        elts.append(cat)
        elts += ["uri", "iter", str(config.get("uri_limit")) \
            if config.get('uri_limit') else \
                '',  "max", str(config.get("max_uri")) if config.get('max_uri') else 'inf']

        return "_".join(elts)

    def select_nodes_to_expand(self, iteration: int) -> list[str]:
        """ Accessible call to _select_nodes_to_expand"""
        return self._select_nodes_to_expand(iteration)

    def _select_nodes_to_expand(self, iteration: int) -> list[str]:
        if iteration == 1:  # INIT state: only starting node
            return [self.start], [""]
        if self.walk == "informed":  # choosing nodes based on best path for next iteration
            for elt in ["1-", '2-', "3-"]:
                if self.to_expand.startswith(elt):
                    self.to_expand = self.to_expand.replace(elt, "")
            path = [self.to_expand]

            # Gettings args for next iteration
            if (";" in self.to_expand) and ("ingoing" in self.to_expand):
                splitted = self.to_expand.replace('ingoing-', '').split(";")
                pred, obj = splitted[0], ";".join(splitted[1:])

                if self.reasoner:
                    obj_same_as = self.reasoner.cached.get(
                        obj, self.reasoner.get_same_as_nodes(self.interface, obj))
                else:
                    obj_same_as = [obj]

                nodes = list(
                    self.pending_nodes_ingoing[
                        (self.pending_nodes_ingoing.predicate.isin([pred, str(pred)])) & \
                        (self.pending_nodes_ingoing.object.isin(obj_same_as + [str(x) for x in obj_same_as]))].subject.values)
            elif (";" in self.to_expand) and ("outgoing" in self.to_expand):
                subj, pred = self.to_expand.replace('outgoing-', '').split(";")

                if self.reasoner:
                    subj_same_as = self.reasoner.cached.get(
                        subj, self.reasoner.get_same_as_nodes(self.interface, subj))
                else:
                    subj_same_as = [subj]

                nodes = list(
                    self.pending_nodes_outgoing[
                        (self.pending_nodes_outgoing.subject.isin(subj_same_as + [str(x) for x in subj_same_as])) & \
                        (self.pending_nodes_outgoing.predicate \
                            .isin([pred, str(pred)]))].object.values)
            else:
                # no need to add owl:sameas consideration, since it means
                # the metric is object-independant
                # print(self.pending_nodes_ingoing.predicate.unique())
                nodes = list(self.pending_nodes_ingoing[\
                    self.pending_nodes_ingoing.predicate.isin(
                        [self.to_expand])].subject.values) + \
                        list(self.pending_nodes_outgoing[\
                    self.pending_nodes_outgoing.predicate.isin(
                        [self.to_expand])].object.values)
                # print(nodes)
            print(nodes)
            nodes = list(set(node for node in nodes if node not in self.nodes_expanded))

            # Sampling nodes if too many compared to max uri
            if len(nodes) > self.max_uri - len(self.nodes_expanded):
                random.seed(23)
                nodes = random.sample(nodes, k=self.max_uri - len(self.nodes_expanded))

            if nodes:
                nodes, _ = self.node_selection(nodes)

        else:  # self.walk == "random"
            candidates = set(list(self.pending_nodes_ingoing.subject.unique()) + \
                list(self.pending_nodes_outgoing.object.unique()))
            candidates = {node for node in candidates if node not in self.nodes_expanded}
            if isinstance(self.uri_limit, int):  # sampling a subset of nodes
                if len(list(candidates)) < self.uri_limit:
                    nodes = list(candidates)
                else:
                    random.seed(23)
                    nodes = random.sample(list(candidates), k=self.uri_limit)
            else:  # take all nodes, BFS setting
                # Sampling nodes if too many compared to max uri
                if len(candidates) > self.max_uri - len(self.nodes_expanded):
                    random.seed(23)
                    nodes = random.sample(candidates, k=self.max_uri - len(self.nodes_expanded))
                else:
                    nodes = list(candidates)
            path = self._extract_paths_from_candidates(nodes)


        return nodes, path

    def _extract_paths_from_candidates(self, nodes: list[str]) -> str:
        """ Extract paths from randomly sampled nodes """
        path = []
        for node in nodes:
            subset_ingoing = self.pending_nodes_ingoing[\
                self.pending_nodes_ingoing.subject.isin([node])]
            subset_outgoing = self.pending_nodes_outgoing[\
                self.pending_nodes_outgoing.object.isin([node])]

            if subset_ingoing.shape[0] == 0:  # only outgoing
                sample = subset_outgoing.sample().iloc[0]
                path.append(f"outgoing-{sample.subject};{sample.predicate}")
            elif subset_outgoing.shape[0] == 0:  # only ingoing
                sample = subset_ingoing.sample().iloc[0]
                path.append(f"ingoing-{sample.predicate};{sample.object}")
            else:  # ingoing and outgoing
                type_path = random.sample(["in", "out"], k=1)
                if type_path == "in":
                    sample = subset_ingoing.sample().iloc[0]
                    path.append(f"ingoing-{sample.predicate};{sample.object}")
                else:
                    sample = subset_ingoing.sample().iloc[0]
                    path.append(f"ingoing-{sample.predicate};{sample.object}")

        return path

    def _expand_one_node(self, args: dict) \
        -> (DataFrame, DataFrame, DataFrame, DataFrame, list[str]):
        res, self.reasoner = self.node_expander(args=args, dates=self.dates)
        return res

    def _update_nodes_expanded(self, iteration:int, nodes: list[str]) -> DataFrame:

        self.nodes_expanded_per_iter = pd.concat(
            [self.nodes_expanded_per_iter,
             pd.DataFrame([[iteration, nodes]], columns=["iteration", "node_expanded"])],
            ignore_index=True
        )

    def run_one_iteration(self, iteration: int) \
        -> (list[(DataFrame, DataFrame, DataFrame, DataFrame, list[str])], list[str], str):
        """ Running one iteration of the search framework """
        nodes_to_expand, path = self._select_nodes_to_expand(iteration)
        self._update_nodes_expanded(iteration=iteration, nodes=nodes_to_expand)

        if self.type_interface == '':
            pool = Pool(self.nb_cpu)
            output = pool.map(self._expand_one_node,
                                [{
                                    "node": node,
                                    "path": path,
                                    "predicate": self.predicate_filter,
                                    "iteration": iteration,
                                    "visited_nodes": list(set(self.nodes_expanded + nodes_to_expand))
                                    } for node in nodes_to_expand])
            pool.close()
            pool.join()

        else:  # type_interface == 'hdt'
            output = []
            for i, args in enumerate([{"node": node,
                                    "path": path,
                                    "predicate": self.predicate_filter,
                                    "iteration": iteration,
                                    "visited_nodes": list(set(self.nodes_expanded + nodes_to_expand))
                                    } for node in nodes_to_expand]):
                print(f"Processing node {i+1}/{len(nodes_to_expand)}\t{nodes_to_expand[i]}")
                self.nodes_expanded.append(args["node"])
                output.append(self._expand_one_node(args))

        return output, nodes_to_expand, path

    def update_occurence(self, ingoing: DataFrame,
                         outgoing: DataFrame, occurence: dict) -> dict:
        """ Accessible call to _update_occurence """
        return self._update_occurence(ingoing, outgoing, occurence)

    def _get_nb(self, superclass: str, pred: str):
        if any(x in superclass for x in [y[1] for y in self.rdf_type]):
            return "1"
        if pred in []:
            return "2"
        return "3"

    def helper_ent_ref(self, node):
        """ Retrieving referent of entity """
        if self.reasoner:
            if node not in self.reasoner.referents:
                self.reasoner.get_same_as_nodes(
                    interface=self.interface, node=node)
            return self.reasoner.referents[node]
            
        return node

    def _update_occurence(self, ingoing: DataFrame,
                          outgoing: DataFrame, occurence: dict) -> dict:
        """
        Updating occurences for future path ranking
        In any case: adding info about the type of predicate
        1 = relevant superclass | 2 = filtered predicate | 3 = other
        - If path on predicate only: {1-3} + pred
        - If pred_object: adding whether ingoing or outgoing
        """
        if self.type_ranking in ["pred_freq", "entropy_pred_freq",
                                 "inverse_pred_freq"]:  # predicate
            for _, row in ingoing.iterrows():
                nb_order = self._get_nb(superclass=row.superclass, pred=row.predicate)
                occurence[f"{nb_order}-{str(row.predicate)}"] += 1
            for _, row in outgoing.iterrows():
                nb_order = self._get_nb(superclass=row.superclass, pred=row.predicate)
                occurence[f"{nb_order}-{str(row.predicate)}"] += 1
        if self.type_ranking in ["pred_object_freq",
                                 "entropy_pred_object_freq",
                                 "inverse_pred_object_freq"]:  # subject predicate
            for _, row in ingoing.iterrows():
                nb_order = self._get_nb(superclass=row.superclass, pred=row.predicate)
                occurence[f"{nb_order}-ingoing-{str(row.predicate)};{self.helper_ent_ref(str(row.object))}"] += 1
            for _, row in outgoing.iterrows():
                nb_order = self._get_nb(superclass=row.superclass, pred=row.predicate)
                occurence[f"{nb_order}-outgoing-{self.helper_ent_ref(str(row.subject))};{str(row.predicate)}"] += 1
        return occurence

    def update_occurrence_after_expansion(self, occurence: dict, to_expand: str) -> dict:
        """ Updating path count:
        - if node selection is all nodes corresponding to a path, then removing that path
        - else decreasing it by one """
        if self.node_selection_type == "random":
            return defaultdict(int, {k: v if v != to_expand else v-1 for k, v in occurence.items()})
        return defaultdict(int, {k: v for k, v in occurence.items() if k != to_expand})

    def merge_outputs(self, output: list, iteration: int, info: dict) -> dict:
        """ Gather outputs from each of the nodes expanded """
        curr_discarded = []
        for subgraph_ingoing, path_ingoing, subgraph_outgoing, path_outgoing, to_discard in output:
            subgraph_ingoing["iteration"] = iteration
            subgraph_outgoing["iteration"] = iteration
            self._merge_outputs_single_run(subgraph_ingoing, path_ingoing,
                                           subgraph_outgoing, path_outgoing, info, iteration)

            curr_discarded += to_discard

        self.discarded = pd.concat(
            [self.discarded,
             pd.DataFrame([[iteration, list(set(curr_discarded))]],
                          columns=["iteration", "node_discarded"])],
            ignore_index=True
        )

        if self.walk == "informed":
            self.to_expand, self.score_expansion = self.ranker(occurences=self.occurence)
            if self.to_expand:
                self.occurence = self.update_occurrence_after_expansion(
                    occurence=self.occurence, to_expand=self.to_expand)
        self.pending_nodes_ingoing = self.pending_nodes_ingoing[\
            ~self.pending_nodes_ingoing.subject.isin(self.nodes_expanded)]
        self.pending_nodes_outgoing = self.pending_nodes_outgoing[\
            ~self.pending_nodes_outgoing.object.isin(self.nodes_expanded)]

        return info

    def _merge_outputs_single_run(self, subgraph_ingoing: DataFrame,
                                  path_ingoing: DataFrame,
                                  subgraph_outgoing: DataFrame,
                                  path_outgoing: DataFrame,
                                  info: dict, iteration: int):
        self.subgraph = pd.concat([self.subgraph, subgraph_ingoing], axis=0)
        self.subgraph = pd.concat([self.subgraph, subgraph_outgoing], axis=0)

        # Pre-ordering step (remove non relevant predicates)
        # 1st = add info on predicates (using domain/range information)
        path_ingoing, info = self.ordering(triple_df=path_ingoing, type_node="ingoing",
                                            info=info, iteration=iteration)
        path_outgoing, info = self.ordering(triple_df=path_outgoing, type_node="outgoing",
                                            info=info, iteration=iteration)

        self.pending_nodes_ingoing = pd.concat(
            [self.pending_nodes_ingoing, path_ingoing], axis=0)
        self.pending_nodes_outgoing = pd.concat(
            [self.pending_nodes_outgoing, path_outgoing], axis=0)
        # self.info = pd.concat([self.info, info], axis=0)

        if self.walk == "informed":
            self.occurence = self._update_occurence(ingoing=path_ingoing,
                                                    outgoing=path_outgoing,
                                                    occurence=self.occurence)

    def _add_save_info(self) -> str:
        date_begin = datetime.now()
        date = '-'.join([str(date_begin)[:10], str(date_begin)[11:19]])

        folder_path = os.path.join(FOLDER_PATH, "experiments")
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        save_folder = os.path.join(folder_path,
                                 f"{date}-{self.folder_name_suffix}")
        if os.path.exists(save_folder):
            raise ValueError("Folder to save data already exists, re creating one")
        os.makedirs(save_folder)
        return save_folder

    def add_subgraph_info(self, iteration: int):
        """ Tracking # of events + unique events found """
        size = self.subgraph.shape[0]
        unique = len(set([str(e) for e in self.subgraph[self.subgraph.type_df == "ingoing"] \
                    .subject.unique()] + \
                    [str(e) for e in self.subgraph[self.subgraph.type_df == "outgoing"] \
                        .object.unique()]))
        self.subgraph_info[iteration] = dict(subgraph_nb_event=size,
                                             subgraph_nb_event_unique=unique)

    def _update_path(self, output: list, end_node: str) -> bool:
        """ Updating paths between visited node and starting node
        self.mode == 'simple_search' -> checking all paths
        self.mode == "search_specific_node" -> additionally check if node was found """
        found_node = False
        for i in tqdm(range(len(output))):
            _, path_ingoing, _, path_outgoing, _ = output[i]
            for _, row in path_ingoing.iterrows():
                if row.subject == end_node:
                    found_node = True

            for _, row in path_outgoing.iterrows():
                if row.object == end_node:
                    found_node = True
        return found_node

    def __call__(self, end_node: str = ""):
        """ end_node necessary only if self.mode == 'search_specific_node' """
        start = datetime.now()
        metadata = {"start": str(start), "node_start": self.start}

        if self.mode == "search_specific_node" and end_node == "":
            raise ValueError(f"For mode {self.mode}, `end_node` should not be empty")
        if self.mode == "search_specific_node":
            metadata.update({"path_found": False,
                             "node_searched": end_node})

        with open(f"{self.save_folder}/config.json", "w", encoding='utf-8') as openfile:
            json.dump(self.config, openfile,
                      indent=4)
        self.expanded = pd.DataFrame(columns=[
            "iteration", "path_expanded", "nb_expanded", "node_expanded", "score"])
        self.metrics_data = {}
        self.info = {}
        best_fone = 0
        found_node = False  # only if looking for a specific node

        for i in range(1, self.iterations+1):
            self.last_iteration = i
            print(i, "/", self.iterations)
            print(f"Iteration {i} started at {datetime.now()}")
            output, nodes_to_expand, path = self.run_one_iteration(iteration=i)
            self.info = self.merge_outputs(output=output, iteration=i, info=self.info)

            self.add_subgraph_info(iteration=i)

            if self.keep_only_last and i > 1:
                if self.rdf_type:
                    try:
                        os.remove(f"{self.save_folder}/{i-1}-subgraph.csv")
                    except OSError:
                        pass
                try:
                    os.remove(f"{self.save_folder}/{i-1}-pending_nodes_ingoing.csv")
                    os.remove(f"{self.save_folder}/{i-1}-pending_nodes_outgoing.csv")
                except OSError:
                    pass

            if self.rdf_type:
                self.subgraph.to_csv(f"{self.save_folder}/{i}-subgraph.csv")

            self.pending_nodes_ingoing.to_csv(
                f"{self.save_folder}/{i}-pending_nodes_ingoing.csv")
            self.pending_nodes_outgoing.to_csv(
                f"{self.save_folder}/{i}-pending_nodes_outgoing.csv")

            if self.walk == "informed":
                # if walk is random, no occurences used for best path choosing
                if self.keep_only_last and i > 1:
                    try:
                        os.remove(f"{self.save_folder}/{i-1}-occurences.json")
                    except OSError:
                        pass

                with open(f"{self.save_folder}/{i}-occurences.json", "w", encoding='utf-8') \
                        as openfile:
                    json.dump(self.occurence, openfile, indent=4)

            if self.mode in ["simple_search", "search_specific_node"]:
                found_node = self._update_path(output=output, end_node=end_node)
                if self.keep_only_last and i > 1:
                    try:
                        os.remove(f"{self.save_folder}/{i}-paths.json")
                    except OSError:
                        pass

                with open(f"{self.save_folder}/{i}-paths.json", "w", encoding='utf-8') \
                        as openfile:
                    json.dump(self.path_node_to_start, openfile, indent=4)

            self.expanded.to_csv(f"{self.save_folder}/expanded.csv")

            events_found = \
                [str(e) for e in self.subgraph[self.subgraph.type_df == "ingoing"] \
                    .subject.unique()] + \
                    [str(e) for e in self.subgraph[self.subgraph.type_df == "outgoing"] \
                        .object.unique()]

            # METRICS
            if self.mode == "search_type_node_metrics":
                self.metrics_data = self.metrics.update_metrics_data(
                    metrics_data=self.metrics_data, iteration=i, found=events_found)

                with open(f"{self.save_folder}/metrics.json", "w", encoding='utf-8') as openfile:
                    json.dump(self.metrics_data, openfile, indent=4)

                current_metrics = self.metrics_data[i]

                if current_metrics["f1"] > best_fone:
                    metadata.update({
                        "best_f1": current_metrics['f1'],
                        "best_corresponding_precision": current_metrics['precision'],
                        "best_corresponding_recall": current_metrics['recall'],
                        "best_f1_it_nb": i
                    })
                    best_fone = current_metrics["f1"]

                metadata.update({
                    "last_f1":  current_metrics["f1"],
                    "last_precision":  current_metrics["precision"],
                    "last_recall":  current_metrics["recall"],
                    "last_it": i
                })

            metadata.update({"nb_expanded": len(self.nodes_expanded)})
            metadata.update({"end": str(datetime.now())})

            with open(f"{self.save_folder}/metadata.json", "w", encoding="utf-8") as openfile:
                json.dump(metadata, openfile, indent=4)
            print(f"Iteration {i} finished at {datetime.now()}\n=====")

            if found_node:
                print(f"Node {end_node} was found, stopping search. " + \
                    "Path can be found in {i}-paths.json")
                self.path_found = True
                self.it_found = i
                metadata.update({"path_found": True, "path_found_iteration": i,
                                 "path": self.path_node_to_start[end_node]})
                with open(f"{self.save_folder}/metadata.json", "w", encoding="utf-8") as openfile:
                    json.dump(metadata, openfile, indent=4)
                break

            candidates = set(list(self.pending_nodes_ingoing.subject.unique()) + \
                list(self.pending_nodes_outgoing.object.unique()))
            candidates = {node for node in candidates if node not in self.nodes_expanded}

            if len(self.nodes_expanded) >= self.max_uri:
                print(f"More than {self.max_uri} nodes were expanded, ",
                      f"finishing process at {datetime.now()} due to parameter `max_uri`\n=====")
                self.expanded.to_csv(f"{self.save_folder}/expanded.csv")
                break

            elif (self.walk == "informed" and self.to_expand):
                self.expanded = pd.concat(
                    [self.expanded,
                     pd.DataFrame(
                        [[i, self.to_expand, len(nodes_to_expand),
                          nodes_to_expand, self.score_expansion]],
                    columns=["iteration", "path_expanded", "nb_expanded",
                             "node_expanded", "score"])],
                    ignore_index=True
                )
                self.expanded.to_csv(f"{self.save_folder}/expanded.csv")

            elif (self.walk == "random" and candidates):
                self.expanded = pd.concat(
                    [self.expanded,
                     pd.DataFrame(
                        [[i, path[nb], 1, node, None] for nb, node in enumerate(nodes_to_expand)],
                    columns=["iteration", "path_expanded", "nb_expanded",
                             "node_expanded", "score"])],
                    ignore_index=True
                )
                self.expanded.to_csv(f"{self.save_folder}/expanded.csv")

            else:
                print("According to params, no further nodes to expand," \
                    + f"finishing process at {datetime.now()}\n=====")
                break

        with open(f"{self.save_folder}/metadata.json", "w", encoding="utf-8") as openfile:
            json.dump(metadata, openfile, indent=4)



if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("-j", "--json", required=True,
                    help="Path to json file containing configuration file")
    ap.add_argument("-m", "--mode", default="search_type_node_metrics",
                    help="mode for the search")
    ap.add_argument("-n", "--node_selection", default="all",
                    help="node selection for the search")
    ap.add_argument("-e", "--end_node", default="",
                    help="node to look for in search (only if mode == 'search_specific_node'")
    ap.add_argument("-w", "--walk", default="informed",
                    help="type of walk in the graph: `random` or `informed`")
    args_main = vars(ap.parse_args())

    with open(args_main["json"], "r", encoding="utf-8") as openfile_main:
        config_loaded = json.load(openfile_main)
    if "rdf_type" in config_loaded:
        config_loaded["rdf_type"] = list(config_loaded["rdf_type"].items())

    framework = GraphSearchFramework(config=config_loaded, mode=args_main["mode"],
                                     node_selection=args_main["node_selection"],
                                     walk=args_main["walk"])
    START = datetime.now()
    print(f"Process started at {START}")
    framework(end_node=args_main["end_node"])
    END = datetime.now()
    print(f"Process ended at {END}, took {END-START}")
