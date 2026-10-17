import os

from falkordb import FalkorDB

GRAPH = os.environ.get("INHERITANCE_GRAPH", "inheritance")


def graph(name: str = GRAPH):
    db = FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"),
                  port=int(os.environ.get("FALKORDB_PORT", "6379")))
    return db.select_graph(name)
