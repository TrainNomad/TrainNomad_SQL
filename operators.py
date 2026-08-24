import json
import os
from typing import List, Dict, Any

OPERATORS_FILE = os.path.join(os.path.dirname(__file__), "operators.json")

def load_operators() -> List[Dict[str, Any]]:
    """Charge l'ensemble des opérateurs définis dans operators.json."""
    if not os.path.exists(OPERATORS_FILE):
        return []
    with open(OPERATORS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_operators(operators: List[Dict[str, Any]]) -> None:
    """Enregistre ou met à jour la liste des opérateurs."""
    with open(OPERATORS_FILE, "w", encoding="utf-8") as f:
        json.dump(operators, f, indent=2, ensure_ascii=False)

def get_operator_by_id(operator_id: str) -> Dict[str, Any]:
    """Récupère la configuration d'un opérateur spécifique."""
    for op in load_operators():
        if op.get("id") == operator_id:
            return op
    return {}