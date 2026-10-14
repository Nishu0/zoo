import pytest


@pytest.mark.parametrize("platform,ssh_argument", [("windows", " --ssh-user $env:USERNAME"), ("macos", "")])
def test_node_join_command_uses_the_hosts_ssh_account(client, alice, platform, ssh_argument):
    response = client.post(f"/v1/nodes/tokens?platform={platform}", json={"name": "vm-host"}, headers=alice)
    assert response.status_code == 201, response.text
    made = response.json()
    assert made["join_command"] == f"zoo-node join '{made['token']}' --service{ssh_argument}"
