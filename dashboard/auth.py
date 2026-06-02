import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dashboard.database import SessionLocal, Usuario, pwd_context

security = HTTPBasic()

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.username == credentials.username).first()
        if not user or not pwd_context.verify(credentials.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Basic"},
            )
        return user
    finally:
        db.close()
