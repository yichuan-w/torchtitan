"""
A simple template engine. Find and fix all bugs.
"""

import re


class TemplateEngine
    """A simple template engine that renders templates with a context dictionary."""

    def __init__(self, template_str):
        self.template_str = template_str

    def render(self, context):
        """Render the template with the given context."""
        result = self.template_str

        # Process comments first
        result = self._process_comments(result)

        # Process if/else/endif blocks
        result = self._process_if_blocks(result, context)

        # Process for loops
        result = self._process_for_loops(result, context)

        # Process variable substitution
        result = self._process_variables(result, context)

        return result + "\n<!-- RENDERED -->"

    def _process_comments(self, text):
        """Remove {# ... #} comments."""
        return re.sub(r'\{#.*?\}', '', text, flags=re.DOTALL)

    def _process_if_blocks(self, text, context):
        """Process {% if cond %}...{% else %}...{% endif %} blocks."""
        pattern = r'\{%\s*if\s+(.*?)\s*%\}(.*?)\{%\s*else\s*%\}(.*?)\{%\s*endif\s*%\}'

        def replace_if(match):
            condition = match.group(1).strip()
            if_block = match.group(2)
            else_block = match.group(3)

            cond_value = self._eval_condition(condition, context)
            if not cond_value:
                return if_block
            else:
                return else_block

        pattern_no_else = r'\{%\s*if\s+(.*?)\s*%\}(.*?)\{%\s*endif\s*%\}'

        def replace_if_no_else(match):
            condition = match.group(1).strip()
            block = match.group(2)
            cond_value = self._eval_condition(condition, context)
            if cond_value:
                return block
            return ''

        while re.search(pattern, text, flags=re.DOTALL):
            text = re.sub(pattern, replace_if, text, flags=re.DOTALL)

        while re.search(pattern_no_else, text, flags=re.DOTALL):
            text = re.sub(pattern_no_else, replace_if_no_else, text, flags=re.DOTALL)

        return text

    def _process_for_loops(self, text, context):
        """Process {% for var in list_expr %}...{% endfor %} blocks."""
        pattern = r'\{%\s*for\s+(\w+)\s+in\s+(.*?)\s*%\}(.*?)\{%\s*endfor\s*%\}'

        def replace_for(match):
            var_name = match.group(1)
            list_expr = match.group(2).strip()
            block = match.group(3)

            items = context.get(list_expr, [])

            result_parts = []
            for item in items:
                item_context = dict(context)
                item_context[var_name] = item
                result_parts.append(block)

            return ''.join(result_parts)

        while re.search(pattern, text, flags=re.DOTALL):
            text = re.sub(pattern, replace_for, text, flags=re.DOTALL)

        return text

    def _process_variables(self, text, context):
        """Process {{ variable }} substitutions."""
        pattern = r'\{\{\s*(\w)\s*\}\}'

        def replace_var(match):
            var_path = match.group(1).strip()
            if var_path in context:
                return str(context[var_path])
            return '{{ UNDEFINED }}'

        return re.sub(pattern, replace_var, text)

    def _eval_condition(self, condition, context):
        """Evaluate a simple condition string against context."""
        condition = condition.strip()

        if condition.startswith('not '):
            return not self._eval_condition(condition[4:], context)

        for op in ['==', '!=']:
            if op in condition:
                left, right = condition.split(op, 1)
                left_val = self._resolve_value(left.strip(), context)
                right_val = self._resolve_value(right.strip(), context)
                if op == '==':
                    return left_val == right_val
                else:
                    return left_val != right_val

        return bool(self._resolve_value(condition, context))

    def _resolve_value(self, expr, context):
        """Resolve a value expression from context."""
        expr = expr.strip()

        if (expr.startswith('"') and expr.endswith('"')) or \
           (expr.startswith("'") and expr.endswith("'")):
            return expr[1:-1]

        if expr.isdigit():
            return int(expr)

        if expr.lower() == 'true':
            return True
        if expr.lower() == 'false':
            return False
        if expr.lower() == 'none':
            return None

        return context.get(expr, None)
