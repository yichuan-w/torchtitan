A developer set up a Git HTTP server on this machine for internal use, but it's broken. Git clone and push operations are failing.

The setup uses nginx with fcgiwrap to serve repositories via git-http-backend. Authentication should use HTTP basic auth with htpasswd. Repositories are stored under `/srv/git/`.

After fixing the issues, the following should work:

- `git clone http://localhost/project-alpha.git` (prompts for username/password)
- Pushing changes to the cloned repository

The nginx site configuration is at `/etc/nginx/sites-enabled/git-http`. The fcgiwrap service needs to be running and accessible to nginx. 

Create an htpasswd file at `/etc/nginx/git-htpasswd` with user `gituser` and password `gitpass`.

The repositories need to support both clone (read) and push (write) operations over HTTP.
