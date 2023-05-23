# -*- coding: utf-8 -*-
"""
Interface to query the data 
Generic interface that cannot be used alone
Classes that will inherit that class (eg HDT, SPARQL)
"""
import os
import yaml
from tqdm import tqdm
import pandas as pd
from pandas.core.frame import DataFrame
from settings import FOLDER_PATH

DEFAULT_PRED = \
    ["http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
     "http://dbpedia.org/ontology/date",
     "http://dbpedia.org/ontology/startDate",
     "http://dbpedia.org/ontology/endDate",
     "http://dbpedia.org/property/birthDate",
     "http://dbpedia.org/property/deathDate"]

with open(os.path.join(FOLDER_PATH, "dataset-config", "dbpedia.yaml"),
          encoding='utf-8') as file:
    dbpedia_dataset_config = yaml.load(file, Loader=yaml.FullLoader)

class Interface:
    """ Querying the KG """
    def __init__(self, dataset_config: dict = dbpedia_dataset_config,
                 dates: list[str] = [None, None], default_pred: list[str] = DEFAULT_PRED,
                 filter_kb: bool = 1):
        self.pred = default_pred

        self.start_date = dates[0]
        self.end_date = dates[1]
        self.filter_kb = filter_kb

        self.dataset_config = dataset_config
        self.dataset_type = dataset_config["config_type"]

    def get_triples(self, **params: dict) -> list:
        """ Will be inherited by subclassses """
        return []

    def run_request(self, params: dict[str, str], filter_pred: list,
                    filter_keep: bool) -> list[(str, str, str)]:
        """ Returning triples corresponding to query """
        triples = self.get_triples(**params)
        if filter_keep:
            return [(a, b, c) for (a, b, c) in triples if b in filter_pred]
        return [(a, b, c) for (a, b, c) in triples if b not in filter_pred]

    def get_superclass(self, node: str) -> str:
        """ Superclass of a node
        Most ancient ancestor before owl:Thing """
        info = self.run_request(
            params=dict(subject=str(node)),
            filter_pred=self.dataset_config["sub_class_of"],
            filter_keep=True)

        if not info:
            return node
        if str(info[0][2]) == self.dataset_config["owl_thing"]:
            return node
        return self.get_superclass(str(info[0][2]))

    def _get_all_results(self, node: str, predicate: list[str]) \
        -> (DataFrame, DataFrame, DataFrame):
        """ ingoing, outgoing, specific outgoing """
        ingoing = self._get_ingoing(node, predicate)
        outgoing = self._get_outgoing(node, predicate)
        return ingoing, outgoing, self._filter_specific(
            self._get_specific_outgoing(ingoing=ingoing, outgoing=outgoing))

    def _filter_namespace(self, triples: list[(str, str, str)]):
        """ Filters nodes that start with a regexed value """

        def filter_f(x_val):
            if "start_uri" in self.dataset_config:
                return x_val.startswith(self.dataset_config["start_uri"]) or \
                    not any(x_val.startswith(elt) for elt in ["http", '"'])
            return True

        triples = [elt for elt in triples if filter_f(elt[2])]
        triples = [elt for elt in triples if filter_f(elt[0])]

        return triples

    @staticmethod
    def pre_process_date(x_date: str) -> str:
        """ Pre processing date (to be format comparable later) """
        xml_dates = [
            "<http://www.w3.org/2001/XMLSchema#date>",
            "<http://www.w3.org/2001/XMLSchema#dateTime>"
        ]
        if any(xml_date in x_date for xml_date in xml_dates):
            return x_date[1:11]
        elif "<http://www.w3.org/2001/XMLSchema#integer>" in x_date:
            return x_date[1:5]
        else:
            return x_date

    def _filter_node(self, triples: list[(str, str, str)], filter_out: list[str]) \
        -> list[(str, str, str)]:
        """ Filtering nodes based on starting patterns/regexs"""
        triples = self._filter_namespace(triples)
        triples = [elt for elt in triples if \
            not any(elt[0].startswith(pattern) for pattern in filter_out)]
        triples = [elt for elt in triples if \
            not any(elt[2].startswith(pattern) for pattern in filter_out)]
        triples = [elt for elt in triples if \
            not any(elt[2].endswith(pattern) for pattern in [".svg"])]
        return triples

    def _filter_specific(self, triples: list[(str, str, str)]) \
        -> list[(str, str, str)]:
        """ Filtering objects of triples based on value """
        invalid = ['"Unknown"@']
        triples = [(sub, pred, obj) for (sub, pred, obj) in triples if obj not in invalid]
        return [(sub, pred, self.pre_process_date(obj)) for (sub, pred, obj) in triples]

    def _get_outgoing(self, node: str, predicate: list[str]) \
        -> list[(str, str, str)]:
        """ Return all triples (s, p, o) s.t.
        p not in predicate and s = node """
        return self._helper_ingoing_outgoing(params=dict(subject=str(node)),
                                             predicate=predicate, filter_keep=False)

    def _get_ingoing(self, node: str, predicate: list[str]) \
        -> list[(str, str, str)]:
        """ Return all triples (s, p, o) s.t.
        p not in predicate and o = node """
        return self._helper_ingoing_outgoing(params=dict(object=str(node)),
                                             predicate=predicate, filter_keep=False)

    def _helper_ingoing_outgoing(self, params: dict, predicate: list[str],
                                 filter_keep: bool) \
                                    -> list[(str, str, str)]:
        """ Filtering 1-hop neighbours depending on dataset """
        triples = self.run_request(params=params,
                                   filter_pred=predicate, filter_keep=filter_keep)
        if self.filter_kb  and self.dataset_config['config_type'] == "dbpedia":
            return self._filter_node(triples=triples, filter_out=[self.dataset_config["category"]])
        if self.dataset_config['config_type'] == "wikidata":
            return self._filter_node(triples=triples,
                                     filter_out=self.dataset_config["start_stop_uri"])
        return triples

    def _get_specific_outgoing(self, ingoing: list[tuple], outgoing: list[tuple]) \
        -> list[(str, str, str)]:
        """ Returning specific outgoing nodes, e.g. rdf:type and dates """
        temp_res = []

        for i in tqdm(range(len(ingoing))):
            subject = ingoing[i][0]
            temp_res += self.run_request(params=dict(subject=str(subject)),
                                             filter_pred=self.pred,
                                             filter_keep=True)

        for i in tqdm(range(len(outgoing))):
            object_t = outgoing[i][2]
            temp_res += self.run_request(params=dict(subject=str(object_t)),
                                                         filter_pred=self.pred,
                                                         filter_keep=True)

        return temp_res

    @staticmethod
    def _get_df(list_triples: list[tuple], type_df: str) -> DataFrame:
        """ Transform into df """
        return pd.DataFrame({"subject": [str(row[0]) for row in list_triples],
                             "predicate": [str(row[1]) for row in list_triples],
                             "object": [str(row[2]) for row in list_triples],
                             "type_df": [type_df] * len(list_triples)}).drop_duplicates()

    def __call__(self, node: str, predicate: list[str]) \
        -> (DataFrame, DataFrame, DataFrame):
        ingoing, outgoing, types = self._get_all_results(node=node, predicate=predicate)

        return self._get_df(ingoing, type_df="ingoing"), \
            self._get_df(outgoing, type_df="outgoing"), \
            self._get_df(types, type_df="spec. outgoing")
