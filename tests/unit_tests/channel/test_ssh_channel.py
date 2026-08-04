# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from jiuwenswarm.gateway.channel_manager.protocol.ssh.config import proxy_config_from_dict
from jiuwenswarm.gateway.channel_manager.protocol.ssh.ssh_connect import SshChannelConfig


def test_ssh_channel_config_from_dict():
    conf = {
        "enabled": True,
        "listen_host": "127.0.0.1",
        "listen_port": 3333,
        "relay_timeout_sec": 120,
    }
    cfg = SshChannelConfig.from_dict(conf)
    assert cfg.enabled is True
    assert cfg.listen_port == 3333
    assert cfg.relay_timeout_sec == 120.0

    proxy = cfg.to_proxy_config()
    assert proxy.listen_host == "127.0.0.1"
    assert proxy.listen_port == 3333


def test_proxy_config_from_dict_sets_default_host_key_path():
    proxy = proxy_config_from_dict({"listen_port": 2222})
    assert proxy.host_key_path.endswith("ssh_host_key")
