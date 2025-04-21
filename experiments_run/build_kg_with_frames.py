# -*- coding: utf-8 -*-
"""
Building KGs from folder, using frame semantics
"""
import os
import click
from tqdm import tqdm
from loguru import logger
from urllib.parse import quote
from rdflib import Graph, URIRef, Literal
from kglab.helpers.data_load import read_csv
from src.hdt_interface import HDTInterface
from src.build_ng.frame_semantics import FrameSemanticsNGBuilder
from kglab.helpers.kg_build import init_graph
from kglab.helpers.variables import NS_NIF, PREFIX_NIF, NS_EX, PREFIX_EX, NS_RDF, PREFIX_RDF, \
        PREFIX_FRAMESTER_WSJ, NS_FRAMESTER_WSJ, \
            NS_FRAMESTER_FRAMENET_ABOX_GFE, PREFIX_FRAMESTER_FRAMENET_ABOX_GFE, \
                NS_FRAMESTER_ABOX_FRAME, PREFIX_FRAMESTER_ABOX_FRAME, \
                        NS_EARMARK, PREFIX_EARMARK, NS_XSD, PREFIX_XSD, \
                            NS_SKOS, PREFIX_SKOS

INTERFACE = HDTInterface()
FS_KG_BUILDER = FrameSemanticsNGBuilder()

PREFIX_TO_NS = {
    PREFIX_NIF: NS_NIF, PREFIX_RDF: NS_RDF, PREFIX_EX: NS_EX,
    PREFIX_FRAMESTER_WSJ: NS_FRAMESTER_WSJ,
    PREFIX_FRAMESTER_ABOX_FRAME: NS_FRAMESTER_ABOX_FRAME,
    PREFIX_EARMARK: NS_EARMARK, PREFIX_XSD: NS_XSD,
    PREFIX_SKOS: NS_SKOS}

def get_abstract(event: str, lang: str):
    """ Retrieve abstract for input to graph building """
    if not lang.startswith("@"):
        raise ValueError("`lang` param should be in format `@en` and starts with `@`")
    params = {
        "subject": event,
        "predicate": 'http://dbpedia.org/ontology/abstract'
    }
    triples = INTERFACE.get_triples(**params)
    return [x[2].replace(lang, "").replace('"', "") for x in triples if lang in x[2]]

def build_graph(event: str):
    graph = init_graph(prefix_to_ns=PREFIX_TO_NS)
    abstracts = get_abstract(event=event, lang="@en")
    for i, text in enumerate(abstracts):
        id_abstract = f"{event.split('/')[-1]}_{i}"
        graph.add((URIRef(quote(event, safe=":/")), URIRef("http://example.com/abstract"), URIRef(f"http://example.com/{quote(id_abstract)}")))
        curr_graph = FS_KG_BUILDER(text_input=text, id_abstract=id_abstract)
        graph += curr_graph
    return graph

@click.command()
@click.option("--folder", help="(dbpedia) folder with subfolder of experiments")
def main(folder: str):
    """ Main """
    exps = os.listdir(folder)
    nb_exp = len(exps)
    for i, exp in enumerate(exps):
        if exp == "French_Revolution":
            graph = init_graph(prefix_to_ns=PREFIX_TO_NS)
            perc = round(100*(i+1)/nb_exp, 1)
            logger.info(f"({perc}%)\t[Event] {exp}")
            curr_folder = os.path.join(folder, exp)

            output_file = os.path.join(curr_folder, "frame_ng.ttl")

            if not os.path.exists(output_file):
                logger.info(f"[Event][{exp}] Building KG using frame semantics")
                try:
                    events = list(read_csv(os.path.join(curr_folder, "gs_events.csv")).linkDBpediaEn.values) + [f"http://dbpedia.org/resource/{exp}"]
                    # events = events[:2]

                    for event in tqdm(events):
                        curr_graph = build_graph(event=event)
                        graph += curr_graph
                    graph.serialize(output_file, format="ttl")
                    logger.success(f"[Done][{exp}] Built KG using frame semantics")
                except Exception as e:
                    logger.info(f"Error: {e}")
                    logger.warning(f"Could not build graph for {event}")
            else:
                logger.success(f"[Done][{exp}] KG using frame semantics already built")


if __name__ == '__main__':
    main()