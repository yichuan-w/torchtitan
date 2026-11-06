import os
import subprocess
import tempfile
import time


def run_cmd(cmd, cwd=None, check=True, capture=True, env=None):
    """Run a shell command and return the result."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        check=check,
        capture_output=capture,
        text=True,
        env=merged_env,
    )
    return result


def wait_for_service(host, port, timeout=30):
    """Wait for a service to be available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = run_cmd(f"curl -s -o /dev/null -w '%{{http_code}}' http://{host}:{port}/", check=False)
            if result.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def test_fcgiwrap_socket_running():
    """Test that fcgiwrap is running and nginx can communicate with it."""
    import stat
    
    # Check if fcgiwrap process is running
    result = run_cmd("pgrep -x fcgiwrap", check=False)
    assert result.returncode == 0, "fcgiwrap process is not running"
    
    # Check if there's a socket file that nginx can use, or fcgiwrap is running via stdin
    # It could be at /run/fcgiwrap.socket, /var/run/fcgiwrap.socket, or no socket (stdin mode)
    socket_found = False
    for socket_path in ["/run/fcgiwrap.socket", "/var/run/fcgiwrap.socket"]:
        if os.path.exists(socket_path):
            mode = os.stat(socket_path).st_mode
            # Socket should be readable and writable by www-data
            if (mode & stat.S_IRGRP) or (mode & stat.S_IROTH):
                if (mode & stat.S_IWGRP) or (mode & stat.S_IWOTH):
                    socket_found = True
                    break
    
    # If no socket file found, check nginx config to see how it connects to fcgiwrap
    # It might be using a different socket path or might be spawning fcgiwrap directly
    if not socket_found:
        config_path = "/etc/nginx/sites-enabled/git-http"
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = f.read()
            # If nginx config has fastcgi_pass, it must be working
            if "fastcgi_pass" in config:
                # Test that the connection actually works by checking nginx can respond
                result = run_cmd(
                    "curl -s -o /dev/null -w '%{http_code}' -u gituser:gitpass http://localhost/project-alpha.git/info/refs?service=git-upload-pack",
                    check=False,
                )
                if result.returncode == 0 and "200" in result.stdout:
                    socket_found = True  # The connection works even if socket not at expected path
    
    assert socket_found, "No valid fcgiwrap socket found and nginx cannot communicate with fcgiwrap"


def test_nginx_running():
    """Test that nginx is running and responding."""
    result = run_cmd("pgrep nginx", check=False)
    assert result.returncode == 0, "nginx is not running"
    
    # Test nginx is listening on port 80
    result = run_cmd("curl -s -o /dev/null -w '%{http_code}' http://localhost/", check=False)
    # Could be 200, 403, 404, or similar - just needs to respond
    assert result.returncode == 0, "nginx is not responding on port 80"


def test_htpasswd_exists():
    """Test that htpasswd file exists with valid format."""
    htpasswd_path = "/etc/nginx/git-htpasswd"
    assert os.path.exists(htpasswd_path), f"htpasswd file does not exist at {htpasswd_path}"
    
    # Check file has at least one user entry
    with open(htpasswd_path, "r") as f:
        content = f.read().strip()
    assert content, "htpasswd file is empty"
    assert ":" in content, "htpasswd file does not contain valid user entries"


def get_working_credentials():
    """Try to find working credentials from htpasswd file."""
    htpasswd_path = "/etc/nginx/git-htpasswd"
    if not os.path.exists(htpasswd_path):
        return None
    
    with open(htpasswd_path, "r") as f:
        first_line = f.readline().strip()
    
    if ":" not in first_line:
        return None
    
    username = first_line.split(":")[0]
    
    # Check if this is the expected user with expected password
    expected_user = "gituser"
    expected_password = "gitpass"
    
    if username == expected_user:
        # Try the expected password
        result = run_cmd(
            f"curl -s -o /dev/null -w '%{{http_code}}' -u {expected_user}:{expected_password} http://localhost/project-alpha.git/info/refs?service=git-upload-pack",
            check=False,
        )
        if result.returncode == 0 and "200" in result.stdout:
            return (expected_user, expected_password)
    
    # Fallback: try common passwords for any user
    for pwd in [expected_password, "password", "test123", "gitpass", "admin", "git", "secret", "test"]:
        result = run_cmd(
            f"curl -s -o /dev/null -w '%{{http_code}}' -u {username}:{pwd} http://localhost/project-alpha.git/info/refs?service=git-upload-pack",
            check=False,
        )
        if result.returncode == 0 and "200" in result.stdout:
            return (username, pwd)
    
    return None


def test_git_clone_works():
    """Test that git clone works with authentication."""
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = os.path.join(tmpdir, "project-alpha")
        
        # Get working credentials
        creds = get_working_credentials()
        assert creds is not None, "Could not find working credentials for authentication"
        
        username, password = creds
        
        # Clone with credentials
        result = run_cmd(
            f"git clone http://{username}:{password}@localhost/project-alpha.git {clone_dir}",
            check=False,
            cwd=tmpdir,
        )
        
        assert result.returncode == 0, f"git clone failed: {result.stderr}"
        assert os.path.exists(os.path.join(clone_dir, "README.md")), "Cloned repository missing README.md"


