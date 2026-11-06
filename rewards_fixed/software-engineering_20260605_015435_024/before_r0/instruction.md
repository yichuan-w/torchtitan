The file `/app/template_engine.py` contains a simple template engine that is supposed to render text templates with variable substitution, if/else conditionals, for loops, and comments. However, it has several bugs that cause incorrect output.

The template syntax is:
- `{{ variable }}` — substitute the value of `variable` from the context
- `{% if condition %}...{% else %}...{% endif %}` — conditional block (else is optional)
- `{% for item in list_var %}...{% endfor %}` — loop over a list from context
- `{# comment text #}` — comments that are removed from output

The `TemplateEngine` class takes a template string and provides a `render(context)` method that returns the rendered output. The `context` is a dictionary that can contain nested dicts, lists, strings, numbers, and booleans.

Variables may use dotted paths like `{{ user.name }}` to access nested dict keys.

You must find and fix ALL bugs in `/app/template_engine.py` so that the tests pass. Do NOT rewrite the engine from scratch — fix the existing code.
