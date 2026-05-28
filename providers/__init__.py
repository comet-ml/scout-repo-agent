"""Repository providers — the seam between Scout's agent loop and a backend."""
from .base import RepositoryProvider
from .github import GitHubProvider
from .simulator import GitHubSimulator

__all__ = ["RepositoryProvider", "GitHubProvider", "GitHubSimulator"]
