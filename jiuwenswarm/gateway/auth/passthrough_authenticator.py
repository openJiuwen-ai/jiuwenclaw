from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthResult,
    CredentialAuthenticator,
    AuthContext,
)

class PassthroughAuthenticator(CredentialAuthenticator):

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """默认放行：不调用第三方服务，返回匿名身份。"""
        return AuthResult(success=True, user_id="anonymous")