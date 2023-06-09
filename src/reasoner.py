# -*- coding: utf-8 -*-
"""
Reasoning over owl predicates
"""
from rdflib.namespace import OWL

class Reasoner:
    """ Main class for reasoning """
    def __init__(self):
        self.cached = {}
        self.referents = {}

        self.same_as = str(OWL.sameAs)

    def get_same_as_nodes(self, interface, node):
        """ Return all ?n s.t. (node, owl:sameAs, ?n) 
        in the data of the interface """
        params = {"subject": node, "predicate": self.same_as}
        triples = interface.get_triples(**params)
        same_nodes = [n for n in list(set(x[2] for x in triples)) if n != node]
        # Updating cached and referents
        self.update_cached(nodes=same_nodes + [node])
        self.update_referents(ref=node, nodes=same_nodes)
        return same_nodes

    def update_cached(self, nodes):
        """ Update cached sameAs nodes """
        nodes = list(set(nodes))
        for node in nodes:
            self.cached[node] = nodes

    def update_referents(self, ref, nodes):
        """ One referent node for the set of nodes """
        for node in nodes + [ref]:
            self.referents[node] = ref

    def update_nodes(self, interface, nodes):
        """
        - Input = list of nodes + interface (hdt/sparql)
        - Output = list of nodes
        For each node in input, update the list with the owl:sameAs links
        """
        pending, updated_nodes = nodes.copy(), nodes.copy()
        while pending:
            curr_node = pending.pop()
            same_nodes = self.cached.get(curr_node,
                                         self.get_same_as_nodes(interface, curr_node))
            updated_nodes.extend(same_nodes)
            pending = [x for x in pending if x not in updated_nodes]

        return list(set(updated_nodes))
