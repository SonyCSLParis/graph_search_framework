# -*- coding: utf-8 -*-
"""
Converting KG schemas

Input = KG + output schema
Output = KG under new schema

Abbreviations:
- NG = narrative graph

Done:
- Rule-based filters on labels rather than predicate IRI (works for wikidata then)
- HDT Interface for querying // move back to graph search code
- Add argparse 

Improvements
- Filters when adding an actor to an event (WHEN filter)
- WordNet taxonomy for nf_to_str?
- Result/cause
"""
import os
import re
import yaml
import time
import click
from typing import Union
from datetime import datetime
from tqdm import tqdm
import pandas as pd
from rdflib import URIRef, Literal, Graph
from settings import FOLDER_PATH
from kglab.helpers.encoding import encode
from src.hdt_interface import HDTInterface
from kglab.helpers.kg_build import init_graph
from kglab.helpers.kg_query import get_labels, get_outgoing
from kglab.helpers.data_load import open_json, read_csv
from kglab.helpers.variables import NS_SEM, PREFIX_SEM, NS_XSD, PREFIX_XSD, STR_XSD, NS_DBR, PREFIX_DBR, NS_RDF, PREFIX_RDF, NS_RDFS
from src.build_ng.dbpedia_spotlight import init_spacy_pipeline, get_db_entities

def get_args_hdt_interface(dataset: str) -> (dict, str, bool):
    """ 3 variable params for HDTInterface init """
    with open(os.path.join(FOLDER_PATH, "dataset-config", f"{dataset}.yaml"),
              encoding='utf-8') as file:
        config = yaml.load(file, Loader=yaml.FullLoader)
    
    folder_hdt = os.path.join(FOLDER_PATH, "dbpedia-snapshot-2021-09") if dataset == "dbpedia" \
        else os.path.join(FOLDER_PATH, "wikidata-2021-03-05")
    nested = True if dataset == "dbpedia" else True
    return config, folder_hdt, nested

