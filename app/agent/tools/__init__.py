"""Tool implementations for the conversational blog agent.

One module per domain. Every tool has the same signature -- ``fn(ctx, **params)``
-- takes its authority from the :class:`~app.agent.context.ToolContext` and
never from a parameter, returns a JSON-serialisable dict, and does not raise for
anything the conversation could recover from.

They are importable and callable on their own, with no Flask request, no app
context and no model. That is what makes them testable, and it is why the loop
is thin: everything interesting happens in a function you can call from a test.
"""
from app.agent.tools import blogs, outlines, research

__all__ = ['blogs', 'outlines', 'research']
