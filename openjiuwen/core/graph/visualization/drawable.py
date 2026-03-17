# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import contextlib
import io
from dataclasses import dataclass
from inspect import ismethod, isfunction, isclass
from typing import Any, get_args, get_type_hints, get_origin, Literal, List, Optional, Union

from openjiuwen.core.common.exception.errors import build_error

with contextlib.redirect_stdout(io.StringIO()):
    try:
        from mermaid import Direction, Mermaid
        from mermaid.flowchart import FlowChart, Link, LinkShape, Node, LinkHead

        _MERMAID_AVAILABLE = True
    except ModuleNotFoundError:
        _MERMAID_AVAILABLE = False

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow import ComponentComposable
from openjiuwen.core.workflow import BranchRouter
from openjiuwen.core.graph.visualization.drawable_edge import DrawableEdge
from openjiuwen.core.graph.visualization.drawable_graph import DrawableGraph
from openjiuwen.core.graph.visualization.drawable_subgraph_node import DrawableSubgraphNode
from openjiuwen.core.graph.visualization.drawable_node import DrawableNode


def _get_targets(data: Any) -> list[str]:
    func = None
    if isfunction(data) or ismethod(data):
        func = data
    elif isclass(data):
        func = data.__call__ if hasattr(data, "__call__") else None
    elif callable(data):
        func = data.__call__
    if func is None:
        return []
    rtn_type = get_type_hints(func).get("return")
    if rtn_type and get_origin(rtn_type) is Literal:
        targets = [name for name in get_args(rtn_type)]
        return targets
    return []


