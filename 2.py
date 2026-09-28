import os
import re
import networkx as nx
from pyvis.network import Network

G = nx.DiGraph()

root_files = [f for f in os.listdir('.') if f.endswith('.py')]

for py_file in root_files:
    G.add_node(py_file, group="Python Script", color="#1f77b4")
    try:
        with open(py_file, 'r', encoding='utf-8') as f:
            content = f.read()
            for target in root_files:
                module_name = target.replace('.py', '')
                if re.search(fr'\b(import {module_name}|from {module_name} import)\b', content):
                    G.add_edge(py_file, target, label="imports")
    except Exception:
        pass

net = Network(height="750px", width="100%", bgcolor="#222222", font_color="white", directed=True)
net.from_nx(G)

# Safe HTML Export
net.write_html("project_graph.html")
print("Done! Open project_graph.html in your browser.")