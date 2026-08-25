"""The conversational blog agent: a tool-calling loop over the existing pipeline.

This package is the orchestrator. It is deliberately separate from
``app.agents`` (plural), which holds the single-purpose LLM workers this app has
always had -- the content writer, the formatter, the SEO analyst, the
humaniser. Those know how to do one thing to a piece of text. This one decides
what to do, when, and whether the user has agreed to it yet.

Reading order, if you are new to it:

* :mod:`app.agent.registry` -- what the agent can do. The tool table is the
  clearest single view of the feature.
* :mod:`app.agent.prompts` -- what it is told to do, and why the enforcement is
  not here.
* :mod:`app.agent.loop` -- the turn: model, tools, results, repeat, persist.
* :mod:`app.agent.events` -- how a turn that outlives its HTTP request is
  watched, resumed and bounded.
* :mod:`app.agent.tools` -- the actual capabilities, each independently callable.

The public surface is intentionally small: a route runs a turn, and everything
else is internal.
"""
from app.agent.events import turns
from app.agent.loop import AgentLoop, run_turn

__all__ = ['AgentLoop', 'run_turn', 'turns']
