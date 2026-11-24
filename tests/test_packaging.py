"""Tests for RdpNet packaging, installation scripts, and systemd units."""

import os


def test_packaging_assets_exist():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    installer_path = os.path.join(base_dir, "scripts", "install_vps.sh")
    service_path = os.path.join(base_dir, "scripts", "rdpnet.service")
    readme_path = os.path.join(base_dir, "README.md")
    main_server_path = os.path.join(base_dir, "server", "main.py")
    agent_exe_path = os.path.join(base_dir, "client", "rdp-agent.exe")
    
    assert os.path.isfile(installer_path), "install_vps.sh must exist"
    assert os.path.isfile(service_path), "rdpnet.service must exist"
    assert os.path.isfile(readme_path), "README.md must exist"
    assert os.path.isfile(main_server_path), "server/main.py must exist"
    assert os.path.isfile(agent_exe_path), "client/rdp-agent.exe must exist"


def test_systemd_service_syntax():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    service_path = os.path.join(base_dir, "scripts", "rdpnet.service")
    
    with open(service_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content
        assert "ExecStart=/usr/bin/python3 -m server.main" in content
        assert "--relay-port 8443" in content
        assert "--web-port 8080" in content
        assert "EnvironmentFile=-/opt/rdpnet/.env" in content
        assert "Restart=always" in content


def test_installer_script_directives():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    installer_path = os.path.join(base_dir, "scripts", "install_vps.sh")
    
    with open(installer_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "systemctl enable rdpnet" in content
        assert "systemctl restart rdpnet" in content
        assert "ufw allow 8443/tcp" in content
        assert "ufw allow 8080/tcp" in content
        assert "RDPNET_WEB_PASS" in content
        assert "/opt/rdpnet" in content
