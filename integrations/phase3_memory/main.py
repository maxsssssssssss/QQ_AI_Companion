"""Loadable helper container; Phase3Runtime owns the only live coordinator."""
from astrbot.api.star import Context, Star


class Phase3MemorySupport(Star):
    def __init__(self, context: Context):
        super().__init__(context)
