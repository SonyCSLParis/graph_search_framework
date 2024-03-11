"""
#TO DO: add documentation on this script
"""
import os
import urllib.request
from urllib.parse import quote_plus
import yaml
from pandas.core.frame import DataFrame
from SPARQLWrapper import SPARQLWrapper, RDFXML
from settings import AGENT, FOLDER_PATH
from src.interface import Interface

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

class SPARQLQuery:
    """
    Creating SPARQL queries based on templates + params
    """
    def __init__(self):
        self.query_template = self._set_query_template()

    def _set_query_template(self) -> str:
        query = """
        CONSTRUCT {
            ?s ?p ?o .
        }
        WHERE {
        <VALUES-unique-subject>
        <VALUES-unique-predicate>
        <VALUES-unique-object>
        ?s ?p ?o .
            }
        """
        return query

    def __call__(self, params: dict[str, str]) -> str:
        query = self.query_template
        for name, abbr in [("subject", "s"), ("predicate", "p"), ("object", "o")]:
            if name in params and params[name]:
                query = query.replace(
                    f"<VALUES-unique-{name}>",
                    "VALUES ?" + abbr + " { <" + quote_plus(params[name], safe='/:#') + "> } "
                )
            else:
                query = query.replace(f"<VALUES-unique-{name}>", "")
        return query


class SPARQLInterface(Interface):
    """
    #TO DO: add documentation on this script
    """
    def __init__(self, dataset_config: dict = dbpedia_dataset_config,
                 dates: list[str] = [None, None], default_pred: list[str] = DEFAULT_PRED,
                 filter_kb: bool = 1, sparql_endpoint: str = "http://dbpedia.org/sparql",
                 agent: str = AGENT):
        Interface.__init__(self, dataset_config=dataset_config, dates=dates,
                           default_pred=default_pred, filter_kb=filter_kb)
        self.sparql = SPARQLWrapper(sparql_endpoint, agent=agent)
        self.sparql_query = SPARQLQuery()

    def get_triples(self, **params: dict[str, str]) -> list[(str, str, str)]:
        query = self.sparql_query(params=params)
        return self.call_endpoint(query=query)

    def call_endpoint(self, query: str) -> DataFrame:
        """ Querying KG through SPARQL endpoint """
        proxy_support = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_support)
        urllib.request.install_opener(opener)
        self.sparql.setReturnFormat(RDFXML)
        self.sparql.setQuery(query)
        # self.sparql.setMethod(POST)
        try:
            results = self.sparql.query().convert()
            return [(str(triple[0]), str(triple[1]), str(triple[2])) \
                for triple in list(set(results))]
        except:
            return []


if __name__ == '__main__':
    NODE = "http://dbpedia.org/resource/Storming_of_the_Bastille"
    NODE =  "http://dbpedia.org/resource/Causes_of_the_French_Revolution"
    # predicate = ["<http://dbpedia.org/ontology/wikiPageRedirects>"]
    PREDICATE = list()
    # sparql_endpoint = \
    # "https://api.triplydb.com/datasets/DBpedia-association/dbpedia/services/dbpedia/sparql"

    interface = SPARQLInterface()
    ingoing, outgoing, types = interface(node=NODE, predicate=PREDICATE)

    from datetime import datetime
    LOG = str(datetime.now())[:19].replace(" ", "-")
    ingoing.to_csv(f"{LOG}_ingoing_sparql.csv")
    outgoing.to_csv(f"{LOG}_outgoing_sparql.csv")
    types.to_csv(f"{LOG}_types_sparql.csv")
