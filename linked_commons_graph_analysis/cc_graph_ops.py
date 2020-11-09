import json
import time
import networkx as nx
import graph_tool.all as gt


def create_graph_from_file(filename, format='nx'):
    """Opens the data from the given filepath as JSON and return a graph.
    
    Parameters:
    filename: a string with the path of a JSON file
    format: 'nx' or 'gt' for networkx or graph_tool formats

    Returns:
    a graph of the specified form
    """
    with open(filename, 'r') as f:
        data = json.load(f)
    return create_graph_from_json(data, format=format)

def _gt_vtx_props():
    return [('id', 'string'),
        ('licenses_qty', 'int'),
        ('cc_licenses', 'object'),
        ('provider_domain', 'string'),
        ('images', 'float'),
        ('links_qty', 'int'),
        ('node_size', 'int')]

def create_graph_from_json(data, format='nx'):
    """Converts the input JSON file to a graph.
    
    Parameters:
    data: JSON object with nodes, links, and associated metadata
    format: 'nx' or 'gt' for networkx or graph_tool formats

    Returns:
    a graph of the specified form
    """
    nodes = data['nodes']
    links = data['links']

    if format == 'nx':
        g = nx.DiGraph()
        for node in nodes:
            g.add_node(node['id'], **node)
        for link in links:
            g.add_edge(link['source'], link['target'], weight=link['value'])
    elif format == 'gt':
        g = gt.Graph(directed=True)
        expected_vtx_props = _gt_vtx_props()
        for prop, prop_type in expected_vtx_props:
            g.vertex_properties[prop] = g.new_vp(prop_type)
        g.edge_properties['weight'] = g.new_ep('int')
        vertices = {}
        for node in nodes:
            node_id = node['id']
            v = g.add_vertex()
            vertices[node_id] = v
            for prop, _ in expected_vtx_props:
                assert prop in node, 'vertex property {} not found not in metadata'.format(prop)
                g.vertex_properties[prop][v] = node[prop]
        for link in links:
            src = link['source']
            dst = link['target']
            weight = link['value']
            e = g.add_edge(vertices[src], vertices[dst])
            g.edge_properties['weight'][e] = weight
    else:
        raise TypeError("format must be nx or gt")
    return g


def nx2gt(nxG):
    """Converts a networkx graph to a graph-tool graph.
    Edge weights assumed to be in edge property 'weight'
    Returned graph has a vertex_property 'id' for node IDs

    Parameters:
    nxG: a networkx graph
    
    Returns:
    graph-tool graph
    """
    gtG = gt.Graph(directed=nxG.is_directed())
    expected_vtx_props = _gt_vtx_props()
    for prop, prop_type in expected_vtx_props:
        gtG.vertex_properties[prop] = gtG.new_vp(prop_type)
    gtG.edge_properties['weight'] = gtG.new_ep('int')
    vertices = {}
    for node, v_data in nxG.nodes(data=True):
        v = gtG.add_vertex()
        vertices[node] = v
        for prop, _ in expected_vtx_props:
            gtG.vertex_properties[prop][v] = v_data[prop]
    for src, dst, data in nxG.edges(data=True):
        e = gtG.add_edge(vertices[src], vertices[dst])
        gtG.edge_properties['weight'][e] = data['weight']
    return gtG

def json_to_csv(in_file, out_file):
    with open(in_file, 'r') as f:
        data = json.load(f)
    nodes = data['nodes']
    links = data['links']
    ids = dict()
    counter = 0
    for node in nodes:
        if node['id'] not in ids:
            ids[node['id']] = counter
            counter += 1
    with open(out_file, 'w') as f:
        for link in links:
            src_id = str(ids[link['source']])
            dst_id = str(ids[link['target']])
            f.write(src_id + ',' + dst_id + '\n')


def get_licenses(g):
    """Takes a graph and returns all the licenses used by any node in it.
    
    Parameters:
    g: networkx Graph or graph_tool Graph
    
    Returns:
    a set of licenses
    """  
    licenses = set()
    if isinstance(g, nx.Graph):
        for node_id, cc_licenses in g.nodes(data='cc_licenses'):
            for license in cc_licenses:
                licenses.add(license)
    elif isinstance(g, gt.Graph):
        for v in g.vertices():
            for license in g.vp['cc_licenses'][v]:
                licenses.add(license)
    else:
        raise TypeError('format must be nx or gt')
    return licenses


