"""
#TO DO: add documentation on this script
"""
from tqdm import tqdm
import pandas as pd
from pandas.core.frame import DataFrame
import requests
from rdflib import Graph
from rdflib.term import Literal

TPF_DBPEDIA = \
    "https://api.triplydb.com/datasets/DBpedia-association/snapshot-2021-09/fragments/?limit=10000"

DEFAULT_PRED = \
    ["http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
     "http://dbpedia.org/ontology/date",
     "http://dbpedia.org/ontology/startDate",
     "http://dbpedia.org/ontology/endDate",
     "http://dbpedia.org/property/birthDate",
     "http://dbpedia.org/property/deathDate"]

class TriplInterface:
    """
    #TO DO: add documentation on this script
    """

    def __init__(self, dates: list[str] = [None, None], default_pred: list[str] = DEFAULT_PRED,
                 url: str = TPF_DBPEDIA):
        # Former url: "https://api.triplydb.com/datasets/DBpedia-association/dbpedia/fragments"
        self.url = url
        self.headers = {
            'Accept': 'application/trig'
        }
        self.format = "trig"
        self.pred = default_pred

        self.start_date = dates[0]
        self.end_date = dates[1]

        self.discard_nodes = ["http://dbpedia.org/resource/Category:"]

    def _run_get_request(self, params: dict[str, str]) -> bytes:
        """ Retrieving get curl request by chunks """
        content = bytes('', 'utf-8')
        with requests.get(self.url, headers=self.headers,
                          params=params, timeout=10,
                          stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
        return content

    def run_request(self, params: dict[str, str], filter_pred: list,
                          filter_keep: bool) -> list[(str, str, str)]:
        """ Returning triples corresponding to query """
        # response = requests.get(self.url, headers=self.headers,
        #                         params=params, timeout=10)
        content = self._run_get_request(params)
        graph = Graph().parse(data=content, format=self.format)
        # graph = Graph().parse(data=response.content, format=self.format)
        if filter_keep:
            triples = [(a, b, c) for (a, b, c) in graph if str(b) in filter_pred]
        else:
            triples = [(a, b, c) for (a, b, c) in graph if str(b) not in filter_pred]

        triples = [(a, b, c) for (a, b, c) in triples \
            if not any(str(a).startswith(prefix) for prefix in self.discard_nodes)]
        triples = [(a, b, c) for (a, b, c) in triples \
            if not any(str(c).startswith(prefix) for prefix in self.discard_nodes)]

        return triples

    def get_superclass(self, node: str) -> str:
        """ Superclass of a node
        Most ancient ancestor before owl:Thing """
        info = self.run_request(
            params=dict(subject=str(node)),
            filter_pred=["http://www.w3.org/2000/01/rdf-schema#subClassOf"],
            filter_keep=True)

        if not info:
            return node
        if str(info[0][2]) == "http://www.w3.org/2002/07/owl#Thing":
            return node
        return self.get_superclass(str(info[0][2]))

    def _get_all_results(self, node: str, predicate: list[str]) \
        -> (list[(str, str, str)], list[(str, str, str)], list[(str, str, str)]):

        ingoing = self._get_ingoing(node, predicate)
        outgoing = self._get_outgoing(node, predicate)
        return ingoing, outgoing, self._get_specific_outgoing(ingoing=ingoing,
                                                              outgoing=outgoing)

    def _get_ingoing(self, node: str, predicate: list[str]) -> list[(str, str, str)]:
        """ Return all triples (s, p, o) s.t.
        p not in predicate and o = node """
        return self.run_request(params=dict(object=str(node)),
                                     filter_pred=predicate, filter_keep=False)

    def _filter_outgoing(self, outgoing: list[(str, str, str)]) -> list[(str, str, str)]:
        return [elt for elt in outgoing if not isinstance(elt[2], Literal)]

    def _get_outgoing(self, node: str, predicate: list[str]) -> list[(str, str, str)]:
        """ Return all triples (s, p, o) s.t.
        p not in predicate and s = node """
        return self._filter_outgoing(
            outgoing=self.run_request(params=dict(subject=str(node)),
                                           filter_pred=predicate, filter_keep=False))

    def _get_specific_outgoing(self, ingoing: list[(str, str, str)],
                               outgoing: list[(str, str, str)]) \
                                -> list[(str, str, str)]:
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
    def _get_df(list_triples: list[(str, str, str)], type_df: str) -> DataFrame:
        return pd.DataFrame({"subject": [str(row[0]) for row in list_triples],
                             "predicate": [str(row[1]) for row in list_triples],
                             "object": [str(row[2]) for row in list_triples],
                             "type_df": [type_df] * len(list_triples)}).drop_duplicates()

    def __call__(self, node: str, predicate: list[str]) -> DataFrame:
        ingoing, outgoing, types = self._get_all_results(node=node, predicate=predicate)
        return self._get_df(ingoing, type_df="ingoing"), \
            self._get_df(outgoing, type_df="outgoing"), \
            self._get_df(types, type_df="spec. outgoing")



if __name__ == '__main__':
    NODE = "http://dbpedia.org/resource/Insurrection_of_10_August_1792"
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

    interface = TriplInterface()
    ingoing_test, outgoing_test, types_test = interface(node=NODE, predicate=PREDICATE)
    print(f"{ingoing_test}\n{outgoing_test}\n{types_test}")
