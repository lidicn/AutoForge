"""af_ir —— AutoForge IR 模型层。

唯一真相是 `schema/ir.schema.json`；本包只是它的 Python 投影与求值工具。
"""

from .expr import (
    ExprError,
    Resolver,
    collect_entity_refs,
    collect_var_refs,
    evaluate,
)
from .models import (
    EDGE_KINDS,
    EDGE_PRIORITY,
    IR_VERSION,
    IRValidationError,
    NODE_KINDS,
    VAR_TYPES,
    AskAnswer,
    AskSpec,
    Automation,
    Edge,
    Graph,
    Node,
    Trigger,
    VarDecl,
    load_automation,
    load_graph,
    validate_automation,
)

__all__ = [
    "IR_VERSION",
    "NODE_KINDS",
    "EDGE_KINDS",
    "EDGE_PRIORITY",
    "VAR_TYPES",
    "IRValidationError",
    "Automation",
    "Edge",
    "Graph",
    "Node",
    "Trigger",
    "VarDecl",
    "AskSpec",
    "AskAnswer",
    "load_automation",
    "load_graph",
    "validate_automation",
    "evaluate",
    "collect_entity_refs",
    "collect_var_refs",
    "ExprError",
    "Resolver",
]