def restrict_graph_by_property(g, prop):
    """Takes a DiGraph and returns the induced subgraph view for nodes that have
    the given property.

    Parameters:
    g: networkx DiGraph
    prop: boolean function that takes in (node, data)

    Returns:
    a networkx subgraph view for the induced subgraph
    """
    if not isinstance(g, nx.Graph):
        raise TypeError('not a networkx graph')
    subgraph_nodes = []
    for node_id, data in g.nodes(data=True):
        if prop(node_id, data):
            subgraph_nodes.append(node_id)
    return nx.induced_subgraph(g, subgraph_nodes)


def restrict_graph_by_license(g, license):
    """Takes a DiGraph and returns the induced subgraph view for nodes that are 
    predominantly the given license. Ties between most popular licenses are not broken.

    Parameters:
    g: networkx DiGraph. Nodes assumed to have field 'cc_licenses'.
    license: a string key for the license type

    Returns:
    a networkx subgraph view for the induced subgraph
    """
    if not isinstance(g, nx.Graph):
        raise TypeError('not a networkx graph')
    subgraph_nodes = []
    for node_id, cc_licenses in g.nodes(data='cc_licenses'):
        # If the most popular license is license
        if isinstance(cc_licenses, dict):
            if license in cc_licenses and cc_licenses[license] >= max(cc_licenses.values()):
                subgraph_nodes.append(node_id)
    return nx.induced_subgraph(g, subgraph_nodes)


def all_license_subgraphs(g, licenses, quota=1, proportion=0):
    """Takes a graph and returns the subgraphs induced by different 
    licenses types.
    
    Parameters:
    g: either a gt.Graph or a nx.Graph
    licenses: list of keys to be used for the return dict
    quota: an int for the minimum number of licenses of a given type to appear
        in that licenses subgraph
    proportion: a float for the proportion of licenses on the domain that should
        be the given license type
    
    Returns:
    a dict mapping string license names to subgraphs
    """
    if isinstance(g, gt.Graph):
        subgraph_by_license = dict()
        for license in licenses:
            nodes = g.new_vp('bool')
            for v in g.vertices():
                cc_licenses = g.vp['cc_licenses'][v]
                if isinstance(cc_licenses, dict):
                    total_licenses = sum(cc_licenses.values())
                    if (license in cc_licenses
                            and cc_licenses[license] >= proportion * total_licenses
                            and cc_licenses[license] >= quota):
                        nodes[v] = True
                else:
                    nodes[v] = False
            subgraph_by_license[license] = gt.GraphView(g, vfilt=nodes)
        return subgraph_by_license
    elif isinstance(g, nx.Graph):
        subgraph_by_license = dict()
        for license in licenses:
            nodes = set()
            for v, data in g.nodes(data=True):
                cc_licenses = data['cc_licenses']
                if isinstance(cc_licenses, dict):
                    total_licenses = sum(cc_licenses.values())
                    if (license in cc_licenses
                            and cc_licenses[license] >= proportion * total_licenses
                            and cc_licenses[license] >= quota):
                        nodes.add(v)
            subgraph_by_license[license] = nx.induced_subgraph(g, nodes)
        return subgraph_by_license
    else:
        raise TypeError('graph format not recognized, must be nx or gt')

        
def get_licenses(g):
    """Get the set of licenses appearing in the graph"""
    if isinstance(g, gt.Graph):
        licenses = set()
        for i, cc_licenses in enumerate(g.vp['cc_licenses']):
            if isinstance(cc_licenses, dict):
                licenses |= cc_licenses.keys()
        return licenses
    elif isinstance(g, nx.Graph):
        licenses = set()
        for node, data in g.nodes(data=True):
            cc_licenses = data['cc_licenses']
            if isinstance(cc_licenses, dict):
                licenses |= cc_licenses.keys()
        return licenses
    else:
        raise TypeError('graph format not recognized')
    

def cc_licenses_by_domain(g):
    """Returns a dictionary mapping node_id to their cc_licenses dict, assuming it is nonempty
    
    Parameters:
    g: networkx DiGraph. Nodes assumed to have filed 'cc_licenses'
    
    Returns:
    a dictionary with keys 'node_id' and values 'dict of cc_licenses'
    """
    ret = dict()
    for node_id, cc_licenses in g.nodes(data='cc_licenses'):
        if isinstance(cc_licenses, dict):
            ret[node_id] = cc_licenses
    return ret

def time_method(func, *args, ppd=3, return_time=False):
    """Computes the time of execution for a function.
    
    Parameters:
    func: function to be applied
    args: any number of arguments to be passed into the function
    return_time: controls whether to return only the result or the result and time elapsed
    
    Returns:
    depending on return_time, either result or the tuple (result, time_elapsed)
    """
    tic = time.time()
    res = func(*args)
    toc = time.time()
    print(f"{func.__name__} computed in {toc-tic:.3f} seconds.")
    if return_time:
        return res, toc - tic
    else:
        return res