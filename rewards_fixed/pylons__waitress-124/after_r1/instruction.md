A git repository with the project this issue is about is checked out somewhere under / (commonly /<project-name>). Find it and work there.

HTTP Response Splitting - Status/Header Names
#117 was about HTTP response splitting in header values. Should this also cover the status line and the header names? The same thing can happen with those.

All of these examples are on waitress 0.9.0b0:
```
$ pip freeze | grep waitress
waitress==0.9.0b0
```

Here's an app that uses a bad status line:
```python
def app(environ, start_response):
	start_response("200 Evil\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", [])
	return [b"This should be the body!"]
```

And the output of connecting to it:
```
$ http 127.0.0.1:8080
HTTP/1.1 200 Evil
Connection: close
Content-Length: 0


$
```

And here's one that uses a bad header name:
```python
def app(environ, start_response):
	start_response("200 BadName",
				   [("\r\n", "Content-length: 0")])
	return [b"This should be the body!"]
```

And its output, which hangs the client until a timeout, leaving a dangling socket:
```
$ http 127.0.0.1:8080
HTTP/1.1 200 BadName


http: error: ConnectionError: HTTPConnectionPool(host='127.0.0.1', port=8080): Read timed out.
```

I realize that HTTP status messages and header names are *probably* less likely to allow user input than header values, but if they do, bad things can still happen.

Please make Waitress reject status strings and response header names that do not follow HTTP syntax, while continuing to accept valid ones.

## Discussion

The issue thread, as it stood when this was reported.


**digitalresistor** wrote:

I used `mod_wsgi` as a source of inspiration for where to add the check, but additional checks can and probably should be added. Thanks for this report, I will work on getting these fixed.
