# Code blocks with language info containing special characters are not properly escaped

When using code blocks with language info that contains special characters, the HTML output doesn't properly escape these characters, which could lead to potential XSS vulnerabilities.

## Code for reproduction

```python
from mistune import create_markdown

md = create_markdown()
html = md('~~~html&"<>\'`\nsome code\n~~~')
print(html)
```

## Actual outcome

The HTML output contains unescaped special characters in the language class attribute:

```html
<pre><code class="language-html&"<>'`">some code</code></pre>
```

## Expected outcome

The special characters in the language info should be properly escaped:

```html
<pre><code class="language-html&amp;&quot;&lt;&gt;&#x27;&#x60;">some code</code></pre>
```

This issue only happens when the language info string contains special characters that need to be escaped in HTML attributes.
