NodeVisitor.generic_visit() and visit() methods not working correctly

Description

I've been using the parsimonious library for parsing, and I've encountered issues with the NodeVisitor class. When upgrading to the latest version, I noticed that two methods in the NodeVisitor class are not working as expected.

First, the `generic_visit()` method no longer raises the expected NotImplementedError when a visitor method is not defined for an expression. Instead, it seems to be returning the first visited child or a string 'No visitor method', which is causing silent failures in my code that previously relied on the exception being raised.

Second, the `visit()` method appears to be using an incorrect position parameter. When I try to use the match shortcut method, it's failing to match text correctly. Here's a simple example that reproduces the issue:

```python
from parsimonious.nodes import NodeVisitor
from parsimonious.grammar import Grammar

class MyVisitor(NodeVisitor):
    grammar = Grammar("""
        example = "hello" " " "world"
    """)

# This used to work but now fails
result = MyVisitor().match("hello world")
```

The issue seems to be that the position parameter is being incorrectly incremented, causing the parser to start at the wrong position in the text.

These changes break backward compatibility with code that relied on the previous behavior of these methods. Is there a way to restore the original functionality?

There is also a boundary-condition failure in one-or-more expressions: a required member that can match zero-width must still be attempted at end-of-input. For example, `Grammar('foo = ~"a*"+').parse("")` should succeed without entering an infinite loop.
