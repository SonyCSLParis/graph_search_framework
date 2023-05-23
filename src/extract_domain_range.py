# -*- coding: utf-8 -*-
""" Extracting directly information for domain/range on predicates
To be run before running the graph search algorithm """
import os
import json
import argparse
from copy import deepcopy
from collections import defaultdict
import yaml
from tqdm import tqdm

from src.hdt_interface import HDTInterface
from settings import FOLDER_PATH


class ExtractDomainRange:
    """ Pre-extracting info to run the graph search:
    domain/range of predicates + superclasses """
    def __init__(self, dataset_type: str, nested: bool, dataset_path: str):
        """
        - `dataset_type`: str, must be `dbpedia`, `wikidata` or `yago`
        - `nested`: boolean, if the hdt dataset to extract info from is nested
        - `dataset_path`: folder path to dataset
        """
        self.dataset_type = dataset_type
        self.nested = nested
        self.dataset_path = dataset_path
        self._check_args()

        # Parameters for the HDT interface
        self.filter_kb = 1
        with open(
            os.path.join(FOLDER_PATH, "dataset-config", f"{dataset_type}.yaml"),
                        encoding='utf-8') as file:
            self.dataset_config = yaml.load(file, Loader=yaml.FullLoader)
        self.pred = []

        # Loading HDT Interface
        self.interface = HDTInterface(filter_kb=self.filter_kb, folder_hdt=self.dataset_path,
                                      dataset_config=self.dataset_config,
                                      nested_dataset=self.nested, default_pred=self.pred)

    def _check_args(self):
        """ Check arguments when instantiating """
        if self.dataset_type not in ["dbpedia", "wikidata", "yago", "faro_sem_news"]:
            raise ValueError("`dataset_type` should be either `dbpedia`, `wikidata` or `yago`")

        if self.nested not in [0, 1]:
            raise ValueError("`nested` should be boolean")

    def get_triples(self, params: dict) -> list[(str, str, str)]:
        """ Querying KB """
        return self.interface.run_request(params=params, filter_pred=[],
                                          filter_keep=False)

    @staticmethod
    def pre_process_yago(value: str) -> list[str]:
        """
        Domain and range values are templated in YAGO HDT
        Not one triple per domain/range value, but all encoded in one triple

        For domain
        _:schema-<id>-rdfs-domain-owl-unionOf-schema-<domain_1>-...-schema-<domain_n>
        ---
        For range
        _:schema-<id>-rdfs-range-owl-unionOf-<type>-<range_1>-...-<type>-<range_n>
        type in ["xsd", "schema"]
        """

        if not value.startswith("_:"):
            return [value]

        short_to_prefix = {
            "schema": "http://schema.org/",
            "xsd": "http://www.w3.org/2001/XMLSchema#"
        }

        list_constraints = value.split("owl-unionOf-")[1].split("-")
        res = []
        for i in range(len(list_constraints)//2):
            res.append(f"{short_to_prefix[list_constraints[2*i]]}{list_constraints[2*i+1]}")

        return res

    def get_pred(self, type_to_extract: str) -> dict:
        """ Getting domain and range information depending on `type_to_extract`"""
        triples = self.get_triples(
                params=dict(predicate=self.dataset_config[type_to_extract]))
        pred_res = {x[0]: [x[2]] for x in triples}

        if self.dataset_type == "yago":
            pred_res = {
                key: self.pre_process_yago(value=val[0]) for key, val in pred_res.items()}
        return pred_res

    def get_type_wikidata(self, type_to_extract: str) -> dict:
        """ Class types of predicates in Wikidata"""
        triples = self.get_triples(
            params=dict(
                predicate=self.dataset_config["property_constraint_direct"],
                object=self.dataset_config[type_to_extract]))
        predicates = set([x[0] for x in triples])

        statements = []
        pred_to_statement = {}
        for pred in predicates:
            triples = self.get_triples(
                params=dict(
                    predicate=self.dataset_config["property_constraint_prop"],
                    subject=pred))
            statements += [x[2] for x in triples]
            pred_to_statement[pred] = [x[2] for x in triples]

        filtered_sub_statements = []
        statement_to_class_type = {}
        for statement in set(statements):
            triples = self.get_triples(
                params=dict(
                    predicate=self.dataset_config["class_qualifier"],
                    subject=statement))
            filtered_sub_statements += triples
            statement_to_class_type[statement] = [x[2] for x in triples]

        # return pred to class type
        return {pred: list(set([x for statement in statements \
                for x in statement_to_class_type[statement]]))\
            for pred, statements in pred_to_statement.items()}

    def get_superclass_wikidata(self) -> dict:
        """ Wikidata superclasses """
        triples = self.interface.run_request(
            params=dict(predicate=self.dataset_config["sub_class_of"][0]),
            filter_pred=[],
            filter_keep=False
        )
        class_to_sub_class = defaultdict(list)

        for triple in triples:
            class_to_sub_class[triple[0]].append(triple[2])

        output = deepcopy(class_to_sub_class)
        for k, sup_cl in class_to_sub_class.items():
            for node in [x for x in sup_cl if x in class_to_sub_class]:
                output[k] += deepcopy(class_to_sub_class[node])
        return {k: list(set(v)) for k, v in output.items()}

    def __call__(self):
        """ Pre extracting info on constraints for predicates """
        if self.dataset_type in ["dbpedia", "yago", "faro_sem_news"]:
            domain_pred = self.get_pred(type_to_extract="domain")
            range_pred = self.get_pred(type_to_extract="range")

            superclasses = {}
            nodes = list(
                set([x for _, val in domain_pred.items() for x in val] + \
                    [x for _, val in range_pred.items() for x in val]))
            for i in tqdm(range(len(nodes))):
                elt = nodes[i]
                superclasses[elt] = [self.interface.get_superclass(node=elt)]

            return domain_pred, range_pred, superclasses

        if self.dataset_type == "wikidata":
            domain_pred = self.get_type_wikidata(type_to_extract="domain")

            range_pred = self.get_type_wikidata(type_to_extract="range")

            superclasses = self.get_superclass_wikidata()
            return domain_pred, range_pred, superclasses

        return None, None, None


if __name__ == '__main__':
    # Command line examples (from repo directory)
    # python src/extract_domain_range.py -dt dbpedia -n 1 -dp ./dbpedia-snapshot-2021-09/
    # python src/extract_domain_range.py -dt yago -dp ./yago-2020-02-24/ -n 1
    # python src/extract_domain_range.py -dt wikidata -dp ./wikidata-2021-03-05/ -n 0

    ap = argparse.ArgumentParser()
    ap.add_argument('-dt', "--dataset_type", required=True,
                    help="type of dataset to extract domain/range/superclasses info from" + \
                        "must be `dbpedia`, `wikidata` or `yago`")
    ap.add_argument('-n', "--nested", required=True,
                    help="boolean, if the hdt dataset to extract info from is nested" + \
                        "(hdt content chunked down in smaller files) or not")
    ap.add_argument("-dp", "--dataset_path", required=True,
                    help="folder path to dataset")
    args_main = vars(ap.parse_args())

    extractor = ExtractDomainRange(dataset_type=args_main["dataset_type"],
                                   nested=int(args_main["nested"]),
                                   dataset_path=args_main["dataset_path"])
    DOMAIN_PRED, RANGE_PRED, SUPERCLASSES = extractor()

    SAVE_FOLDER = os.path.join(FOLDER_PATH, "domain-range-pred")
    if not os.path.exists(SAVE_FOLDER):
        os.makedirs(SAVE_FOLDER)

    DATASET_TYPE = args_main["dataset_type"]
    for data, save_name in [
        (DOMAIN_PRED, f"{DATASET_TYPE}-domain.json"),
        (RANGE_PRED, f"{DATASET_TYPE}-range.json"),
        (SUPERCLASSES, f"{DATASET_TYPE}-superclasses.json")
    ]:
        with open(os.path.join(SAVE_FOLDER, save_name), 'w', encoding='utf-8') as openfile:
            json.dump(data, openfile, indent=4)
