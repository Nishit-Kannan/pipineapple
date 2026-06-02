"""HTTP routes for PiPineapple, organised as Flask blueprints.

Each phase of the curriculum adds one (or more) blueprints here. Routes are
thin: they call into ``app.services`` and render templates. Business logic
lives in services, subprocess invocations live in ``app.tools``.
"""