class Drawable:
    def __init__(self):
        self._graph = DrawableGraph(nodes={}, edges=[], start_nodes=[], end_nodes=[], break_nodes=[])
        self._loop_nodes = set()

    def add_node(self, node_id: str, component: ComponentComposable):
        """convert component to DrawableNode & save it to self._graph.nodes"""
        from openjiuwen.core.workflow import LoopComponent
        from openjiuwen.core.workflow.components.flow.loop.loop_comp import AdvancedLoopComponent
        from openjiuwen.core.workflow.components.flow.workflow_comp import SubWorkflowComponent
        from openjiuwen.core.workflow import BranchComponent
        from openjiuwen.core.workflow import IntentDetectionComponent
        if isinstance(component, LoopComponent) or isinstance(component, AdvancedLoopComponent):
            subgraph = component.loop_group.drawable.get_graph() \
                if isinstance(component, LoopComponent) else component.body.drawable.get_graph()
            # If end nodes are unset, the graph is traversed to discover all end nodes.
            if len(subgraph.end_nodes) == 0:
                out_degrees = {subgraph_node_id: 0 for subgraph_node_id in subgraph.nodes}
                for edge in subgraph.edges:
                    if edge.source not in out_degrees:
                        out_degrees[edge.source] = 0
                    out_degrees[edge.source] += 1
                for subgraph_node_id, out_degree in out_degrees.items():
                    if out_degree == 0:
                        subgraph.end_nodes.append(subgraph.nodes[subgraph_node_id])
            self._graph.nodes[node_id] = DrawableSubgraphNode(id=node_id, subgraph=subgraph)
            self._loop_nodes.add(node_id)
            self.add_edge(node_id, node_id)
        elif isinstance(component, SubWorkflowComponent):
            self._graph.nodes[node_id] = DrawableSubgraphNode(id=node_id,
                                                              subgraph=component.sub_workflow._internal.drawable.get_graph())
        elif isinstance(component, BranchComponent) or isinstance(component, IntentDetectionComponent):
            self._graph.nodes[node_id] = DrawableNode(node_id)
            self.add_edge(source=node_id, conditional=True, data=component.router())
        else:
            self._graph.nodes[node_id] = DrawableNode(node_id)

    def set_start_node(self, node_id: str):
        """save node whose id is node_id to self._graph.start_nodes"""
        if node_id not in self._graph.nodes:
            raise build_error(StatusCode.DRAWABLE_GRAPH_START_NODE_INVALID, node_id=node_id,
                              reason=f"node '{node_id}' does not exist in the graph")
        self._graph.start_nodes.append(self._graph.nodes[node_id])

    def set_end_node(self, node_id: str):
        """save node whose id is node_id to self._graph.end_nodes"""
        if node_id not in self._graph.nodes:
            raise build_error(StatusCode.DRAWABLE_GRAPH_END_NODE_INVALID, node_id=node_id,
                              reason=f"node '{node_id}' does not exist in the graph")
        self._graph.end_nodes.append(self._graph.nodes[node_id])

    def set_break_node(self, node_id: str):
        """save node whose id is node_id to self._graph.break_nodes"""
        if node_id not in self._graph.nodes:
            raise build_error(StatusCode.DRAWABLE_GRAPH_BREAK_NODE_INVALID, node_id=node_id,
                              reason=f"node '{node_id}' does not exist in the graph")
        self._graph.break_nodes.append(self._graph.nodes[node_id])

    def add_edge(self, source: str, target: str = None, conditional: bool = False, streaming: bool = False,
                 data: Any = None):
        """add edge to self._graph.edges"""
        if source in self._loop_nodes:
            self._graph.edges.append(DrawableEdge(source=source, target=target, conditional=True))
            return
        if not conditional:
            self._graph.edges.append(DrawableEdge(source=source, target=target, data=data, conditional=conditional,
                                                  streaming=streaming))
            return
        branch_datas = None
        if isinstance(data, BranchRouter):
            drawable_branch_router = data.get_drawable_branch_router()
            targets = drawable_branch_router.targets
            branch_datas = drawable_branch_router.datas
        else:
            targets = _get_targets(data)
        for i, t in enumerate(targets):
            self._graph.edges.append(DrawableEdge(source=source, target=t,
                                                  data=branch_datas[i] if branch_datas is not None else None,
                                                  conditional=conditional, streaming=streaming))

    def to_mermaid(self, title: str = "", expand_subgraph: int | bool = False, enable_animation: bool = False) -> str:
        """convert self._graph to Mermaid syntax"""
        return _MermaidDiagram().to_mermaid(self._graph, title, expand_subgraph, enable_animation)

    def to_mermaid_png(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        """convert self._graph to Mermaid syntax and render it as png"""
        return _MermaidDiagram().to_mermaid_png(self._graph, title, expand_subgraph)

    def to_mermaid_svg(self, title: str = "", expand_subgraph: int | bool = False) -> bytes:
        """convert self._graph to Mermaid syntax and render it as svg"""
        return _MermaidDiagram().to_mermaid_svg(self._graph, title, expand_subgraph)

    def get_graph(self) -> DrawableGraph:
        """get drawable graph"""
        return self._graph


if not _MERMAID_AVAILABLE:
    class _MermaidDiagram:
        def __init__(self):
            self._raise()

        @staticmethod
        def _raise():
            raise ImportError("Mermaid package is not installed. Please install it by `pip install mermaid-py`.")

        def to_mermaid(self, *args, **kwargs):
            self._raise()

        def to_mermaid_png(self, *args, **kwargs):
            self._raise()

        def to_mermaid_svg(self, *args, **kwargs):
            self._raise()

else:

    class _MermaidDiagram:
        class _NodeIdGenerator:
            _prefix = "node"

            def __init__(self):
                self._node_id = 0

            def next(self):
                self._node_id += 1
                return "_".join([self._prefix, str(self._node_id)])

        class _LinkIdGenerator:
            _prefix = "link"

            def __init__(self):
                self._node_id = 0

            def next(self):
                self._node_id += 1
                return "_".join([self._prefix, str(self._node_id)])

        @dataclass
        class _SubGraphNode:
            node: Node
            subgraph_links: Optional[List[Link]] = None
            subgraph_start_nodes: Optional[List[Node]] = None
            subgraph_end_nodes: Optional[List[Node]] = None
            subgraph_break_nodes: Optional[List[Node]] = None

        class _ExtendLink(Link):
            def __init__(
                    self,
                    origin: Node,
                    end: Node,
                    shape: Union[str, LinkShape] = "normal",
                    head_left: Union[str, LinkHead] = "none",
                    head_right: Union[str, LinkHead] = "arrow",
                    message: str = "",
                    id_: str = "",
                    properties: Optional[dict] = None,
            ) -> None:
                super().__init__(origin, end, shape, head_left, head_right, message)
                self.id_ = id_
                self.properties = properties
                if self.message.startswith("|") and self.message.endswith("|"):
                    self.message = message

            def __str__(self) -> str:
                tag = "" if not self.id_ else "".join([self.id_, "@"])
                properties_str = ""
                if self.properties:
                    properties_str = "".join(["\n", tag,
                                              "{" + ", ".join(f"{k}: {v}" for k, v in self.properties.items()) + "}"])
                element: list[str] = [
                    self.origin.id_,
                    " ",
                    tag,
                    self.head_left,
                    self.shape,
                    self.head_right,
                    f'|"{self.message}"|' if self.message else "",
                    " ",
                    self.end.id_,
                    properties_str
                ]
                return "".join(element)

        def __init__(self):
            self._node_id_generator = self._NodeIdGenerator()
            self._link_id_generator = self._LinkIdGenerator()

        def to_mermaid(self, graph: DrawableGraph, title: str = "", expand_subgraph: int | bool = False,
                       enable_animation: bool = False) -> str:
            """convert graph to Mermaid syntax"""
            if not isinstance(title, str):
                raise build_error(StatusCode.DRAWABLE_GRAPH_TO_MERMAID_INVALID, reason="'title' type is not str")
            if not (isinstance(expand_subgraph, bool) or (type(expand_subgraph) is int and expand_subgraph >= 0)):
                raise build_error(StatusCode.DRAWABLE_GRAPH_TO_MERMAID_INVALID,
                                  reason="'expand_subgraph' type is not bool")
            if not isinstance(enable_animation, bool):
                raise build_error(StatusCode.DRAWABLE_GRAPH_TO_MERMAID_INVALID,
                                  reason="'enable_animation' type is not bool")

            mermaid_nodes = {}
            subgraph_mermaid_nodes = {}
            for node in graph.nodes.values():
                if expand_subgraph and isinstance(node, DrawableSubgraphNode):
                    subgraph_mermaid_nodes[node.id] = self._gen_mermaid_node(
                        expand_subgraph=expand_subgraph \
                            if isinstance(expand_subgraph, bool) or expand_subgraph <= 0 else expand_subgraph - 1,
                        node=node, enable_animation=enable_animation)
                else:
                    shape = "normal"
                    if node in graph.start_nodes or node in graph.end_nodes:
                        shape = "round-edge"
                    mermaid_nodes[node.id] = Node(id_=self._node_id_generator.next(), content=node.id, shape=shape)

            links = self._gen_mermaid_links(graph, mermaid_nodes, subgraph_mermaid_nodes, enable_animation)
            nodes = [node for node in mermaid_nodes.values()] + [node.node for node in subgraph_mermaid_nodes.values()]
            chart = FlowChart(title, nodes, links)
            return chart.script

        def to_mermaid_png(self, graph: DrawableGraph, title: str = "", expand_subgraph: int | bool = False) -> bytes:
            """convert self._graph to Mermaid syntax and render it as png"""
            return Mermaid(self.to_mermaid(graph=graph, title=title,
                                           expand_subgraph=expand_subgraph)).img_response.content

        def to_mermaid_svg(self, graph: DrawableGraph, title: str = "", expand_subgraph: int | bool = False) -> bytes:
            """convert self._graph to Mermaid syntax and render it as svg"""
            return Mermaid(self.to_mermaid(graph=graph, title=title,
                                           expand_subgraph=expand_subgraph,
                                           enable_animation=True)).svg_response.content

        def _gen_mermaid_node(self, expand_subgraph: int | bool, node: DrawableSubgraphNode,
                              enable_animation: bool) -> _SubGraphNode:
            mermaid_nodes = {}
            subgraph_mermaid_nodes = {}
            graph = node.subgraph
            for sub_node in graph.nodes.values():
                if expand_subgraph and isinstance(sub_node, DrawableSubgraphNode):
                    subgraph_mermaid_nodes[sub_node.id] = self._gen_mermaid_node(
                        expand_subgraph=expand_subgraph \
                            if isinstance(expand_subgraph, bool) or expand_subgraph <= 0 else expand_subgraph - 1,
                        node=sub_node, enable_animation=enable_animation)
                else:
                    shape = "normal"
                    if sub_node in graph.start_nodes or sub_node in graph.end_nodes:
                        shape = "round-edge"
                    mermaid_nodes[sub_node.id] = Node(id_=self._node_id_generator.next(),
                                                      content=sub_node.id, shape=shape)

            links = self._gen_mermaid_links(graph, mermaid_nodes, subgraph_mermaid_nodes, enable_animation)

            def get_node_by_id(node_id):
                if node_id in mermaid_nodes:
                    return mermaid_nodes[node_id]
                elif node_id in subgraph_mermaid_nodes:
                    return subgraph_mermaid_nodes[node_id].node
                else:
                    return "invalid node id"

            subgraph_start_nodes = [get_node_by_id(start_node.id) for start_node in graph.start_nodes]
            subgraph_end_nodes = [get_node_by_id(end_node.id) for end_node in graph.end_nodes]
            subgraph_break_nodes = [get_node_by_id(break_node.id) for break_node in graph.break_nodes]
            sub_nodes = ([sub_node for sub_node in mermaid_nodes.values()] +
                         [sub_node.node for sub_node in subgraph_mermaid_nodes.values()])
            subgraph_node = self._SubGraphNode(node=Node(id_=self._node_id_generator.next(), content=node.id,
                                                         sub_nodes=sub_nodes, direction=Direction.TOP_TO_BOTTOM),
                                               subgraph_links=links,
                                               subgraph_start_nodes=subgraph_start_nodes,
                                               subgraph_end_nodes=subgraph_end_nodes,
                                               subgraph_break_nodes=subgraph_break_nodes)
            return subgraph_node

        def _gen_mermaid_links(self, graph: DrawableGraph, mermaid_nodes: dict, subgraph_mermaid_nodes: dict,
                               enable_animation: bool) -> List[Link]:
            links = []
            link_cls = self._ExtendLink
            for edge in graph.edges:
                shape = LinkShape.NORMAL
                message = ""
                link_extend_args = {}
                if edge.conditional:
                    shape = LinkShape.DOTTED
                if edge.streaming:
                    shape = LinkShape.THICK
                    if enable_animation:
                        link_extend_args["id_"] = self._link_id_generator.next()
                        link_extend_args["properties"] = {"animate": "true"}
                if edge.conditional and edge.data:
                    message = edge.data
                if edge.source in mermaid_nodes and edge.target in mermaid_nodes:
                    links.append(link_cls(mermaid_nodes[edge.source], mermaid_nodes[edge.target], shape=shape,
                                          message=message, **link_extend_args))
                elif edge.source in mermaid_nodes and edge.target in subgraph_mermaid_nodes:
                    for node in subgraph_mermaid_nodes[edge.target].subgraph_start_nodes:
                        links.append(link_cls(mermaid_nodes[edge.source], node, shape=shape, message=message,
                                              **link_extend_args))
                elif edge.source in subgraph_mermaid_nodes and edge.target in mermaid_nodes:
                    for node in subgraph_mermaid_nodes[edge.source].subgraph_end_nodes:
                        links.append(link_cls(node, mermaid_nodes[edge.target], shape=shape, message=message,
                                              **link_extend_args))
                    for node in subgraph_mermaid_nodes[edge.source].subgraph_break_nodes:
                        links.append(link_cls(node, mermaid_nodes[edge.target], shape=shape, message=message,
                                              **link_extend_args))
                elif edge.source in subgraph_mermaid_nodes and edge.target in subgraph_mermaid_nodes:
                    for node_source in subgraph_mermaid_nodes[edge.source].subgraph_end_nodes:
                        for node_target in subgraph_mermaid_nodes[edge.target].subgraph_start_nodes:
                            links.append(link_cls(node_source, node_target, shape=shape, message=message,
                                                  **link_extend_args))

            for node in subgraph_mermaid_nodes.values():
                links += node.subgraph_links
            return links
