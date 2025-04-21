# -*- coding: utf-8 -*-
"""
Installing DBpedia Spotlight:
https://github.com/MartinoMensio/spacy-dbpedia-spotlight

Building NG from frame semantics

Frame annotations in Framester
http://etna.istc.cnr.it/framesterpage/wsj/wsjpropnetannotations/CE_55094

Improvements
- Add entities + links to DBpedia
"""
import re
import spacy
from urllib.parse import quote
from spacy.tokens import Span, Doc
from rdflib import Graph, Literal, URIRef
from frame_semantic_transformer import FrameSemanticTransformer, DetectFramesResult

from kglab.kg_building_from_text.dbpedia_spotlight import init_spacy_pipeline
from kglab.helpers.kg_build import init_graph
from kglab.helpers.variables import NS_NIF, PREFIX_NIF, NS_EX, PREFIX_EX, NS_RDF, PREFIX_RDF, \
        PREFIX_FRAMESTER_WSJ, NS_FRAMESTER_WSJ, \
            NS_FRAMESTER_FRAMENET_ABOX_GFE, PREFIX_FRAMESTER_FRAMENET_ABOX_GFE, \
                NS_FRAMESTER_ABOX_FRAME, PREFIX_FRAMESTER_ABOX_FRAME, \
                        NS_EARMARK, PREFIX_EARMARK, NS_XSD, PREFIX_XSD, \
                            NS_SKOS, PREFIX_SKOS

# context = [
#     "The Korean War was started when North Korea invaded South Korea.",
#     "The United Nations, with United States as the principal force, came to aid of South Korea.",
#     "China, along with assistance from Soviet Union, came to aid of North Korea.",
#     "The war arose from the division of Korea at the end of World War II and from the global tensions of the Cold War that developed immediately afterwards."
# ]

