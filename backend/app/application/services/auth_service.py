import hashlib
import secrets
from typing import Optional
from datetime import datetime
from app.domain.models.user import User, UserRole
from app.domain.repositories.user_repository import UserRepository
from app.application.errors.exceptions import UnauthorizedError, ValidationError, BadRequestError
from app.core.config import get_settings
from app.application.services.token_service import TokenService
from app.domain.models.auth import AuthToken
import logging

logger = logging.getLogger(__name__)


class AuthService:
    """Authentication service handling user authentication and authorization"""
    
    def __init__(self, user_repository: UserRepository, token_service: TokenService):
        self.user_repository = user_repository
        self.settings = get_settings()
        self.token_service = token_service
    
    def _hash_password(self, password: str) -> str:
        """Hash password using PBKDF2-SHA256.

        Stored format: ``pbkdf2:{rounds}:{hash_hex}``
        The global salt (PASSWORD_SALT) is applied but NOT stored in the hash
        string — it is kept in config, identical to the original design.
        """
        salt   = self.settings.password_salt or ''
        rounds = self.settings.password_hash_rounds or 100000
        hash_bytes = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            rounds,
        )
        return f"pbkdf2:{rounds}:{hash_bytes.hex()}"

    def _pbkdf2_sha256(self, password: str, salt: str, rounds: int = None) -> str:
        """Low-level PBKDF2-SHA256 helper (kept for internal use)."""
        if rounds is None:
            rounds = self.settings.password_hash_rounds or 100000
        hash_bytes = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            rounds,
        )
        return f"pbkdf2:{rounds}:{hash_bytes.hex()}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its stored hash.

        Supports two formats:
        - **New** (``pbkdf2:{rounds}:{hash_hex}``): rounds are read from the
          stored string, so the verification is always correct regardless of
          the current PASSWORD_HASH_ROUNDS setting.
        - **Legacy** (``{salt}{hash_hex}``): hash was created before this
          format change, with 10 rounds and the salt prepended literally.
          Verified using 10 rounds for backward-compatibility.
        """
        if not password_hash:
            return False

        import hmac as _hmac
        try:
            salt = self.settings.password_salt or ''

            if password_hash.startswith('pbkdf2:'):
                # New format — extract rounds from stored string
                parts = password_hash.split(':', 2)
                if len(parts) != 3:
                    return False
                rounds    = int(parts[1])
                hash_bytes = hashlib.pbkdf2_hmac(
                    'sha256',
                    password.encode('utf-8'),
                    salt.encode('utf-8'),
                    rounds,
                )
                candidate = f"pbkdf2:{rounds}:{hash_bytes.hex()}"
            else:
                # Legacy format — salt was prepended to the hex digest, 10 rounds
                hash_bytes = hashlib.pbkdf2_hmac(
                    'sha256',
                    password.encode('utf-8'),
                    salt.encode('utf-8'),
                    10,
                )
                candidate = salt + hash_bytes.hex()

            return _hmac.compare_digest(candidate, password_hash)
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False
    
    def _generate_user_id(self) -> str:
        """Generate unique user ID"""
        return secrets.token_urlsafe(16)
    
    async def register_user(self, fullname: str, password: str, email: str, role: UserRole = UserRole.USER) -> User:
        """Register a new user"""
        logger.info(f"Registering user: {email}")

        if self.settings.auth_provider != "password":
            raise BadRequestError("Registration is not allowed")
        
        # Validate input
        if not fullname or len(fullname.strip()) < 2:
            raise ValidationError("Full name must be at least 2 characters long")
        
        if not email or '@' not in email:
            raise ValidationError("Valid email is required")
        
        if not password or len(password) < 6:
            raise ValidationError("Password must be at least 6 characters long")
        
        # Check if email already exists
        if await self.user_repository.email_exists(email):
            raise ValidationError("Email already exists")
        
        # Hash password
        password_hash = self._hash_password(password)
        
        # Create user
        user = User(
            id=self._generate_user_id(),
            fullname=fullname.strip(),
            email=email.lower(),
            password_hash=password_hash,
            role=role,
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # Save to database
        created_user = await self.user_repository.create_user(user)
        
        logger.info(f"User registered successfully: {created_user.id}")
        return created_user
    
    async def authenticate_user(self, email: str, password: str) -> Optional[User]:
        """Authenticate user by email and password"""
        logger.debug(f"Authenticating user: {email}")
        
        # Handle different auth providers
        if self.settings.auth_provider == "none":
            # No authentication required - return a default user
            return User(
                id="anonymous",
                fullname="anonymous",
                email="anonymous@localhost",
                role=UserRole.USER,
                is_active=True
            )
        
        elif self.settings.auth_provider == "local":
            # Local authentication using configured credentials
            if (email == self.settings.local_auth_email and 
                password == self.settings.local_auth_password):
                return User(
                    id="local_admin",
                    fullname="Local Admin",
                    email=email,
                    role=UserRole.ADMIN,
                    is_active=True
                )
            else:
                logger.warning(f"Local authentication failed for user: {email}")
                return None
        
        elif self.settings.auth_provider == "password":
            # Database password authentication
            user = await self.user_repository.get_user_by_email(email)
            if not user:
                logger.warning(f"User not found: {email}")
                return None
            
            if not user.is_active:
                logger.warning(f"User account is inactive: {email}")
                return None
            
            if not user.password_hash:
                logger.warning(f"User has no password hash: {email}")
                return None
            
            # Verify password
            if not self._verify_password(password, user.password_hash):
                logger.warning(f"Invalid password for user: {email}")
                return None
            
            # Update last login
            user.update_last_login()
            await self.user_repository.update_user(user)
            
            logger.info(f"User authenticated successfully: {email}")
            return user
        
        else:
            raise ValueError(f"Unsupported auth provider: {self.settings.auth_provider}")
    
    async def login_with_tokens(self, email: str, password: str) -> AuthToken:
        """Authenticate user and return JWT tokens"""
        user = await self.authenticate_user(email, password)
        
        if not user:
            raise UnauthorizedError("Invalid email or password")
        
        # Generate JWT tokens
        access_token = self.token_service.create_access_token(user)
        refresh_token = self.token_service.create_refresh_token(user)
        
        return AuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user=user
        )
    
    async def refresh_access_token(self, refresh_token: str) -> AuthToken:
        """Refresh access token and rotate the one-time refresh token."""
        payload = await self.token_service.async_verify_token_type(refresh_token, "refresh")
        if not payload:
            raise UnauthorizedError("Invalid or revoked refresh token")

        # Password-provider users are reloaded so deactivated accounts cannot
        # refresh. The local provider intentionally has no database user; bind
        # that token to the configured local credentials instead.
        user_id = payload.get("sub")
        if self.settings.auth_provider == "local":
            if (
                user_id != "local_admin"
                or payload.get("email") != self.settings.local_auth_email
                or payload.get("is_active") is not True
            ):
                raise UnauthorizedError("Invalid local refresh token")
            user = User(
                id="local_admin",
                fullname="Local Admin",
                email=self.settings.local_auth_email,
                role=UserRole.ADMIN,
                is_active=True,
            )
        elif self.settings.auth_provider == "none":
            user = User(
                id="anonymous",
                fullname="anonymous",
                email="anonymous@localhost",
                role=UserRole.USER,
                is_active=True,
            )
        else:
            user = await self.user_repository.get_user_by_id(user_id)
            if not user or not user.is_active:
                raise UnauthorizedError("User not found or inactive")

        # Refresh tokens are one-time capabilities. Revoke before issuing the
        # replacement; a Redis failure therefore fails closed.
        if not await self.token_service.async_revoke_token(refresh_token):
            raise UnauthorizedError("Refresh token rotation unavailable")

        new_access_token = self.token_service.create_access_token(user)
        new_refresh_token = self.token_service.create_refresh_token(user)

        return AuthToken(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
        )

    
    async def verify_token(self, token: str) -> Optional[User]:
        """Verify JWT access token and return user.
        
        Enforces token type == 'access' and checks the Redis revocation
        blacklist so that logged-out tokens are immediately rejected.
        """
        payload = await self.token_service.async_verify_access_token(token)
        if not payload:
            return None
        user_info = {
            "id": payload.get("sub"),
            "fullname": payload.get("fullname"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "is_active": payload.get("is_active", True),
        }

        if not user_info["id"]:
            return None
        
        # For database users, verify user still exists and is active
        if self.settings.auth_provider == "password":
            user = await self.user_repository.get_user_by_id(user_info["id"])
            if not user or not user.is_active:
                return None
            return user
        
        # For local/none authentication, create user from token info
        return User(
            id=user_info["id"],
            fullname=user_info["fullname"],
            email=user_info.get("email"),
            role=UserRole(user_info.get("role", "user")),
            is_active=user_info.get("is_active", True)
        )
    
    async def logout(self, token: str) -> bool:
        """Logout user by revoking token — adds it to the Redis blacklist."""
        if self.settings.auth_provider == "none":
            raise BadRequestError("Logout is not allowed")
        return await self.token_service.async_revoke_token(token)
    
    async def change_password(self, user_id: str, old_password: str, new_password: str) -> bool:
        """Change user password"""
        logger.info(f"Changing password for user: {user_id}")
        
        # Get user
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            raise ValidationError("User not found")
        
        if not user.is_active:
            raise UnauthorizedError("User account is inactive")
        
        # Verify old password
        if not user.password_hash or not self._verify_password(old_password, user.password_hash):
            raise UnauthorizedError("Invalid old password")
        
        # Validate new password
        if not new_password or len(new_password) < 6:
            raise ValidationError("New password must be at least 6 characters long")
        
        # Hash new password
        new_password_hash = self._hash_password(new_password)
        
        # Update user password
        user.password_hash = new_password_hash
        user.updated_at = datetime.utcnow()
        
        await self.user_repository.update_user(user)
        
        logger.info(f"Password changed successfully for user: {user_id}")
        return True
    
    async def change_fullname(self, user_id: str, new_fullname: str) -> User:
        """Change user fullname"""
        logger.info(f"Changing fullname for user: {user_id}")
        
        # Get user
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            raise ValidationError("User not found")
        
        if not user.is_active:
            raise UnauthorizedError("User account is inactive")
        
        # Validate new fullname
        if not new_fullname or len(new_fullname.strip()) < 2:
            raise ValidationError("Full name must be at least 2 characters long")
        
        # Update user fullname
        user.fullname = new_fullname.strip()
        user.updated_at = datetime.utcnow()
        
        updated_user = await self.user_repository.update_user(user)
        
        logger.info(f"Fullname changed successfully for user: {user_id}")
        return updated_user
    
    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID"""
        return await self.user_repository.get_user_by_id(user_id)
    
    async def deactivate_user(self, user_id: str) -> bool:
        """Deactivate user account"""
        logger.info(f"Deactivating user: {user_id}")
        
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            raise ValidationError("User not found")
        
        user.deactivate()
        await self.user_repository.update_user(user)
        
        logger.info(f"User deactivated successfully: {user_id}")
        return True
    
    async def activate_user(self, user_id: str) -> bool:
        """Activate user account"""
        logger.info(f"Activating user: {user_id}")
        
        user = await self.user_repository.get_user_by_id(user_id)
        if not user:
            raise ValidationError("User not found")
        
        user.activate()
        await self.user_repository.update_user(user)
        
        logger.info(f"User activated successfully: {user_id}")
        return True
    
    async def reset_password(self, email: str, new_password: str) -> bool:
        """Reset user password with email"""
        logger.info(f"Resetting password for user: {email}")
        
        if self.settings.auth_provider != "password":
            raise BadRequestError("Password reset is not allowed")
        
        # Get user by email
        user = await self.user_repository.get_user_by_email(email)
        if not user:
            raise ValidationError("User not found")
        
        if not user.is_active:
            raise UnauthorizedError("User account is inactive")
        
        # Validate new password
        if not new_password or len(new_password) < 6:
            raise ValidationError("New password must be at least 6 characters long")
        
        # Hash new password
        new_password_hash = self._hash_password(new_password)
        
        # Update user password
        user.password_hash = new_password_hash
        user.updated_at = datetime.utcnow()
        
        await self.user_repository.update_user(user)
        
        logger.info(f"Password reset successfully for user: {email}")
        return True 