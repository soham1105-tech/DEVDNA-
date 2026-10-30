from tree_sitter import Language, Parser
import tree_sitter_python as tspython

PY_LANGUAGE = Language(tspython.language())
parser = Parser(PY_LANGUAGE)

def analyze_code_structure(raw_code: str):
    tree = parser.parse(bytes(raw_code, "utf8"))
    
    stats = {
        "total_nodes": 0,
        "functions": 0,
        "classes": 0,
        "control_flow_branches": 0,
        "max_depth": 0
    }
    
    def traverse(node, depth):
        stats["total_nodes"] += 1
        stats["max_depth"] = max(stats["max_depth"], depth)
        
        if node.type == 'function_definition':
            stats["functions"] += 1
        elif node.type == 'class_definition':
            stats["classes"] += 1
        elif node.type in ['if_statement', 'for_statement', 'while_statement', 'try_statement']:
            stats["control_flow_branches"] += 1
            
        for child in node.children:
            traverse(child, depth + 1)

    traverse(tree.root_node, 0)
    
    if stats["total_nodes"] == 0:
        return {"ast_stats": stats, "human_architect_score": 0.0, "is_ai_generated_flag": False}
        
    # Adjusted Heuristic:
    # Give weight to functions, classes, depth, and control loops
    modularity = (stats["functions"] * 25) + (stats["classes"] * 30)
    branching = stats["control_flow_branches"] * 20
    depth_score = min(20, stats["max_depth"] * 2)
    
    # Scale against node density
    density_factor = min(1.0, stats["total_nodes"] / 100.0)
    
    raw_score = (modularity + branching + depth_score) * density_factor
    final_score = min(98.0, max(15.0, round(raw_score, 2)))
    
    return {
        "ast_stats": stats,
        "human_architect_score": final_score,
        "is_ai_generated_flag": final_score < 30.0
    }