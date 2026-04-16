"""
EcoZap — Agents Package
========================
Importa e registra todos os agentes automaticamente.
Chamar load_all_agents() no startup da aplicação.
"""
from app.agents.registry import load_all_agents, get_agent, get_all_agents, list_registered

__all__ = ["load_all_agents", "get_agent", "get_all_agents", "list_registered"]
