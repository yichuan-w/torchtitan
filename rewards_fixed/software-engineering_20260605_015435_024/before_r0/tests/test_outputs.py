import sys
import pytest

sys.path.insert(0, "/app")


# ========== Module existence ==========

def test_module_exists():
    try:
        import template_engine
        assert template_engine is not None
    except ImportError as e:
        pytest.fail(f"Failed to import template_engine: {e}")


def test_class_exists():
    from template_engine import TemplateEngine
    assert TemplateEngine is not None


# ========== Basic variable substitution ==========

def test_simple_variable():
    from template_engine import TemplateEngine
    t = TemplateEngine("Hello, {{ name }}!")
    result = t.render({"name": "World"})
    assert result == "Hello, World!", f"Got: {result!r}"


def test_multiple_variables():
    from template_engine import TemplateEngine
    t = TemplateEngine("{{ first }} {{ last }}")
    result = t.render({"first": "John", "last": "Doe"})
    assert result == "John Doe", f"Got: {result!r}"


def test_variable_not_in_context():
    from template_engine import TemplateEngine
    t = TemplateEngine("{{ missing }}")
    result = t.render({})
    assert "UNDEFINED" in result, f"Expected undefined marker, got: {result!r}"


def test_numeric_variable():
    from template_engine import TemplateEngine
    t = TemplateEngine("Count: {{ count }}")
    result = t.render({"count": 42})
    assert result == "Count: 42", f"Got: {result!r}"


# ========== Nested/dotted variable access ==========

def test_dotted_variable():
    from template_engine import TemplateEngine
    t = TemplateEngine("{{ user.name }} is {{ user.age }}")
    result = t.render({"user": {"name": "Alice", "age": 30}})
    assert result == "Alice is 30", f"Got: {result!r}"


def test_deeply_nested_variable():
    from template_engine import TemplateEngine
    t = TemplateEngine("{{ a.b.c }}")
    result = t.render({"a": {"b": {"c": "deep"}}})
    assert result == "deep", f"Got: {result!r}"


def test_dotted_variable_missing():
    from template_engine import TemplateEngine
    t = TemplateEngine("{{ user.phone }}")
    result = t.render({"user": {"name": "Bob"}})
    # Should show undefined, not crash
    assert "UNDEFINED" in result, f"Got: {result!r}"


# ========== Comments ==========

def test_comment_removed():
    from template_engine import TemplateEngine
    t = TemplateEngine("before{# this is a comment #}after")
    result = t.render({})
    assert result == "beforeafter", f"Got: {result!r}"


def test_comment_multiline():
    from template_engine import TemplateEngine
    t = TemplateEngine("start{# line1\nline2 #}end")
    result = t.render({})
    assert result == "startend", f"Got: {result!r}"


# ========== If/else blocks ==========

def test_if_true():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if show %}visible{% endif %}")
    result = t.render({"show": True})
    assert result == "visible", f"Got: {result!r}"


def test_if_false():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if show %}visible{% endif %}")
    result = t.render({"show": False})
    assert result == "", f"Got: {result!r}"


def test_if_else_true():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if flag %}yes{% else %}no{% endif %}")
    result = t.render({"flag": True})
    assert result == "yes", f"Got: {result!r}"


def test_if_else_false():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if flag %}yes{% else %}no{% endif %}")
    result = t.render({"flag": False})
    assert result == "no", f"Got: {result!r}"


def test_if_comparison_equal():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if x == 10 %}ten{% else %}other{% endif %}")
    result = t.render({"x": 10})
    assert result == "ten", f"Got: {result!r}"


def test_if_comparison_not_equal():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if x != 10 %}not ten{% endif %}")
    result = t.render({"x": 5})
    assert result == "not ten", f"Got: {result!r}"


def test_if_string_comparison():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if role == 'admin' %}admin panel{% endif %}")
    result = t.render({"role": "admin"})
    assert result == "admin panel", f"Got: {result!r}"


def test_if_not():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if not flag %}disabled{% endif %}")
    result = t.render({"flag": False})
    assert result == "disabled", f"Got: {result!r}"


def test_if_not_truthy():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if not items %}empty{% endif %}")
    result = t.render({"items": []})
    assert result == "empty", f"Got: {result!r}"


def test_if_truthy_non_bool():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if name %}has name{% endif %}")
    result = t.render({"name": "Alice"})
    assert result == "has name", f"Got: {result!r}"


def test_if_falsy_empty_string():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if name %}has name{% else %}no name{% endif %}")
    result = t.render({"name": ""})
    assert result == "no name", f"Got: {result!r}"


# ========== For loops ==========

def test_for_loop():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for item in items %}[{{ item }}]{% endfor %}")
    result = t.render({"items": ["a", "b", "c"]})
    assert result == "[a][b][c]", f"Got: {result!r}"


def test_for_loop_empty():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for item in items %}[{{ item }}]{% endfor %}")
    result = t.render({"items": []})
    assert result == "", f"Got: {result!r}"


