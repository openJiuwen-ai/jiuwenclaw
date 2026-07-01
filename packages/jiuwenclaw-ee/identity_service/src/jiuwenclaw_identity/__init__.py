"""JiuwenClaw 身份/认证服务（独立部署）。

职责：OAuth2 密码流签发 JWT、身份/凭据/组织/成员的权威数据源；
claw_manager 与企业版 web 仅作为资源服务器验签 JWT、读取 claims。
认证后端可二次开发（默认本地口令库，厂商可接自有库/LDAP/OIDC）。
"""

__version__ = "0.1.0"
