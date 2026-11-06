A production server runs a multi-service web application stack managed by supervisord. The stack consists of:

- PostgreSQL database (`appdb`)
- Redis cache (`appcache`)  
- Application server (`appserver`)
- Background worker (`appworker`)
- Nginx reverse proxy (`appproxy`)

The supervisord configuration is at `/etc/supervisor/conf.d/`. Currently, services are failing to start properly - the application crashes on startup because it cannot connect to the database and cache, the background worker fails to start, and Nginx returns errors because the application isn't ready when it tries to proxy requests.

The application requires a database table to be created before it can start. A migration script is provided at `/opt/webapp/init_db.py`.

Fix the supervisord configuration so that all five services start successfully in the correct order:
1. PostgreSQL and Redis start first (can be parallel)
2. Database initialization runs after PostgreSQL is ready
3. Application server and background worker start after the database is initialized and cache is ready
4. Nginx starts last, proxying to the application server on port 5000

After fixing, restart supervisord and ensure the application is accessible on port 80.
