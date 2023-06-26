# -*- coding: utf-8 -*-
"""
Expanding one node by retrieving its ingoing and outgoing edges
Filtering subgraph and pending nodes to be explored
"""
import os
import json
from collections import defaultdict
from pandas.core.frame import DataFrame

from settings import FOLDER_PATH
from src.filtering import Filtering
from src.hdt_interface import HDTInterface
from src.reasoner import Reasoner

class NodeExpansion:
    """
    NodeExpansion class:
    1 - Get ingoing, outgoing and specific outgoing nodes of a set of nodes
    2 - Apply filtering depending on the input narrative filters
    3 - Return non-discarded ingoing and outgoing nodes
    """

    def __init__(self, rdf_type: list[tuple], args_filtering: dict,
                 interface, reasoner=None):
        """
        - `rdf_type`: list of tuples (<type_uri>, <URI>),
        e.g. ["event", "http://dbpedia.org/ontology/Event"]
        - `args_filtering`: parameters to apply the filters,
        should have the following structure: (corr. = corresponding)
        {
            "when": boolean,
            "where": boolean,
            "who": boolean,
            "point_in_time": list[<uri-corr.-to-point-in-time>],
            "start_dates": list[<uri-corr.-to-start-date>],
            "end_dates": list[<uri-corr.-to-end-date>],
            "places": list[<uri-corr.-to-place>],
            "people": list[<uri-corr.-to-ppl>],
            "dataset_type": str,
        }
        - `interface`: permits to interact with the KG.
        Three implemented: HDTInterface, TriplInterface, SPARQLInterface
        BUT in practice, experiments with HDTInterface only
        --> TriplInterface and SPARQLInterface obsolete
        """
        self.interface = interface
        self.reasoner = reasoner
        self.rdf_type = rdf_type
        self._check_args()

        self.mapping = {uri: name for (name, uri) in rdf_type}

        self.filtering = Filtering(args=args_filtering)
        self.superclasses = defaultdict(list)

        self.dataset_type = interface.dataset_config["config_type"]
        info_folder = os.path.join(FOLDER_PATH, "domain-range-pred")
        with open(os.path.join(info_folder, f"{self.dataset_type}-superclasses.json"),
                  "r", encoding="utf-8") as openfile:
            self.superclasses = json.load(openfile)

    def _check_args(self):
        """ Checking params when instantiating the class """
        if self.rdf_type:
            if not isinstance(self.rdf_type, list):
                raise ValueError('`rdf_type` param should be a non-empty list of tuples')
            if any(not (isinstance(elt, tuple) and len(elt) == 2) for elt in self.rdf_type):
                raise ValueError('`rdf_type` param should be a list of tuples')
            if any(not ((isinstance(a, str)) and isinstance(b, str)) \
                for (a, b) in self.rdf_type):
                raise ValueError("Type of two-element tuples should be" \
                        + "(str, str)")

    def get_output_triples(self, node: str, predicate: list[str]) \
        -> (DataFrame, DataFrame, DataFrame):
        """ Direct call to _get_output_triples """
        return self._get_output_triples(node, predicate)

    def _get_output_triples(self, node: str, predicate: list[str]) \
        -> (DataFrame, DataFrame, DataFrame):
        """ Getting ingoing, outgoing and specific outgoing nodes """
        return self.interface(node=node, predicate=predicate)

    def filter_sub_graph(self, type_date_df, triple_ingoing, triple_outgoing, dates) \
        -> (DataFrame, DataFrame, DataFrame, DataFrame, list[str]):
        """ Direct call to _filter_sub_graph """
        return self._filter_sub_graph(type_date_df, triple_ingoing, triple_outgoing, dates)

    def _filter_sub_graph(self, type_date_df: DataFrame, triple_ingoing: DataFrame,
                          triple_outgoing: DataFrame, dates: list[str, str]) \
                            -> (DataFrame, DataFrame, DataFrame, DataFrame, list[str]):
        """ Filtering subgraph: nodes to be removed, nodes to be kept, other """

        # Edge case: type_date_df is empty
        # --> we assume that the ingoing/outgoing nodes are not relevant for the search
        if type_date_df.shape[0] == 0:
            to_keep = []
            to_discard = list(triple_ingoing.subject.unique()) + \
                list(triple_outgoing.object.unique())

        else:
            to_discard = self.filtering(ingoing=triple_ingoing, outgoing=triple_outgoing,
                                        type_date=type_date_df, dates=dates)

            if self.mapping:
                filtered = [k for k, sup_class in self.superclasses.items() \
                    if any(elt in sup_class for elt in self.mapping.keys())] + \
                        list(self.mapping.keys())
            else:
                filtered = []
            # Filter on types of nodes that should be retrieved
            to_keep = list(type_date_df[(~type_date_df.subject.isin(to_discard)) & \
                (type_date_df.object.isin(filtered))].subject.unique())

        if self.reasoner:
            to_discard = self.reasoner.update_nodes(self.interface, to_discard)
            to_keep = self.reasoner.update_nodes(self.interface, to_keep)

        return triple_ingoing[triple_ingoing.subject.isin(to_keep)], \
            triple_ingoing[~triple_ingoing.subject.isin(to_discard)], \
            triple_outgoing[triple_outgoing.object.isin(to_keep)], \
            triple_outgoing[~triple_outgoing.object.isin(to_discard)], \
            to_discard
    
    def _remove_expanded_nodes(self, ingoing, outgoing, types_date, nodes):
        """ Removing nodes that were already visited """
        ingoing = ingoing[~ingoing.subject.isin(nodes)]
        outgoing = outgoing[~outgoing.object.isin(nodes)]
        types_date = types_date[~types_date.object.isin(nodes)]
        return ingoing, outgoing, types_date

    def __call__(self, args: dict, dates: list[str, str]) \
        -> (DataFrame, DataFrame, DataFrame, DataFrame, list[str]):

        # Querying knowledge base
        ingoing, outgoing, types_date = self._get_output_triples(
            node=args["node"], predicate=args["predicate"])

        # Removing nodes that were already visited
        ingoing, outgoing, types_date = self._remove_expanded_nodes(
            ingoing=ingoing, outgoing=outgoing, types_date=types_date,
            nodes=args["visited_nodes"] if "visited_nodes" in args.keys() else []
        )

        # Filter subgraph to keep
        filtered = self._filter_sub_graph(type_date_df=types_date, triple_ingoing=ingoing,
                                      triple_outgoing=outgoing, dates=dates)

        return filtered, self.reasoner