def test_for_loop_single():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for x in xs %}{{ x }}{% endfor %}")
    result = t.render({"xs": [99]})
    assert result == "99", f"Got: {result!r}"


def test_for_loop_with_variables():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for item in items %}{{ prefix }}{{ item }}{% endfor %}")
    result = t.render({"items": [1, 2], "prefix": "#"})
    assert result == "#1#2", f"Got: {result!r}"


def test_for_loop_context_isolation():
    """Loop variable should not leak outside the loop."""
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for item in items %}{{ item }}{% endfor %}{{ item }}")
    result = t.render({"items": [1, 2], "item": "outer"})
    # After loop, item should be "outer" (from parent context)
    assert result == "12outer", f"Got: {result!r}"


# ========== Combined features ==========

def test_if_inside_for():
    from template_engine import TemplateEngine
    t = TemplateEngine(
        "{% for item in items %}"
        "{% if item.active %}[{{ item.name }}]{% endif %}"
        "{% endfor %}"
    )
    context = {
        "items": [
            {"name": "a", "active": True},
            {"name": "b", "active": False},
            {"name": "c", "active": True},
        ]
    }
    result = t.render(context)
    assert result == "[a][c]", f"Got: {result!r}"


# Note: Nested for loops are not required. The regex-based approach
# naturally handles sequential constructs but nested blocks of the same
# type are not supported.


def test_complex_template():
    from template_engine import TemplateEngine
    t = TemplateEngine(
        "Hello {{ user.name }}! "
        "{% if user.admin %}Admin{% else %}User{% endif %}. "
        "Items: {% for item in items %}{{ item }},{% endfor %}"
    )
    context = {
        "user": {"name": "Alice", "admin": True},
        "items": ["apple", "banana"],
    }
    result = t.render(context)
    assert "Hello Alice!" in result
    assert "Admin" in result
    assert "apple,banana" in result


# ========== Negative numbers ==========

def test_negative_number_comparison():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if value == -5 %}negative five{% endif %}")
    result = t.render({"value": -5})
    assert result == "negative five", f"Got: {result!r}"


def test_negative_number_in_context():
    from template_engine import TemplateEngine
    t = TemplateEngine("{{ temperature }}")
    result = t.render({"temperature": -10})
    assert result == "-10", f"Got: {result!r}"


# ========== Boolean literals ==========

def test_boolean_false_comparison():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if active == false %}inactive{% endif %}")
    result = t.render({"active": False})
    assert result == "inactive", f"Got: {result!r}"


def test_boolean_true_comparison():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if flag == true %}yes{% endif %}")
    result = t.render({"flag": True})
    assert result == "yes", f"Got: {result!r}"


# ========== None handling ==========

def test_none_comparison():
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if value == none %}null{% endif %}")
    result = t.render({"value": None})
    assert result == "null", f"Got: {result!r}"


# ========== Error cases ==========

def test_undefined_variable_in_condition():
    """Condition referencing undefined variable should be falsy."""
    from template_engine import TemplateEngine
    t = TemplateEngine("{% if missing %}shown{% else %}hidden{% endif %}")
    result = t.render({})
    assert result == "hidden", f"Got: {result!r}"


# ========== Fixed: if/else logic was inverted ==========

def test_if_else_correct_branch():
    """This specifically tests that if/else uses the correct branch."""
    from template_engine import TemplateEngine
    # When condition is True, the if-block should be rendered
    t = TemplateEngine("{% if x %}A{% else %}B{% endif %}")
    result = t.render({"x": True})
    assert result == "A", f"Expected 'A' for true condition, got {result!r}"

    # When condition is False, the else-block should be rendered
    result = t.render({"x": False})
    assert result == "B", f"Expected 'B' for false condition, got {result!r}"


# ========== Fixed: For loop variable substitution ==========

def test_for_loop_variable_substitution():
    """Loop variable should be substituted inside the loop body."""
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for x in xs %}{{ x }}{% endfor %}")
    result = t.render({"xs": [1, 2, 3]})
    assert result == "123", f"Got: {result!r}"


# ========== Fixed: Dotted access for loop items ==========

def test_dotted_access_in_loop():
    from template_engine import TemplateEngine
    t = TemplateEngine(
        "{% for p in people %}{{ p.name }}:{{ p.age }} {% endfor %}"
    )
    context = {
        "people": [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
    }
    result = t.render(context)
    assert result == "Alice:30 Bob:25 ", f"Got: {result!r}"


# ========== Fixed: For loop body variable processing ==========

def test_for_loop_with_context_vars():
    """Variables from the outer context should be available inside loops."""
    from template_engine import TemplateEngine
    t = TemplateEngine("{% for x in xs %}{{ prefix }}{{ x }}{% endfor %}")
    result = t.render({"xs": [1, 2], "prefix": "X"})
    assert result == "X1X2", f"Got: {result!r}"
