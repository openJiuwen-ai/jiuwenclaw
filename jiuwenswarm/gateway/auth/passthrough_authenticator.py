from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthResult,
    CredentialAuthenticator,
    AuthContext,
)

class PassthroughAuthenticator(CredentialAuthenticator):

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """认证直接通过，返回默认用户; 是否需要做基础验证？"""
        return AuthResult(success=True, user_id="anonymous")