if __name__ == '__main__':
    import yaml
    interface_main = HDTInterface()
    reasoner_main = Reasoner()

    NODE = "http://dbpedia.org/resource/Antoine_Morlot"
    PREDICATE = ["http://dbpedia.org/ontology/wikiPageWikiLink",
                    "http://dbpedia.org/ontology/wikiPageRedirects",
                    "http://dbpedia.org/ontology/wikiPageDisambiguates",
                    "http://www.w3.org/2000/01/rdf-schema#seeAlso",
                    "http://xmlns.com/foaf/0.1/depiction",
                    "http://xmlns.com/foaf/0.1/isPrimaryTopicOf",
                    "http://dbpedia.org/ontology/thumbnail",
                    "http://dbpedia.org/ontology/wikiPageExternalLink",
                    "http://dbpedia.org/ontology/wikiPageID",
                    "http://dbpedia.org/ontology/wikiPageLength",
                    "http://dbpedia.org/ontology/wikiPageRevisionID",
                    "http://dbpedia.org/property/wikiPageUsesTemplate",
                    "http://www.w3.org/2002/07/owl#sameAs",
                    "http://www.w3.org/ns/prov#wasDerivedFrom"]
    RDF_TYPE = [("event", "http://dbpedia.org/ontology/Event")]

    with open(os.path.join(FOLDER_PATH, "dataset-config", "dbpedia.yaml"), 'rb') as openfile_main:
        DATASET_CONFIG = yaml.load(openfile_main, Loader=yaml.FullLoader)

    ARGS_FILTERING = {
        "when": 1,
        "where": 1,
        "who": 0,
        "point_in_time": DATASET_CONFIG["point_in_time"],
        "start_dates": DATASET_CONFIG["start_dates"],
        "end_dates": DATASET_CONFIG["end_dates"],
        "places": DATASET_CONFIG["places"],
        "people": DATASET_CONFIG["person"],
        "dataset_type": DATASET_CONFIG["config_type"],
    }

    node_expander = NodeExpansion(interface=interface_main, reasoner=reasoner_main,
                                  rdf_type=RDF_TYPE, args_filtering=ARGS_FILTERING)
    subgraph_ingoing_test, path_ingoing_test, subgraph_outgoing_test, \
        path_outgoing_test, to_discard_test = \
            node_expander(args={"path": [], "node": NODE, "predicate": PREDICATE},
                          dates=["1789-01-01", "1799-12-31"])
    print(f"{subgraph_ingoing_test}\n{path_ingoing_test}")
    print(f"{subgraph_outgoing_test}\n{path_outgoing_test}")
    print(f"\nTO DISCARD\n{to_discard_test}\n")
