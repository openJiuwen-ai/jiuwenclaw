# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import List, Dict, Any
from collections import Counter

from openjiuwen.core.common.logging import LogManager

logger = LogManager.get_logger("agent_builder")


class SimpleIrToMermaid:
    """SimpleIR to Mermaid converter.

    Transforms SimpleIR workflow format to Mermaid flowchart.

    Example:
        ```python
        mermaid_code = SimpleIrToMermaid.transform_to_mermaid(nodes)
        ```
    """

    @staticmethod
    def edge_transform(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transform edge information.

        Args:
            nodes: Node list

        Returns:
            Edge list
        """
        edges: List[Dict[str, Any]] = []
        for node in nodes:
            if "next" in node and node["next"]:
                edges_item = {
                    "source": node["id"],
                    "target": node["next"],
                }
                edges.append(edges_item)
            else:
                if node.get("type") != "End":
                    conditions = node.get("parameters", {}).get("conditions", [])
                    for con in conditions:
                        if "next" in con and con["next"]:
                            con_desc = con.get("description", "")
                            edges_item = {
                                "source": node["id"],
                                "target": con["next"],
                                "branch": con.get("branch", ""),
                                "description": con_desc
                            }
                            edges.append(edges_item)
        return edges

    @staticmethod
    def trans_to_mermaid(data: Dict[str, Any]) -> str:
        """Transform to Mermaid format.

        Args:
            data: Dictionary containing nodes and edges

        Returns:
            Mermaid code string
        """
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        id_to_desc = {
            n["id"]: n.get("description", n["id"]) for n in nodes
        }
        count_edges = Counter(e["source"] for e in edges)

        lines: List[str] = ["graph TD"]

        for node_id, desc in id_to_desc.items():
            label = desc.replace('`', "'").replace('"', "'")
            lines.append(f"  {node_id}[Node{node_id}: {label}]")

        for e in edges:
            src, dst = e["source"], e["target"]
            edges_desc = e.get("description", "").replace('`', "'").replace('"', "'")

            pattern = r"^When condition met[(?P<cond>.+?)]"
            match = re.search(pattern, edges_desc)

            if count_edges[src] > 1 and edges_desc:
                label = match.group("cond") if match else edges_desc
                lines.append(f"  {src} -- {label} --> {dst}")
            else:
                lines.append(f"  {src} --> {dst}")

        return "\n".join(lines)

    @staticmethod
    def transform_to_mermaid(json_data: List[Dict[str, Any]]) -> str:
        """Transform SimpleIR format to Mermaid.

        Args:
            json_data: SimpleIR format node list

        Returns:
            Mermaid code string
        """
        edges = SimpleIrToMermaid.edge_transform(json_data)
        return SimpleIrToMermaid.trans_to_mermaid({
            "nodes": json_data,
            "edges": edges
        })