class KGConverter:
    """ Converting input triples to NG with SEM ontology"""
    def __init__(self, dataset: str):
        """ Adding main reusable variables 
        - `domain`, `range`, `subclass`: path to json files containing info about 
        domain, range of predicates and subclass of entities """
        self.nf_to_pred = {
            "who": NS_SEM["hasActor"],
            "what": NS_SEM["eventType"],
            "when": NS_SEM["hasTimeStamp"],
            # "when_ts": NS_SEM["hasBeginTimeStamp"],
            "when_bts": NS_SEM["hasBeginTimeStamp"],
            "when_ets": NS_SEM["hasEndTimeStamp"],
            "where": NS_SEM["hasPlace"],
            "part_of": NS_SEM["subEventOf"],
            "part_of_inverse": NS_SEM["hasSubEvent"],
        }
        self.nf_to_str = {
            "who": ["person", "combatant", "commander", "participant"],
            "what": ["type"],
            # "when": ["date", "point in time", "start time", "end time", "time"],
            "when_bts": ["start time", "date", "point in time"],
            "when_ets": ["end time"],
            # "when_ts": ["date", "point in time"],
            "where": ["place", "location", "country"],
            "part_of": ["partof", "part of"],
            "part_of_inverse": ["has part", "significant event"],
        }
        self.temporal_filters = self.nf_to_str["when_bts"] + self.nf_to_str["when_ets"]
        self.str_to_nf = {
            string: nf for nf, l_str in self.nf_to_str.items() for string in l_str
        }
        self.cached = {}

        self.domain = open_json(path=os.path.join(FOLDER_PATH, "domain-range-pred/", f"{dataset}-domain.json"))
        self.range = open_json(path=os.path.join(FOLDER_PATH, "domain-range-pred/", f"{dataset}-range.json"))
        self.superclass = open_json(path=os.path.join(FOLDER_PATH, "domain-range-pred/", f"{dataset}-superclasses.json"))

        config, folder_hdt, nested = get_args_hdt_interface(dataset=dataset)
        self.interface = HDTInterface(dataset_config=config, folder_hdt=folder_hdt, nested_dataset=nested)

        self.nlp = init_spacy_pipeline()
        self.prefix_to_ns = {
            PREFIX_SEM: NS_SEM, PREFIX_XSD: NS_XSD,
            PREFIX_DBR: NS_DBR, PREFIX_RDF: NS_RDF}

    def get_sem_pred_by_type(self, type_: str, info: dict, pred: str) -> list:
        """ Differentiating between domain/range """
        res = set()
        curr_info = info.get(pred, [])
        for cinfo in curr_info:
            all_classes = [cinfo] + self.superclass.get(cinfo, [ ])
            for cclass in all_classes:
                cclass_name = cclass.split("/")[-1].lower()
                if cclass_name in self.str_to_nf:
                    res.add((self.str_to_nf[cclass_name], type_))
        return list(res)

    def get_sem_pred(self, pred: str) -> list[str]:
        """Using domain and pred to find corresponding SEM predicate"""
        res = []
        for type_, info in [("range", self.range), ("domain", self.domain)]:
            res.extend(self.get_sem_pred_by_type(type_=type_, info=info, pred=pred))
        return res

    def get_outgoing_links(self, input_df: pd.DataFrame) -> (pd.DataFrame, list[str]):
        """ Takes output of graph search, extract events and retrieve outgoing links """
        events = list(input_df[input_df.type_df == "ingoing"].subject.unique()) + \
            list(input_df[input_df.type_df == "outgoing"].object.unique())
        # print(events)
        
        columns = ["subject", "predicate", "object"]
        output = pd.DataFrame(columns=columns)
        for event in tqdm(events):
            params = {"subject": event}
            outgoing = self.interface.get_triples(**params)
            output = pd.concat([
                output,
                pd.DataFrame(outgoing, columns=columns)])

        return output.fillna(""), events

    def get_labels_iterative(self, input_df: pd.DataFrame) -> pd.DataFrame:
        predicates = [x for x in input_df.predicate.unique() if x.startswith("http")]
        columns = ["predicate", "label"]
        output = pd.DataFrame(columns=columns)
        for predicate in tqdm(predicates):
            params = {"subject": predicate, "predicate": str(NS_RDFS["label"])}
            labels = self.interface.get_triples(**params)
            labels = [(x[0], x[2]) for x in labels]
            output = pd.concat([
                output,
                pd.DataFrame(labels, columns=columns)])
        return output.drop_duplicates()
    
    def helper_temporal(self, row, cands: list[str], ta: list[str], graph: Graph, to_add: str) -> (Graph, list[str]):
        """ Helper """
        for cand in cands:
            graph.add((URIRef(encode(row.subject)), self.nf_to_pred[self.str_to_nf[cand]],
                        Literal(row.object[1:11], datatype=NS_XSD["date"])))
        ta.append(to_add)
        return graph, ta

    
    def add_temporal(self, input_df: pd.DataFrame, graph: Graph, start_d: Union[str, None], end_d: Union[str, None]) -> Graph:
        """ Adding timestamps to narrative graph """
        # First starting with xsd:dateTime
        subset = input_df[input_df.object.str.contains(STR_XSD)]
        events = subset.subject.unique()

        start_d = start_d if start_d else str(datetime.min)[:10]
        end_d = end_d if end_d else str(datetime.max)[:10]

        for event in events:
            start_found, end_found = None, None
            for _, row in subset[subset.subject == event].iterrows():
                # Checking for start, end, point in time predicates
                for (key, to_add) in [("when_bts", "start"), ("when_ets", "end")]:
                    cands = [x for x in self.nf_to_str[key] if x in row.label]
                    if cands and (start_d <= row.object[1:11] <= end_d):
                        if (to_add == "start") and ((not start_found) or (start_found and row.object[1:11] < start_found)):
                            start_found = row.object[1:11]
                        if (to_add == "end") and ((not end_found) or (end_found and row.object[1:11] > end_found)):
                            end_found = row.object[1:11]
            
            # If only start_found -> adding same date for end_found
            if start_found and (not end_found):
                end_found = start_found
            if start_found:
                graph.add((URIRef(encode(event)), self.nf_to_pred["when_bts"], Literal(start_found, datatype=NS_XSD["date"])))
                graph.add((URIRef(encode(event)), self.nf_to_pred["when_ets"], Literal(end_found, datatype=NS_XSD["date"])))

        return graph

        # BELOW: former, filter on dates with regex
        # if cand in self.nf_to_str["when"]: # Regex to extract dates
        #             matches = re.findall("\\d{4}-\\d{2}-\\d{2}", row.object)
        #             if len(matches) == 1:
        #                 # In EventKG, no event ?e with (?e, sem:hasTimeStamp, ?ts)
        #                 # If only one --> same begin and end timestamps
        #                 if start_d <= matches[0] <= end_d:
        #                     graph.add((URIRef(encode(row.subject)), self.nf_to_pred["when_bts"],
        #                             Literal(matches[0], datatype=NS_XSD["date"])))
        #                     graph.add((URIRef(encode(row.subject)), self.nf_to_pred["when_ets"],
        #                             Literal(matches[0], datatype=NS_XSD["date"])))
        #             if len(matches) == 2:
        #                 matches = sorted(matches)
        #                 if start_d <= matches[0] <= end_d:
        #                     graph.add((URIRef(encode(row.subject)), self.nf_to_pred["when_bts"],
        #                             Literal(matches[0], datatype=NS_XSD["date"])))
        #                 if start_d <= matches[1] <= end_d:
        #                     graph.add((URIRef(encode(row.subject)), self.nf_to_pred["when_ets"],
        #                             Literal(matches[1], datatype=NS_XSD["date"])))


    def __call__(self, input_df: pd.DataFrame,
                 start_d: Union[str, None] = None, end_d: Union[str, None] = None, add_text_extraction: bool = False) -> Graph:
        """ 
        - input_df: output of graph search, {i}-subgraph.csv 
        columns: subject, predicate, object, type_df, iteration, regex_helper-
        - start_d & end_d: start and end date of the event
        """
        # Init/Formatting main variables
        columns = ["subject", "predicate", "object"]
        graph = init_graph(prefix_to_ns=self.prefix_to_ns)
        input_df = input_df.fillna("")
        input_df.columns = columns + list(input_df.columns[3:])

        # Extracting outgoing links of events
        # print("Extracting outgoing links")
        outgoing_links, events = self.get_outgoing_links(input_df=input_df)
        input_df = pd.concat([input_df[columns], outgoing_links])
        # input_df.to_csv("input.csv")

        # Adding events
        for event in events:
            graph.add((URIRef(encode(event)), NS_RDF["type"], NS_SEM["Event"]))

        # Getting predicate labels
        # print("Retrieving labels")
        pred_labels = self.get_labels_iterative(input_df=input_df)
        pred_to_labels = pred_labels.set_index("predicate")["label"].to_dict()
        input_df = input_df.merge(pred_labels, on="predicate")

        # URI objects
        # print("Build Narrative Graph - URI objects")
        input_df_uri = input_df[input_df.object.str.startswith("http")]
        unique_preds = input_df_uri.predicate.unique()
        for pred in unique_preds:
            self.cached[pred] = self.get_sem_pred(pred=pred)

        for _, row in tqdm(input_df_uri.iterrows(), total=input_df_uri.shape[0]):
            # Pre-identified domain/range
            if self.cached[row.predicate]:
                for (nf, type_pred) in self.cached[row.predicate]:
                    if type_pred == "range":
                        graph.add((URIRef(encode(row.subject)), self.nf_to_pred[nf], URIRef(encode(row.object))))
                    else:  # type_pred == "domain"
                        graph.add((URIRef(encode(row.object)), self.nf_to_pred[nf], URIRef(encode(row.subject))))
            # Predicate contains one key from self.str_to_nf
            label_pred = pred_to_labels.get(row.predicate, row.predicate.split("/")[-1].lower())
            cands = [x for x in self.str_to_nf if x in label_pred]
            for cand in cands:
                graph.add((URIRef(encode(row.subject)), self.nf_to_pred[self.str_to_nf[cand]],
                           URIRef(encode(row.object))))

        # Literal objects: extracting additional information
        # print("Build Narrative Graph - Literal objects")
        input_df_lit = input_df[(~input_df.object.str.startswith("http")) & \
            (~input_df.object.isin([""]))]

        ## Literal objects: time
        input_df_temp = input_df_lit[input_df_lit.apply(lambda x: any(y in x['label'] for y in self.temporal_filters), axis=1)]
        graph = self.add_temporal(
            input_df=input_df_temp,
            graph=graph, start_d=start_d, end_d=end_d)

         
        ## Literal objects: other
        if add_text_extraction:
            # Additional information extraction from text
            input_df_other = pd.concat([input_df_lit, input_df_temp]).drop_duplicates(keep=False)
            for _, row in tqdm(input_df_other.iterrows(), total=input_df_other.shape[0]):
                label_pred = pred_to_labels.get(row.predicate, row.predicate.split("/")[-1].lower())
                cands = [x for x in self.str_to_nf if x in label_pred]
                for cand in cands:
                    pred = self.nf_to_pred[self.str_to_nf[cand]]

                    if row.object not in self.cached:
                        doc = self.nlp(row.object)
                        self.cached[row.object] = get_db_entities(doc)
                    
                    for ent in self.cached[row.object]:
                        graph.add((URIRef(encode(row.subject)), pred, URIRef(encode(ent))))

        return graph


@click.command()
@click.option("--input_file", help="path to .csv (format similar to output of graph search)")
@click.option("--dataset", help="dataset to work with (dbpedia/wikidata)")
@click.option("--start_d", help="start date")
@click.option("--end_d", help="end date")
@click.option("--save", help="save file")
def main(input_file: str, dataset: str, start_d: Union[str, None], end_d: Union[str, None], save: str):
    df = read_csv(path=input_file)
    converter = KGConverter(dataset=dataset)
    graph = converter(input_df=df, start_d=start_d, end_d=end_d)
    graph.serialize(save, format="ttl")


if __name__ == '__main__':
    """ 
    python src/build_ng/generic_kb_to_ng.py --input_file <french-rev-ex> --dataset dbpedia \
        --start_d 1789-05-05 --end_d 1799-12-31 --save <save>
    """
    main()