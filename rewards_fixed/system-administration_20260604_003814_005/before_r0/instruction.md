The APT repository at `/var/www/repo` isn't working — `apt-get update` fails and packages can't be installed. nginx is already configured to serve `/var/www/repo` on port 80 but isn't started. Additional `.deb` packages are waiting in `/app/packages/`.

Diagnose what's wrong with the repository and fix it. When you're done:
- `apt-get update` must succeed without warnings or errors
- `hello-deb`, `useful-util`, and `hello-app` must all be installable via apt

