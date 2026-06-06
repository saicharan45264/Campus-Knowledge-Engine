from app.core.config import get_settings
settings = get_settings()
print("Port:", settings.POSTGRES_PORT)