def test_git_push_works():
    """Test that git push works with authentication."""
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = os.path.join(tmpdir, "project-alpha")
        
        # Get working credentials
        creds = get_working_credentials()
        assert creds is not None, "Could not find working credentials for authentication"
        
        username, password = creds
        
        # Clone the repo
        result = run_cmd(
            f"git clone http://{username}:{password}@localhost/project-alpha.git {clone_dir}",
            check=False,
            cwd=tmpdir,
        )
        assert result.returncode == 0, f"git clone failed for push test: {result.stderr}"
        
        # Configure git user for commit
        run_cmd("git config user.email 'test@example.com'", cwd=clone_dir, check=False)
        run_cmd("git config user.name 'Test User'", cwd=clone_dir, check=False)
        
        # Make a change
        test_file = os.path.join(clone_dir, "test_push.txt")
        with open(test_file, "w") as f:
            f.write(f"Push test content {time.time()}")
        
        run_cmd("git add test_push.txt", cwd=clone_dir)
        run_cmd('git commit -m "Test push commit"', cwd=clone_dir)
        
        # Push
        result = run_cmd(
            f"git push http://{username}:{password}@localhost/project-alpha.git HEAD:master",
            check=False,
            cwd=clone_dir,
        )
        
        assert result.returncode == 0, f"git push failed: {result.stderr}"


def test_repository_receivepack_enabled():
    """Test that the repository has http.receivepack enabled."""
    result = run_cmd("git config -f /srv/git/project-alpha.git/config http.receivepack", check=False)
    assert result.returncode == 0, "Could not read git config"
    receivepack = result.stdout.strip().lower()
    assert receivepack == "true", f"http.receivepack is not set to true (got: {receivepack})"


def test_nginx_config_has_auth():
    """Test that nginx config includes authentication."""
    config_path = "/etc/nginx/sites-enabled/git-http"
    assert os.path.exists(config_path), f"nginx config does not exist at {config_path}"
    
    with open(config_path, "r") as f:
        config = f.read()
    
    # Check for auth_basic directive
    assert "auth_basic" in config, "nginx config does not have auth_basic directive"
    assert "auth_basic_user_file" in config, "nginx config does not have auth_basic_user_file directive"


def test_nginx_config_has_correct_fastcgi_params():
    """Test that nginx config has required FastCGI parameters for git-http-backend."""
    config_path = "/etc/nginx/sites-enabled/git-http"
    
    with open(config_path, "r") as f:
        config = f.read()
    
    # Check for critical FastCGI parameters
    assert "GIT_PROJECT_ROOT" in config, "nginx config missing GIT_PROJECT_ROOT"
    assert "GIT_HTTP_EXPORT_ALL" in config or "GIT_HTTP_EXPORT" in config, \
        "nginx config missing GIT_HTTP_EXPORT_ALL or similar export setting"
    assert "REMOTE_USER" in config, "nginx config missing REMOTE_USER for push authentication"


def test_repository_permissions():
    """Test that repository has correct permissions for git operations."""
    repo_path = "/srv/git/project-alpha.git"
    assert os.path.exists(repo_path), f"Repository does not exist at {repo_path}"
    
    # Check that objects directory exists
    objects_path = os.path.join(repo_path, "objects")
    assert os.path.exists(objects_path), "objects directory does not exist"
    
    # Check that the repository allows write access for git push
    # This requires www-data (or git group member) to have write access
    import stat
    import pwd
    
    objects_stat = os.stat(objects_path)
    mode = objects_stat.st_mode
    
    # Must be a directory
    assert stat.S_ISDIR(mode), "objects should be a directory"
    
    # Check if write is allowed - at least one of these must be true:
    # - Owner is www-data with user write
    # - Group is www-data/git with group write
    # - Others have write
    www_data_uid = pwd.getpwnam("www-data").pw_uid
    www_data_gid = pwd.getpwnam("www-data").pw_gid
    
    can_write = False
    if objects_stat.st_uid == www_data_uid and (mode & stat.S_IWUSR):
        can_write = True
    elif (mode & stat.S_IWGRP):
        # Check if group allows write and www-data is in it
        if objects_stat.st_gid == www_data_gid:
            can_write = True
        else:
            # Check if it's the git group and www-data is a member
            try:
                import grp
                group_name = grp.getgrgid(objects_stat.st_gid).gr_name
                if group_name == "git":
                    # Check if www-data is in git group
                    www_data_groups = os.getgrouplist("www-data", www_data_gid)
                    if objects_stat.st_gid in www_data_groups:
                        can_write = True
            except KeyError:
                pass
    elif mode & stat.S_IWOTH:
        can_write = True
    
    assert can_write, "Repository objects directory is not writable by www-data (needed for push)"