class FrameSemanticsNGBuilder:
    """ Building Narrative Graphs using Frame Semantics """
    def __init__(self):
        self.frame_transformer = FrameSemanticTransformer()

        nlp = spacy.load("en_core_web_sm")
        nlp.add_pipe(
            "dbpedia_spotlight",
            config={'confidence': 0.7, 'dbpedia_rest_endpoint': 'http://localhost:2222/rest'})
        self.nlp = nlp
        self.prefix_to_ns = {
            PREFIX_NIF: NS_NIF, PREFIX_RDF: NS_RDF, PREFIX_EX: NS_EX,
            PREFIX_FRAMESTER_WSJ: NS_FRAMESTER_WSJ,
            PREFIX_FRAMESTER_ABOX_FRAME: NS_FRAMESTER_ABOX_FRAME,
            PREFIX_EARMARK: NS_EARMARK, PREFIX_XSD: NS_XSD,
            PREFIX_SKOS: NS_SKOS
            }

    def add_nif_phrase_sent(self, graph: Graph, doc: Doc,
                            id_abstract: str) -> Graph:
        """ Init graph with full paragraph and sentences """
        graph.add((NS_EX[quote(id_abstract)], NS_RDF["type"], NS_NIF["Phrase"]))
        graph.add((NS_EX[quote(id_abstract)], NS_RDF["value"], Literal(doc.text)))

        for i, sent in enumerate(doc.sents):
            graph.add((NS_EX[quote(id_abstract)], NS_NIF["sentence"], NS_EX[f"{quote(id_abstract)}_{i}"]))
            graph.add((NS_EX[f"{quote(id_abstract)}_{i}"], NS_RDF["type"], NS_NIF["Sentence"]))
            graph.add((NS_EX[f"{quote(id_abstract)}_{i}"], NS_RDF["value"], Literal(sent.text)))
        return graph

    def add_trigger_token(self, graph: Graph, tokens: list[str]):
        """ Add nodes for each trigger token (for now unique across all) """
        for token in tokens:
            graph.add((NS_EX[quote(token)], NS_RDF["type"], NS_NIF["Word"]))
        return graph

    def add_frame(self, graph: Graph, id_sent: int, result: DetectFramesResult, doc: Doc, surf_to_ent: dict) -> Graph:
        """ Add frame info 
        - id_sent = f"{id_abstract}_{i}" (cf. above) """
        # Adding token as nif:Word node
        tokens = []
        for trigger in result.trigger_locations:
            tokens.append(result.sentence[trigger:].split(" ")[0])
        # graph = self.add_trigger_token(graph=graph, tokens=tokens)

        # Adding each frame as one annotation
        for i, frame in enumerate(result.frames):
            frame_annot_iri = NS_EX[f"{quote(id_sent)}_{i}"]
            graph.add((frame_annot_iri, NS_RDF["type"], NS_FRAMESTER_WSJ["CorpusEntry"]))
            # graph.add((frame_annot_iri, NS_RDF["type"], NS_DUL["Event"]))
            graph.add((frame_annot_iri, NS_FRAMESTER_WSJ["fromDocument"], NS_EX[quote(id_sent)]))
            graph.add((frame_annot_iri, NS_FRAMESTER_WSJ["onFrame"],
                       NS_FRAMESTER_ABOX_FRAME[frame.name]))
            graph.add((frame_annot_iri, NS_FRAMESTER_WSJ["onLemma"],
                       Literal(result.sentence[frame.trigger_location:].split(" ")[0])))


            for i_fe, fr_el in enumerate(frame.frame_elements):
                fe_iri = NS_EX[f"{quote(id_sent)}_{i}_{i_fe}"]
                graph.add((frame_annot_iri, NS_FRAMESTER_WSJ["withmappedrole"], fe_iri))
                graph.add((fe_iri, NS_RDF["type"], NS_FRAMESTER_WSJ["MappedRole"]))
                graph.add((fe_iri, NS_FRAMESTER_WSJ["withfnfe"],
                           NS_FRAMESTER_FRAMENET_ABOX_GFE[fr_el.name]))
                graph.add((fe_iri, NS_RDF["value"], Literal(fr_el.text)))

                # Here add part about DBpedia Spotlight
                curr_ent = [val for k, val in surf_to_ent.items() if k in fr_el.text]
                for ent in curr_ent:
                    graph.add((fe_iri, NS_SKOS["related"], URIRef(quote(ent, safe=":/"))))

                type_role = NS_NIF["Word"] if len(fr_el.text.split(" ")) == 1 else NS_NIF["Phrase"]
                graph.add((fe_iri, NS_RDF["type"], type_role))
                graph.add((fe_iri, NS_NIF["superString"], NS_EX[quote(id_sent)]))

                # start, end = self.find_start_end_token(sent=doc, substring=fr_el.text.strip())
                # graph.add((fe_iri, NS_EARMARK["begins"], Literal(start, datatype=NS_XSD["int"])))
                # graph.add((fe_iri, NS_EARMARK["ends"], Literal(end, datatype=NS_XSD["int"])))
        return graph

    def find_start_end_token(self, sent: Span, substring: str) -> (int, int):
        """ Start/End token NUMBER in a sentence """
        res = re.search(re.escape(substring), sent.text)
        idx_start, idx_end = res.start(), res.end()
        token_start = [x for x in sent if x.idx == idx_start][0].i
        token_end = [x for x in sent[token_start:] if x.idx + len(x.text) == idx_end][0].i
        return token_start, token_end


    def __call__(self, text_input, id_abstract) -> Graph:
        """ Input = 
        - text_input: full text (eg., DBpedia abstract) 
        - id_abstract: id for IRI identifier """

        # Init graph with phrases+sentences+binding
        graph = init_graph(prefix_to_ns=self.prefix_to_ns)
        doc = self.nlp(text_input)
        graph = self.add_nif_phrase_sent(graph=graph, doc=doc, id_abstract=id_abstract)

        # DBpedia Spotlight entities
        ents = [ent._.dbpedia_raw_result for ent in doc.ents if ent._.dbpedia_raw_result]
        surf_to_ent = {x['@surfaceForm']: x['@URI'] for x in ents}

        # Adding frame results
        results = self.frame_transformer.detect_frames_bulk([sent.text for sent in doc.sents])
        for id_sent, result in enumerate(results):
            graph = self.add_frame(graph=graph, id_sent=f"{id_abstract}_{id_sent}",
                                   result=result, doc=doc, surf_to_ent=surf_to_ent)

        return graph


if __name__ == '__main__':
    # context = [
    #     "The Korean War was started when North Korea invaded South Korea."#,
    #     # "The United Nations, with United States as the principal force, came to aid of South Korea."
    # ]
    # TEXT_INPUT = " ".join(context)

    TEXT_INPUT = "The Coup d'état of 18 Brumaire brought General Napoleon Bonaparte to power as First Consul of France and in the view of most historians ended the French Revolution and which will lead to the Coronation of Napoleon as Emperor. This bloodless coup d'état overthrew the Directory, replacing it with the French Consulate. This occurred on 9 November 1799, which was 18 Brumaire, Year VIII under the short-lived French Republican calendar system."
    TEXT_INPUT = "The Coup d'état of 18 Brumaire brought General Napoleon Bonaparte to power as First Consul of France and in the view of most historians ended the French Revolution and will lead to the Coronation of Napoleon as Emperor. This bloodless coup d'état overthrew the Directory, replacing it with the French Consulate. This occurred on 9 November 1799, which was 18 Brumaire, Year VIII under the short-lived French Republican calendar system."

    builder = FrameSemanticsNGBuilder()
    GRAPH = builder(text_input=TEXT_INPUT, id_abstract='23')

    # for ROW in GRAPH.query("""SELECT ?s ?p ?o WHERE {?s ?p ?o}"""):
    #     print(f"<{ROW.s}> <{ROW.p}> <{ROW.o}>")

    GRAPH.serialize("frame_kg_coup_18_brumaire.ttl")
