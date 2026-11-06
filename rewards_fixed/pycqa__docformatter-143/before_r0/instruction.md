A git repository with the project this issue is about is checked out somewhere under / (commonly /<project-name>). Find it and work there.

No wrapping when there is a paragraph break
example:
```python
def func():
    """My awesome function.

    This line is quite long. In fact is it longer than one hundred and twenty characters so it should be wrapped but it is not.

    It doesn't wrap because of this line and the blank line in between! Delete them and it will wrap.
    """
```

When running docformatter it doesn't wrap, eg:
```
docformatter example.py --wrap-descriptions 120 --wrap-summaries 120
```

docformatter 1.5.0